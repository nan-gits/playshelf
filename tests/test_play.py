from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

FOLDER = "Harbor Town & Co (Public) #1 ä"
ENTRY = "Harbor Town USA/Harbor Town USA_ Hometown Stories.html"


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    game = settings.games_dir / FOLDER
    (game / "Harbor Town USA" / "img").mkdir(parents=True)
    (game / ENTRY).write_text("<title>Harbor Town</title><p>Game</p>", encoding="utf-8")
    for name in (
        "a.png",
        "b.jpg",
        "c.webp",
        "d.mp3",
        "e.ogg",
        "f.mp4",
        "g.webm",
        "h.woff2",
        "i.woff",
        "j.ttf",
        "k.js",
        "l.css",
        "m.svg",
        "n.gif",
        "o.m4a",
        "p.json",
    ):
        (game / "Harbor Town USA" / "img" / name).write_bytes(b"\0")
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "secret.txt").write_text("secret")
    with TestClient(create_app(settings)) as client:
        yield client


def the_game(client: TestClient) -> dict:
    [game] = client.get("/api/games").json()
    return game


def test_play_redirects_and_records_start(api: TestClient) -> None:
    game = the_game(api)
    assert game["last_played"] is None
    response = api.get(f"/play/{game['id']}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    location = response.headers["location"]
    assert " " not in location and "&" not in location and "#" not in location
    assert location.startswith("/games/Harbor%20Town%20%26%20Co%20%28Public%29%20%231%20%C3%A4/")

    page = api.get(location)
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "Game" in page.text

    after = the_game(api)
    assert after["play_count"] == 1
    assert after["last_played"] is not None
    api.get(f"/play/{game['id']}", follow_redirects=False)
    assert the_game(api)["play_count"] == 2


@pytest.mark.parametrize(
    ("name", "media_type"),
    [
        ("a.png", "image/png"),
        ("b.jpg", "image/jpeg"),
        ("c.webp", "image/webp"),
        ("d.mp3", "audio/mpeg"),
        ("e.ogg", "audio/ogg"),
        ("f.mp4", "video/mp4"),
        ("g.webm", "video/webm"),
        ("h.woff2", "font/woff2"),
        ("i.woff", "font/woff"),
        ("j.ttf", "font/ttf"),
        ("k.js", "text/javascript"),
        ("l.css", "text/css"),
        ("m.svg", "image/svg+xml"),
        ("n.gif", "image/gif"),
        ("o.m4a", "audio/mp4"),
        ("p.json", "application/json"),
    ],
)
def test_assets_have_content_types(api: TestClient, name: str, media_type: str) -> None:
    game = the_game(api)
    url = "/".join(
        api.get(f"/play/{game['id']}", follow_redirects=False).headers["location"].split("/")[:-1]
    )
    response = api.get(f"{url}/img/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";")[0] == media_type


@pytest.mark.parametrize(
    "path",
    [
        "/games/../data/secret.txt",
        "/games/%2e%2e/data/secret.txt",
        "/games/..%2fdata/secret.txt",
        "/games/%2e%2e%2fdata%2fsecret.txt",
        "/games/x/../../data/secret.txt",
    ],
)
def test_no_path_traversal(api: TestClient, path: str) -> None:
    response = api.get(path)
    assert "secret" not in response.text
    assert response.status_code in (400, 404)


def test_symlink_out_of_games_dir_is_blocked(api: TestClient, settings: Settings) -> None:
    link = settings.games_dir / "link"
    try:
        link.symlink_to(settings.data_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks are not supported here")
    response = api.get("/games/link/secret.txt")
    assert response.status_code == 404
    assert "secret" not in response.text


def test_no_directory_listing(api: TestClient) -> None:
    assert api.get("/games/").status_code == 404
    assert api.get(f"/games/{FOLDER}/").status_code == 404


def test_play_unknown_game(api: TestClient) -> None:
    response = api.get("/play/999")
    assert response.status_code == 404
    assert "Game not found" in response.text
    german = api.get("/play/999", headers={"Accept-Language": "de"})
    assert "Spiel nicht gefunden" in german.text
    assert 'lang="de"' in german.text


def test_play_missing_folder_gives_clear_message(api: TestClient, settings: Settings) -> None:
    game = the_game(api)
    (settings.games_dir / FOLDER).rename(settings.games_dir / "renamed")
    response = api.get(f"/play/{game['id']}", follow_redirects=False)
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Game folder missing" in response.text
    assert "&amp; Co" in response.text  # folder name is HTML-escaped
    after = api.get(f"/api/games/{game['id']}").json()
    assert after["missing"] is True
    assert after["play_count"] == 0


def test_play_missing_entry_gives_clear_message(api: TestClient, settings: Settings) -> None:
    game = the_game(api)
    (settings.games_dir / FOLDER / ENTRY).unlink()
    response = api.get(f"/play/{game['id']}")
    assert response.status_code == 404
    assert "Start file missing" in response.text
    assert the_game(api)["play_count"] == 0


def test_folder_name_with_percent_and_plus(settings: Settings) -> None:
    folder = settings.games_dir / "100% Game+ v1.0"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("ok")
    with TestClient(create_app(settings)) as client:
        [game] = client.get("/api/games").json()
        response = client.get(f"/play/{game['id']}")
        assert response.status_code == 200
        assert response.text == "ok"


def test_relative_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relative GAMES_DIR as in the default configuration."""
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        _env_file=None,
        games_dir=Path("games"),
        data_dir=Path("data"),
        inbox_dir=Path("inbox"),
        auth="none",
    )
    (tmp_path / "games" / "G").mkdir(parents=True)
    (tmp_path / "games" / "G" / "index.html").write_text("ok")
    with TestClient(create_app(settings)) as client:
        assert client.get("/play/1").text == "ok"
