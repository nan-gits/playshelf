"""Online update check: look up the current version behind a game's update link.

Each source has an adapter (`app/sources/`) that returns the version currently
online, or the date of the last change, for a link. The link's domain decides
which adapter is responsible. The result of each check is stored per game (table
`update_checks`); whether an update is available is computed on read against
the recorded version. That way the notice disappears on its own as soon as the
new version is imported or entered.
"""

import asyncio
import logging
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

from app import PROJECT_URL, __version__
from app.db import Database, now

log = logging.getLogger(__name__)

USER_AGENT = f"Playshelf/{__version__} (+{PROJECT_URL})"
TIMEOUT = 20  # seconds per request
MIN_DELAY = 5.0  # seconds between two requests to the same server
MAX_BYTES = 5 * 1024 * 1024  # larger responses are cut off

# Result of a check
STATUS_OK = "ok"  # version or date found
STATUS_ERROR = "error"  # request failed, message in `error`
STATUS_MANUAL = "manual"  # source cannot be checked automatically


class CheckError(Exception):
    """The check failed; the text is shown in the UI."""


class ManualCheck(CheckError):
    """This source cannot be checked automatically (for example, login required)."""


@dataclass
class Latest:
    """What an adapter found online: version, date of change, or both."""

    version: str | None = None
    date: str | None = None  # ISO 8601, for example 2026-10-01, or with time

    def __post_init__(self) -> None:
        if not self.version and not self.date:
            raise CheckError("Neither a version nor a date was found on the page.")


# Fetching web pages


@dataclass
class Response:
    url: str
    status: int
    text: str


Fetch = Callable[..., Response]


class Fetcher:
    """Polite fetching: own user agent, timeout, pause per server."""

    def __init__(self, min_delay: float = MIN_DELAY, timeout: float = TIMEOUT) -> None:
        self.min_delay = min_delay
        self.timeout = timeout
        self._last: dict[str, float] = {}

    def __call__(self, url: str, headers: dict[str, str] | None = None) -> Response:
        host = urlsplit(url).hostname or ""
        wait = self._last.get(host, 0) + self.min_delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "en,de;q=0.8",
                **(headers or {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read(MAX_BYTES)
                charset = response.headers.get_content_charset() or "utf-8"
                return Response(response.geturl(), response.status, body.decode(charset, "replace"))
        except urllib.error.HTTPError as exc:
            raise CheckError(_http_message(exc.code)) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise CheckError(f"The page cannot be reached: {reason}") from exc
        finally:
            self._last[host] = time.monotonic()


def _http_message(code: int) -> str:
    if code == 404:
        return "The page was not found (404). Is the update link still correct?"
    if code in (401, 403):
        return f"Access was denied ({code}), possibly a login or bot protection."
    if code == 429:
        return "Too many requests (429). The next check will try again."
    return f"The page answered with error {code}."


# Adapter


class Adapter(Protocol):
    """Common interface of all sources."""

    name: str  # as in db.SOURCES
    domains: tuple[str, ...]  # responsible for these domains and their subdomains
    adult: bool  # adult game site, only checked with ADULT_SOURCES=true

    def latest(self, url: str, fetch: Fetch) -> Latest: ...


ADAPTERS: list[Adapter] = []


def register(adapter: Adapter) -> Adapter:
    ADAPTERS.append(adapter)
    return adapter


def adapter_for(url: str) -> Adapter | None:
    host = (urlsplit(url).hostname or "").lower()
    for adapter in ADAPTERS:
        if any(host == d or host.endswith(f".{d}") for d in adapter.domains):
            return adapter
    return None


# Comparing versions

_VERSION = re.compile(r"\d+(?:[._]\d+)*(?:[a-z](?![a-z]))?", re.IGNORECASE)
_PRE_RELEASE = {"alpha": -3, "beta": -2, "pre": -2, "rc": -1}


def version_key(version: str | None) -> tuple | None:
    """Sort key of a version string, None if it contains no number.

    Understands, among others, `0.11`, `v1.4.4`, `0_11`, `1.0.26a`, `Ch.3 v0.5`. A
    number preceded by `v` is taken as the version. Trailing zeros do not count
    (1.0 = 1.0.0); a trailing letter counts as a small follow-up (1.0.26a > 1.0.26).
    """
    if not version:
        return None
    text = version.strip().lower()
    marked = re.search(r"(?:^|[^a-z])v(?:er(?:sion)?)?\.?\s*(\d)", text)
    match = _VERSION.search(text, marked.start(1) if marked else 0)
    if not match:
        return None
    raw = match.group().replace("_", ".")
    letter = raw[-1] if raw[-1].isalpha() else ""
    numbers = [int(part) for part in raw.rstrip(letter).split(".")]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    rest = text[match.end() :]
    stage = next((v for k, v in _PRE_RELEASE.items() if re.match(rf"\W*{k}\b", rest)), 0)
    return (tuple(numbers), stage, letter)


def compare_versions(a: str | None, b: str | None) -> int | None:
    """-1, 0 or 1 for `a` compared to `b`; None if either side is not comparable."""
    key_a, key_b = version_key(a), version_key(b)
    if key_a is None or key_b is None:
        if a and b and a.strip().lower() == b.strip().lower():
            return 0
        return None
    return (key_a > key_b) - (key_a < key_b)


# Checking and storing


def check_url(url: str, fetch: Fetch, adult_sources: bool = False) -> Latest:
    adapter = adapter_for(url)
    if adapter is None:
        host = urlsplit(url).hostname or url
        raise ManualCheck(f"There is no automatic check for {host}.")
    if adapter.adult and not adult_sources:
        raise ManualCheck(
            f"Automatic checks for {adapter.name} are turned off (ADULT_SOURCES=false)."
        )
    return adapter.latest(url, fetch)


def check_game(
    db: Database, game: dict[str, Any], fetch: Fetch, adult_sources: bool = False
) -> dict[str, Any]:
    """Checks one game, stores the result and returns it."""
    url = game.get("update_url")
    if not url:
        raise CheckError("This game has no update link.")
    result: dict[str, Any] = {"url": url, "version": None, "date": None, "error": None}
    try:
        latest = check_url(url, fetch, adult_sources)
        result.update(status=STATUS_OK, version=latest.version, date=latest.date)
    except ManualCheck as exc:
        result.update(status=STATUS_MANUAL, error=str(exc))
    except CheckError as exc:
        result.update(status=STATUS_ERROR, error=str(exc))
    except Exception as exc:  # bug in the adapter, for example a changed page layout
        log.exception("Update check of %s failed", url)
        result.update(status=STATUS_ERROR, error=f"The page could not be read ({exc}).")
    save_check(db, game["id"], result)
    return get_check(db, game["id"])  # type: ignore[return-value]


def save_check(db: Database, game_id: int, result: dict[str, Any]) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO update_checks"
            " (game_id, url, status, version, date, error, checked_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                game_id,
                result["url"],
                result["status"],
                result["version"],
                result["date"],
                result["error"],
                now(),
            ),
        )


def get_check(db: Database, game_id: int) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM update_checks WHERE game_id = ?", (game_id,)).fetchone()
    return dict(row) if row else None


def all_checks(db: Database) -> dict[int, dict[str, Any]]:
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM update_checks").fetchall()
    return {row["game_id"]: dict(row) for row in rows}


def last_change(db: Database, game: dict[str, Any]) -> str:
    """When the installed version was recorded (for sources that only give a date)."""
    with db.connect() as conn:
        row = conn.execute(
            "SELECT MAX(at) FROM game_history WHERE game_id = ?", (game["id"],)
        ).fetchone()
    return row[0] or game["added_at"]


def update_info(
    game: dict[str, Any], check: dict[str, Any] | None, installed_at: str | None = None
) -> dict[str, Any] | None:
    """Update check state for display, None if never checked.

    `available` is True if a newer version is online or, for sources that only
    give a date, if the page changed after `installed_at`. A result for an update
    link that has since been changed no longer counts.
    """
    if check is None or check["url"] != game.get("update_url"):
        return None
    available = False
    if check["status"] == STATUS_OK:
        if check["version"]:
            available = compare_versions(check["version"], game.get("version")) == 1
        elif check["date"] and installed_at:
            available = _as_utc(check["date"]) > _as_utc(installed_at)
    return {
        "status": check["status"],
        "version": check["version"],
        "date": check["date"],
        "error": check["error"],
        "checked_at": check["checked_at"],
        "available": available,
    }


def _as_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def annotate(db: Database, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adds `update` to each game (result of the latest check, or None)."""
    checks = all_checks(db)
    for game in games:
        check = checks.get(game["id"])
        installed = last_change(db, game) if check and not check["version"] else None
        game["update"] = update_info(game, check, installed)
    return games


# Checking all games

_running = threading.Lock()


def due_games(db: Database, max_age_hours: float | None = None) -> list[dict[str, Any]]:
    """Games with an update link whose latest check is older than `max_age_hours`."""
    checks = all_checks(db)
    due = []
    for game in db.list_games():
        if not game["update_url"] or game["missing"]:
            continue
        check = checks.get(game["id"])
        if max_age_hours is not None and check and check["url"] == game["update_url"]:
            age = datetime.now(UTC) - _as_utc(check["checked_at"])
            if age.total_seconds() < max_age_hours * 3600:
                continue
        due.append(game)
    return due


def check_all(
    db: Database,
    fetch: Fetch,
    max_age_hours: float | None = None,
    adult_sources: bool = False,
) -> int | None:
    """Checks all (due) games one after another; None if a check is already running."""
    if not _running.acquire(blocking=False):
        return None
    try:
        games = due_games(db, max_age_hours)
        for game in games:
            check_game(db, game, fetch, adult_sources)
        if games:
            log.info("Update check: %d games checked", len(games))
        return len(games)
    finally:
        _running.release()


def is_running() -> bool:
    return _running.locked()


# Scheduled check

START_DELAY = 120  # seconds after startup, so startup does not wait for the network
TICK = 900  # seconds between two looks for due games


async def run_scheduled_checks(
    db: Database, fetch: Fetch, every_hours: float, adult_sources: bool = False
) -> None:
    """Background task of the app: checks each game about every `every_hours` hours.

    A game is due when its latest check is older than the interval. Games are checked
    one after another with pauses per server, never in several runs at once.
    """
    await asyncio.sleep(START_DELAY)
    while True:
        try:
            await asyncio.to_thread(check_all, db, fetch, every_hours, adult_sources)
        except Exception:
            log.exception("Scheduled update check failed")
        await asyncio.sleep(TICK)


# Adapters register themselves on import
from app import sources  # noqa: E402, F401
