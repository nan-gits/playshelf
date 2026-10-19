"""Import from a download link: Playshelf downloads the archive itself.

A download runs in a background thread and writes into `DATA_DIR/imports/<token>.download`.
Once it is complete it goes through the same import as an uploaded archive
(`imports.receive`) and appears under pending imports.

Downloads are kept in memory only. A restart drops running ones; their partial files
are removed by `imports.cleanup` at the next start.

Only direct links to a file work reliably. A few file hosters are handled:
Pixeldrain, Dropbox and Google Drive links are rewritten to their download address.
Hosters that need a browser, an account or decrypt in the browser (MEGA, Gofile,
Patreon) are refused with a clear message.

By default only public internet addresses are contacted, so the link import cannot
be used to reach devices in the local network (URL_IMPORT_PRIVATE_NETWORKS=true
allows them). The check is made on the address actually connected to, so a host
name that resolves differently on the second lookup does not get around it.
"""

import http.client
import ipaddress
import logging
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from app import importer, imports
from app.config import Settings
from app.db import Database
from app.importer import ImportRejected, format_size
from app.updates import USER_AGENT

log = logging.getLogger(__name__)

MB = 1024 * 1024
CHUNK_SIZE = 1024 * 1024
TIMEOUT = 60  # seconds without data before a download is given up
MAX_PARALLEL = 2  # downloads running at the same time; more wait in the queue
MAX_ACTIVE_PER_USER = 10
KEEP_FINISHED_SECONDS = 24 * 3600
MAX_URL_LENGTH = 2000

QUEUED = "queued"
DOWNLOADING = "downloading"
EXTRACTING = "extracting"
DONE = "done"
FAILED = "failed"
ACTIVE = (QUEUED, DOWNLOADING, EXTRACTING)


class DownloadRejected(Exception):
    """The link cannot be downloaded; the text is shown in the UI."""


class Cancelled(Exception):
    """The download was cancelled by the user or because the app stops."""


# Links: check, and rewrite file hoster pages to their download address

UPLOAD_YOURSELF = " Download the file in your browser and import it with Import game."

# Hosters that cannot work without a browser or an account
REFUSED_HOSTS = {
    "mega.nz": "MEGA encrypts its files and decrypts them only in the browser, so Playshelf"
    " cannot download them.",
    "mega.co.nz": "MEGA encrypts its files and decrypts them only in the browser, so"
    " Playshelf cannot download them.",
    "mega.io": "MEGA encrypts its files and decrypts them only in the browser, so Playshelf"
    " cannot download them.",
    "gofile.io": "Gofile hands out files only to a browser session, so Playshelf cannot"
    " download them.",
    "patreon.com": "Patreon downloads need your Patreon login, which Playshelf does not have.",
}


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith(f".{domain}")


def prepare_url(url: str) -> str:
    """Checks a link and returns the address to download. Raises `DownloadRejected`."""
    url = url.strip()
    if not url:
        raise DownloadRejected("Please enter a download link.")
    if len(url) > MAX_URL_LENGTH:
        raise DownloadRejected("The link is too long.")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise DownloadRejected("Only http:// and https:// links can be downloaded.")
    if parts.username or parts.password:
        raise DownloadRejected("Links with a user name or password are not supported.")
    host = parts.hostname.lower()
    for domain, reason in REFUSED_HOSTS.items():
        if _host_matches(host, domain):
            raise DownloadRejected(reason + UPLOAD_YOURSELF)

    # Pixeldrain: pixeldrain.com/u/<id> is a page, /api/file/<id>?download the file
    if _host_matches(host, "pixeldrain.com"):
        path = PurePosixPath(parts.path)
        if len(path.parts) == 3 and path.parts[1] == "u":
            return f"https://pixeldrain.com/api/file/{path.parts[2]}?download"
        if len(path.parts) >= 2 and path.parts[1] == "l":
            raise DownloadRejected(
                "This is a Pixeldrain list. Open it and copy the link of the single file."
            )
    # Dropbox: dl=0 shows a page, dl=1 sends the file
    if _host_matches(host, "dropbox.com"):
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "dl"]
        query.append(("dl", "1"))
        return urlunsplit(parts._replace(query=urlencode(query)))
    # Google Drive: /file/d/<id>/view is a page
    if host == "drive.google.com":
        match = re.match(r"/file/d/([\w-]+)", parts.path)
        file_id = match.group(1) if match else dict(parse_qsl(parts.query)).get("id")
        if file_id:
            return "https://drive.usercontent.google.com/download?" + urlencode(
                {"id": file_id, "export": "download", "confirm": "t"}
            )
    return url


# Connections only to allowed addresses


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


def _private_network(host: str) -> DownloadRejected:
    return DownloadRejected(
        f"{host} is an address in a local or private network. Playshelf only downloads"
        " from the internet (URL_IMPORT_PRIVATE_NETWORKS=true allows local addresses)."
    )


def _public_connection(
    address: tuple[str, int],
    timeout: object = socket._GLOBAL_DEFAULT_TIMEOUT,  # type: ignore[attr-defined]
    source_address: tuple[str, int] | None = None,
    **_: Any,
) -> socket.socket:
    """Like `socket.create_connection`, but only to public addresses."""
    host, port = address
    error: OSError | None = None
    for family, kind, proto, _name, sockaddr in socket.getaddrinfo(
        host, port, type=socket.SOCK_STREAM
    ):
        if not _is_public(str(sockaddr[0])):
            continue
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:  # type: ignore[attr-defined]
                sock.settimeout(timeout)  # type: ignore[arg-type]
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as exc:
            sock.close()
            error = exc
    if error:
        raise error
    raise _private_network(host)


class _PublicHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._create_connection = _public_connection


def _via_proxy(request: urllib.request.Request) -> bool:
    # set_proxy() sends the full URL to an HTTP proxy and tunnels HTTPS through it
    return bool(getattr(request, "_tunnel_host", None)) or request.selector == request.full_url


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        if _via_proxy(req):  # the proxy connects; the host name was checked before
            return super().http_open(req)
        return self.do_open(_PublicHTTPConnection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        if _via_proxy(req):
            return super().https_open(req)
        return self.do_open(_PublicHTTPSConnection, req, context=self._context)


class _RedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follows redirects only to http and https, and checks each new address."""

    def __init__(self, private_networks: bool) -> None:
        self.private_networks = private_networks

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        parts = urlsplit(newurl)
        if parts.scheme.lower() not in ("http", "https"):
            raise DownloadRejected("The link redirects to an address that is not http or https.")
        if not self.private_networks:
            check_host(parts.hostname or "")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_host(host: str) -> None:
    """Refuses host names that resolve to a local or private address."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise DownloadRejected(f"The server {host} was not found.") from exc
    if not all(_is_public(str(info[4][0])) for info in infos):
        raise _private_network(host)


def build_opener(private_networks: bool) -> urllib.request.OpenerDirector:
    if private_networks:
        return urllib.request.build_opener(_RedirectHandler(True))
    return urllib.request.build_opener(
        _PublicHTTPHandler, _PublicHTTPSHandler, _RedirectHandler(False)
    )


# File name and kind of the downloaded file

# Start of the file -> archive ending, for names that have none
MAGIC = [
    (0, b"PK\x03\x04", ".zip"),
    (0, b"PK\x05\x06", ".zip"),
    (0, b"Rar!\x1a\x07", ".rar"),
    (0, b"7z\xbc\xaf\x27\x1c", ".7z"),
    (0, b"\x1f\x8b", ".tar.gz"),
    (0, b"BZh", ".tar.bz2"),
    (0, b"\xfd7zXZ\x00", ".tar.xz"),
    (257, b"ustar", ".tar"),
]


def sniff(head: bytes) -> str | None:
    """Archive ending matching the first bytes of a file, if known."""
    return next(
        (suffix for offset, magic, suffix in MAGIC if head[offset : offset + len(magic)] == magic),
        None,
    )


def looks_like_page(head: bytes) -> bool:
    start = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    return start.startswith((b"<!doctype", b"<html", b"<head", b"<?xml", b"<!--", b"{"))


def clean_filename(name: str) -> str:
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if ch.isprintable()).strip(" .")
    return name[:200]


def filename_for(response: http.client.HTTPResponse, url: str) -> str:
    """The file name from Content-Disposition, otherwise from the address."""
    name = response.headers.get_filename() or ""
    if not name:
        name = unquote(PurePosixPath(urlsplit(url).path).name)
    return clean_filename(name) or "download"


def with_suffix(name: str, head: bytes) -> str:
    """Adds an archive ending that matches the content if the name has none."""
    if importer.archive_suffix(name):
        return name
    suffix = sniff(head)
    if suffix is None or suffix not in importer.supported_suffixes():
        raise DownloadRejected(
            "The link did not deliver an archive this server can import"
            f" ({', '.join(importer.supported_suffixes())})."
        )
    return name + suffix


def _http_message(code: int) -> str:
    if code == 404:
        return "The file was not found (404). Is the link still valid?"
    if code == 410:
        return "The file is no longer available (410)."
    if code in (401, 403):
        return (
            f"Access was denied ({code}). The link may need a login, may have expired, or"
            " the site may block downloads outside the browser." + UPLOAD_YOURSELF
        )
    if code == 429:
        return "Too many requests (429). Please try again later."
    return f"The server answered with error {code}."


# Jobs


@dataclass
class Job:
    id: str
    user_id: int
    url: str
    filename: str
    state: str = QUEUED
    received: int = 0
    total: int | None = None
    error: str | None = None
    import_id: int | None = None
    finished: float | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "filename": self.filename,
            "state": self.state,
            "received": self.received,
            "total": self.total,
            "error": self.error,
            "import_id": self.import_id,
        }


class Downloads:
    """Download jobs of all accounts, run by a small thread pool."""

    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(MAX_PARALLEL, thread_name_prefix="download")

    def start(self, url: str, user_id: int) -> Job:
        """Checks the link and queues the download. Raises `DownloadRejected`."""
        target = prepare_url(url)
        if not self.settings.url_import_private_networks:
            check_host(urlsplit(target).hostname or "")
        name = clean_filename(unquote(PurePosixPath(urlsplit(target).path).name)) or "download"
        with self.lock:
            self._prune()
            mine = [j for j in self.jobs.values() if j.user_id == user_id]
            active = sum(1 for j in mine if j.state in ACTIVE)
            if active >= MAX_ACTIVE_PER_USER:
                raise DownloadRejected(
                    f"There are already {active} downloads running or waiting. Please wait"
                    " until some have finished."
                )
            job = Job(imports.new_token(), user_id, target, name)
            self.jobs[job.id] = job
        self.pool.submit(self._run, job)
        return job

    def list(self, user_id: int) -> list[Job]:
        with self.lock:
            self._prune()
            return [j for j in self.jobs.values() if j.user_id == user_id]

    def get(self, job_id: str, user_id: int) -> Job | None:
        job = self.jobs.get(job_id)
        return job if job and job.user_id == user_id else None

    def remove(self, job: Job) -> None:
        """Cancels a waiting or running download, or dismisses a finished one.
        Raises `RuntimeError` while the archive is being extracted."""
        with self.lock:
            if job.state == EXTRACTING:
                raise RuntimeError("The archive is being extracted and can no longer be stopped.")
            job.cancel.set()
            self.jobs.pop(job.id, None)

    def shutdown(self) -> None:
        with self.lock:
            for job in self.jobs.values():
                job.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _prune(self) -> None:
        limit = time.monotonic() - KEEP_FINISHED_SECONDS
        for job_id in [j.id for j in self.jobs.values() if j.finished and j.finished < limit]:
            del self.jobs[job_id]

    def _finish(self, job: Job, state: str, error: str | None = None) -> None:
        job.state = state
        job.error = error
        job.finished = time.monotonic()

    def _run(self, job: Job) -> None:
        if job.cancel.is_set():
            return
        self.settings.imports_dir.mkdir(parents=True, exist_ok=True)
        path = self.settings.imports_dir / f"{job.id}.download"
        try:
            job.state = DOWNLOADING
            filename = self._download(job, path)
            with self.lock:
                if job.cancel.is_set():
                    raise Cancelled
                job.state = EXTRACTING
            imp = imports.receive(self.settings, self.db, path, filename, job.user_id)
            job.import_id = imp["id"]
            self._finish(job, DONE)
            log.info("Link import: %s is waiting as import %s", filename, imp["id"])
        except Cancelled:
            log.info("Link import: download of %s cancelled", job.url)
        except DownloadRejected as exc:
            self._finish(job, FAILED, str(exc))
        except ImportRejected as exc:
            self._finish(job, FAILED, f'"{job.filename}" was not imported: {exc}')
        except Exception as exc:  # for example a full disk
            log.exception("Link import: %s could not be downloaded", job.url)
            self._finish(job, FAILED, f"Unexpected error: {exc}")
        finally:
            path.unlink(missing_ok=True)

    def _download(self, job: Job, path: Path) -> str:
        """Downloads into `path`; returns the file name to import it under."""
        limit = self.settings.max_upload_mb * MB
        too_big = DownloadRejected(
            f"The file is too large. The maximum is {format_size(limit)} (MAX_UPLOAD_MB)."
        )
        opener = build_opener(self.settings.url_import_private_networks)
        request = urllib.request.Request(job.url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                length = response.headers.get("Content-Length", "")
                if length.isdigit():
                    job.total = int(length)
                    if job.total > limit:
                        raise too_big
                job.filename = filename_for(response, response.geturl())
                head = b""
                with path.open("wb") as file:
                    while chunk := response.read(CHUNK_SIZE):
                        if job.cancel.is_set():
                            raise Cancelled
                        if not head:
                            head = chunk[:512]
                            if looks_like_page(head) and not sniff(head):
                                raise DownloadRejected(
                                    "The link leads to a web page, not to a file. Use the"
                                    ' direct download link (for example "Copy link address"'
                                    " on the download button)." + UPLOAD_YOURSELF
                                )
                        job.received += len(chunk)
                        if job.received > limit:
                            raise too_big
                        file.write(chunk)
        except urllib.error.HTTPError as exc:
            raise DownloadRejected(_http_message(exc.code)) from exc
        except urllib.error.URLError as exc:
            raise DownloadRejected(f"The server cannot be reached: {exc.reason}") from exc
        except http.client.IncompleteRead as exc:
            raise DownloadRejected(
                "The server ended the download early. Please try again."
            ) from exc
        except (TimeoutError, ConnectionError) as exc:
            raise DownloadRejected(
                f"The download stopped: the server sent no data for {TIMEOUT} seconds or"
                " closed the connection."
            ) from exc
        if not job.received:
            raise DownloadRejected("The server sent an empty file.")
        if job.total and job.received < job.total:
            raise DownloadRejected("The server ended the download early. Please try again.")
        job.filename = with_suffix(job.filename, head)
        return job.filename
