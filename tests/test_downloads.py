import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app import downloads
from app.config import Settings
from app.main import create_app
from tests.test_inbox import GAME, zip_bytes

# path -> (status, headers, body)
ROUTES: dict[str, tuple[int, dict[str, str], bytes]] = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        status, headers, body = ROUTES.get(self.path, (404, {}, b"not found"))
        self.send_response(status)
        for name, value in {"Content-Length": str(len(body)), **headers}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture(autouse=True)
def no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The test server is local; a proxy from the environment must not be used."""
    for name in ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)


@pytest.fixture
def server() -> Iterator[str]:
    ROUTES.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture
def local_client(settings: Settings) -> Iterator[TestClient]:
    settings.url_import_private_networks = True
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def wait(client: TestClient, job_id: str) -> dict:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = next(j for j in client.get("/api/downloads").json() if j["id"] == job_id)
        if job["state"] not in downloads.ACTIVE:
            return job
        time.sleep(0.05)
    raise AssertionError("download did not finish")


def start(client: TestClient, url: str) -> dict:
    response = client.post("/api/downloads", json={"url": url})
    assert response.status_code == 202, response.text
    return wait(client, response.json()["id"])


def test_downloads_and_imports_archive(local_client: TestClient, server: str) -> None:
    ROUTES["/get?id=1"] = (
        200,
        {"Content-Disposition": 'attachment; filename="New Game 0.2.zip"'},
        GAME,
    )
    job = start(local_client, f"{server}/get?id=1")
    assert job["state"] == downloads.DONE, job
    assert job["filename"] == "New Game 0.2.zip"
    assert job["received"] == job["total"] == len(GAME)
    pending = local_client.get("/api/imports").json()
    assert [(p["id"], p["filename"]) for p in pending] == [(job["import_id"], "New Game 0.2")]


def test_name_without_ending_gets_one_from_content(local_client: TestClient, server: str) -> None:
    ROUTES["/files/abc123"] = (200, {}, GAME)
    job = start(local_client, f"{server}/files/abc123")
    assert job["state"] == downloads.DONE, job
    assert job["filename"] == "abc123.zip"


def test_follows_redirects(local_client: TestClient, server: str) -> None:
    ROUTES["/go"] = (302, {"Location": "/real/Game.zip"}, b"")
    ROUTES["/real/Game.zip"] = (200, {}, GAME)
    job = start(local_client, f"{server}/go")
    assert job["state"] == downloads.DONE, job
    assert job["filename"] == "Game.zip"


def test_web_page_is_refused(local_client: TestClient, server: str) -> None:
    ROUTES["/download/Game.zip"] = (
        200,
        {"Content-Type": "text/html"},
        b"<!DOCTYPE html><html><body>Click here</body></html>",
    )
    job = start(local_client, f"{server}/download/Game.zip")
    assert job["state"] == downloads.FAILED
    assert "web page" in job["error"]
    assert local_client.get("/api/imports").json() == []


def test_http_error_is_explained(local_client: TestClient, server: str) -> None:
    job = start(local_client, f"{server}/missing.zip")
    assert job["state"] == downloads.FAILED
    assert "404" in job["error"]


def test_too_large_is_refused_before_download(
    local_client: TestClient, settings: Settings, server: str
) -> None:
    settings.max_upload_mb = 1
    ROUTES["/big.zip"] = (200, {}, b"PK\x03\x04" + b"0" * (2 * 1024 * 1024))
    job = start(local_client, f"{server}/big.zip")
    assert job["state"] == downloads.FAILED
    assert "too large" in job["error"]
    assert job["received"] == 0


def test_rejected_archive_reports_reason(local_client: TestClient, server: str) -> None:
    ROUTES["/nohtml.zip"] = (200, {}, zip_bytes({"readme.txt": "hello"}))
    job = start(local_client, f"{server}/nohtml.zip")
    assert job["state"] == downloads.FAILED
    assert job["error"].startswith('"nohtml.zip" was not imported:')


def test_dismiss_and_not_found(local_client: TestClient, server: str) -> None:
    job = start(local_client, f"{server}/missing.zip")
    assert local_client.delete(f"/api/downloads/{job['id']}").status_code == 204
    assert local_client.get("/api/downloads").json() == []
    assert local_client.delete(f"/api/downloads/{job['id']}").status_code == 404


def test_no_partial_files_left(local_client: TestClient, settings: Settings, server: str) -> None:
    start(local_client, f"{server}/missing.zip")
    assert not list(settings.imports_dir.glob("*.download"))


def test_local_addresses_are_refused_by_default(client: TestClient, server: str) -> None:
    response = client.post("/api/downloads", json={"url": f"{server}/Game.zip"})
    assert response.status_code == 422
    assert "private network" in response.json()["detail"][0]["message"]


def test_connection_checks_the_address_actually_used(server: str) -> None:
    port = int(server.rsplit(":", 1)[1])
    with pytest.raises(downloads.DownloadRejected, match="private network"):
        downloads._public_connection(("127.0.0.1", port), 5)


@pytest.mark.parametrize(
    ("address", "public"),
    [
        ("127.0.0.1", False),
        ("10.0.8.2", False),
        ("192.168.1.10", False),
        ("169.254.169.254", False),
        ("::1", False),
        ("::ffff:10.0.0.1", False),
        ("fe80::1%eth0", False),
        ("93.184.215.14", True),
        ("2606:4700::6810:84e5", True),
    ],
)
def test_public_addresses(address: str, public: bool) -> None:
    assert downloads._is_public(address) is public


@pytest.mark.parametrize(
    "url",
    ["", "ftp://example.com/game.zip", "file:///etc/passwd", "https://user:pw@example.com/a.zip"],
)
def test_invalid_links(url: str) -> None:
    with pytest.raises(downloads.DownloadRejected):
        downloads.prepare_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://mega.nz/file/abc#key",
        "https://gofile.io/d/abc",
        "https://www.patreon.com/file?h=1",
    ],
)
def test_unsupported_hosters_are_explained(url: str, client: TestClient) -> None:
    response = client.post("/api/downloads", json={"url": url})
    assert response.status_code == 422
    assert "Import game" in response.json()["detail"][0]["message"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://pixeldrain.com/u/AbC123", "https://pixeldrain.com/api/file/AbC123?download"),
        ("https://www.dropbox.com/s/x/Game.zip?dl=0", "https://www.dropbox.com/s/x/Game.zip?dl=1"),
        (
            "https://drive.google.com/file/d/1aB-c_D/view?usp=sharing",
            "https://drive.usercontent.google.com/download?id=1aB-c_D&export=download&confirm=t",
        ),
        ("  https://example.com/Game.zip ", "https://example.com/Game.zip"),
    ],
)
def test_hoster_pages_are_rewritten(url: str, expected: str) -> None:
    assert downloads.prepare_url(url) == expected


def test_pixeldrain_list_is_refused() -> None:
    with pytest.raises(downloads.DownloadRejected, match="list"):
        downloads.prepare_url("https://pixeldrain.com/l/abc")


@pytest.mark.parametrize(
    ("head", "suffix"),
    [
        (b"PK\x03\x04rest", ".zip"),
        (b"Rar!\x1a\x07\x01\x00", ".rar"),
        (b"7z\xbc\xaf\x27\x1c\x00", ".7z"),
        (b"\x1f\x8b\x08", ".tar.gz"),
        (b"\x00" * 257 + b"ustar\x00", ".tar"),
        (b"<html>", None),
    ],
)
def test_sniff(head: bytes, suffix: str | None) -> None:
    assert downloads.sniff(head) == suffix


def test_filename_is_cleaned() -> None:
    assert downloads.clean_filename("../..\\evil/Game 1.0.zip") == "Game 1.0.zip"
    assert downloads.clean_filename(" .. ") == ""
