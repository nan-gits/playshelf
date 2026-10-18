"""Framework of the online update check, without real network access."""

import urllib.error
from collections.abc import Iterator
from email.message import Message
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import updates
from app.config import Settings
from app.db import DB_FILENAME, Database
from app.main import create_app


class FakeFetch:
    """Returns given answers instead of real requests and records the URLs."""

    def __init__(self, pages: dict[str, str | Exception] | None = None) -> None:
        self.pages = pages or {}
        self.urls: list[str] = []

    def __call__(self, url: str, headers: dict[str, str] | None = None) -> updates.Response:
        self.urls.append(url)
        page = self.pages.get(url)
        if page is None:
            raise updates.CheckError(_http_404())
        if isinstance(page, Exception):
            raise page
        return updates.Response(url, 200, page)


def _http_404() -> str:
    return "The page was not found (404). Is the update link still correct?"


class Dummy:
    """Test adapter for example.org: page content "version=…" or "date=…"."""

    name = "Other"
    domains = ("example.org",)
    adult = False

    def latest(self, url: str, fetch: updates.Fetch, title: str | None = None) -> updates.Latest:
        text = fetch(url).text
        key, _, value = text.partition("=")
        if key == "boom":
            raise RuntimeError("broken")
        return updates.Latest(**{key: value})


@pytest.fixture(autouse=True)
def dummy_adapter() -> Iterator[None]:
    adapter = Dummy()
    updates.ADAPTERS.insert(0, adapter)
    yield
    updates.ADAPTERS.remove(adapter)


# Versions


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("0.11", "0_11", 0),
        ("v1.4.4", "1.4.4", 0),
        ("1.0", "1.0.0", 0),
        ("v.0.78", "v0.78 Public", 0),
        ("Version 2.1", "2.1", 0),
        ("1.0.26a", "1.0.26", 1),
        ("1.0.26b", "1.0.26a", 1),
        ("0.10", "0.9", 1),
        ("v1.4.5", "1.4.4", 1),
        ("0.191.0", "0.19", 1),
        ("1.0", "1.0 beta", 1),
        ("1.0 rc1", "1.0 beta", 1),
        ("Ch.3 v0.5", "0.4", 1),
        ("0.4", "0.5", -1),
        ("k.A", "0.5", None),
        ("Final", "1.0", None),
        ("Final", "final", 0),
        (None, "1.0", None),
    ],
)
def test_compare_versions(a: str | None, b: str | None, expected: int | None) -> None:
    assert updates.compare_versions(a, b) == expected


def test_adapter_for_matches_domain_and_subdomains() -> None:
    assert updates.adapter_for("https://example.org/x").name == "Other"
    assert updates.adapter_for("https://www.example.org/x").name == "Other"
    assert updates.adapter_for("https://notexample.org/x") is None
    assert updates.adapter_for("https://f95zone.to/threads/1/").name == "F95Zone"


def test_latest_needs_version_or_date() -> None:
    with pytest.raises(updates.CheckError):
        updates.Latest()


# Checking and storing


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / DB_FILENAME)


def make_game(db: Database, version: str | None, url: str | None) -> dict:
    game = db.create_game("Game", "index.html", "Game")
    return db.update_game(game["id"], {"version": version, "update_url": url})


def test_check_game_saves_result(db: Database) -> None:
    game = make_game(db, "0.4", "https://example.org/game")
    check = updates.check_game(db, game, FakeFetch({"https://example.org/game": "version=0.5"}))
    assert check["status"] == updates.STATUS_OK
    assert check["version"] == "0.5"
    assert check["error"] is None
    assert check["checked_at"]
    [annotated] = updates.annotate(db, db.list_games())
    assert annotated["update"]["available"] is True
    assert annotated["update"]["version"] == "0.5"


def test_hint_disappears_when_version_is_entered(db: Database) -> None:
    game = make_game(db, "0.4", "https://example.org/game")
    updates.check_game(db, game, FakeFetch({"https://example.org/game": "version=v0.5"}))
    db.update_game(game["id"], {"version": "0.5"})
    [annotated] = updates.annotate(db, db.list_games())
    assert annotated["update"]["available"] is False


def test_result_for_changed_link_is_ignored(db: Database) -> None:
    game = make_game(db, "0.4", "https://example.org/game")
    updates.check_game(db, game, FakeFetch({"https://example.org/game": "version=0.5"}))
    db.update_game(game["id"], {"update_url": "https://example.org/anders"})
    [annotated] = updates.annotate(db, db.list_games())
    assert annotated["update"] is None


def test_errors_are_saved_not_raised(db: Database) -> None:
    game = make_game(db, "0.4", "https://example.org/fehlt")
    check = updates.check_game(db, game, FakeFetch())
    assert check["status"] == updates.STATUS_ERROR
    assert "404" in check["error"]

    game = make_game_folder(db, "Zwei", "https://example.org/boom")
    check = updates.check_game(db, game, FakeFetch({"https://example.org/boom": "boom=1"}))
    assert check["status"] == updates.STATUS_ERROR
    assert "broken" in check["error"]


def make_game_folder(db: Database, folder: str, url: str) -> dict:
    game = db.create_game(folder, "index.html", folder)
    return db.update_game(game["id"], {"version": "1", "update_url": url})


def test_unknown_domain_needs_manual_check(db: Database) -> None:
    game = make_game(db, "1.0", "https://unknown.example.com/game")
    check = updates.check_game(db, game, FakeFetch())
    assert check["status"] == updates.STATUS_MANUAL
    assert "unknown.example.com" in check["error"]


def test_date_only_source_compares_with_install_date(db: Database) -> None:
    game = make_game(db, "1.0", "https://example.org/date")
    db.add_history(game["id"], "1.0", "edited")
    fetch = FakeFetch({"https://example.org/date": "date=2000-01-01"})
    updates.check_game(db, game, fetch)
    assert updates.annotate(db, db.list_games())[0]["update"]["available"] is False
    fetch.pages["https://example.org/date"] = "date=2999-01-01"
    updates.check_game(db, game, fetch)
    assert updates.annotate(db, db.list_games())[0]["update"]["available"] is True


def test_deleting_game_removes_check(db: Database) -> None:
    game = make_game(db, "1.0", "https://example.org/game")
    updates.check_game(db, game, FakeFetch({"https://example.org/game": "version=2"}))
    db.set_missing(game["id"])
    db.delete_game(game["id"])
    assert updates.all_checks(db) == {}


def test_check_all_only_due_games(db: Database) -> None:
    game = make_game(db, "1.0", "https://example.org/game")
    make_game_folder(db, "A without link", "https://example.org/two")
    db.update_game(db.list_games()[0]["id"], {"update_url": None})
    fetch = FakeFetch({"https://example.org/game": "version=2"})
    assert updates.check_all(db, fetch, max_age_hours=24) == 1
    assert fetch.urls == ["https://example.org/game"]
    # freshly checked: due again only after the interval
    assert updates.check_all(db, fetch, max_age_hours=24) == 0
    assert updates.check_all(db, fetch, max_age_hours=0) == 1
    assert updates.get_check(db, game["id"])["version"] == "2"


def test_fetcher_waits_between_requests_to_same_host(monkeypatch) -> None:
    slept: list[float] = []
    clock = iter([100.0, 100.0, 101.0, 101.0])
    monkeypatch.setattr(updates.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(updates.time, "sleep", slept.append)

    class FakeResponse:
        status = 200
        headers = type("H", (), {"get_content_charset": lambda self: "utf-8"})()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            return b"ok"

        def geturl(self):
            return "https://example.org/"

    requests = []

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    fetch = updates.Fetcher(min_delay=5, timeout=7)
    assert fetch("https://example.org/a").text == "ok"
    fetch("https://example.org/b")
    assert slept == [4.0]
    assert requests[0][1] == 7
    assert requests[0][0].get_header("User-agent").startswith("Playshelf/")


@pytest.mark.parametrize(
    ("headers", "error"),
    [({"cf-mitigated": "challenge"}, updates.BotProtection), ({}, updates.CheckError)],
)
def test_fetcher_recognizes_bot_protection(monkeypatch, headers, error) -> None:
    answer = Message()
    for name, value in headers.items():
        answer[name] = value

    def fake_urlopen(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", answer, None)

    monkeypatch.setattr(updates.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(error) as caught:
        updates.Fetcher(min_delay=0)("https://f95zone.to/threads/1/")
    is_manual = isinstance(caught.value, updates.ManualCheck)
    assert is_manual == bool(headers)
    if is_manual:
        assert "f95zone.to asks for a browser check" in str(caught.value)


# API


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    (settings.games_dir / "Game").mkdir(parents=True)
    (settings.games_dir / "Game" / "index.html").write_text("<title>Game</title>")
    app = create_app(settings)
    with TestClient(app) as client:
        app.state.fetcher = FakeFetch({"https://example.org/game": "version=0.5"})
        yield client


def test_api_check_now(api: TestClient) -> None:
    gid = api.get("/api/games").json()[0]["id"]
    assert api.get("/api/games").json()[0]["update"] is None
    assert api.post(f"/api/games/{gid}/check-update").status_code == 409

    api.patch(
        f"/api/games/{gid}", json={"version": "0.4", "update_url": "https://example.org/game"}
    )
    response = api.post(f"/api/games/{gid}/check-update")
    assert response.status_code == 200, response.text
    assert response.json()["update"]["available"] is True
    assert response.json()["update"]["version"] == "0.5"
    assert api.get(f"/api/games/{gid}").json()["update"]["available"] is True
    assert api.post("/api/games/999/check-update").status_code == 404


def test_api_check_all(api: TestClient) -> None:
    gid = api.get("/api/games").json()[0]["id"]
    api.patch(
        f"/api/games/{gid}", json={"version": "0.5", "update_url": "https://example.org/game"}
    )
    response = api.post("/api/updates/check")
    assert response.status_code == 202
    assert response.json()["count"] == 1
    # Background tasks run in the TestClient before it returns
    game = api.get("/api/games").json()[0]
    assert game["update"]["status"] == "ok"
    assert game["update"]["available"] is False
    assert api.get("/api/updates/status").json() == {"running": False}


# Schedule


def test_scheduled_checks_run_due_games(db: Database, monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(updates, "START_DELAY", 0)
    monkeypatch.setattr(updates, "TICK", 3600)
    game = make_game(db, "1.0", "https://example.org/game")
    fetch = FakeFetch({"https://example.org/game": "version=1.1"})

    async def run() -> None:
        task = asyncio.create_task(updates.run_scheduled_checks(db, fetch, every_hours=24))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if fetch.urls:
                break
        await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(run())
    assert fetch.urls == ["https://example.org/game"]
    assert updates.get_check(db, game["id"])["version"] == "1.1"


def test_schedule_is_configurable(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("UPDATE_CHECK_HOURS", "6")
    assert Settings(_env_file=None).update_check_hours == 6
    monkeypatch.setenv("UPDATE_CHECK_HOURS", "0")
    assert Settings(_env_file=None).update_check_hours == 0
