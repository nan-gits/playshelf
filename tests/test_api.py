from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    """App with two games that are scanned at startup."""
    write(settings.games_dir / "Paper Lanterns 1.4.4" / "Paper Lanterns.html", "<title>PL</title>")
    write(settings.games_dir / "Paper Lanterns 1.4.4" / "extra" / "alt.html")
    write(settings.games_dir / "Paper Lanterns 1.4.4" / "credits.html")
    write(settings.games_dir / "CK" / "index.html", "<title>CK</title>")
    write(settings.games_dir / "outside.html")
    with TestClient(create_app(settings)) as client:
        yield client


def game_id(client: TestClient, folder: str) -> int:
    return next(g["id"] for g in client.get("/api/games").json() if g["folder"] == folder)


def messages(response) -> dict[str, str]:
    return {error["field"]: error["message"] for error in response.json()["detail"]}


def test_games_are_scanned_on_startup(api: TestClient) -> None:
    response = api.get("/api/games")
    assert response.status_code == 200
    games = response.json()
    assert [(g["title"], g["entry"], g["version_hint"]) for g in games] == [
        ("CK", "index.html", None),
        ("PL", "Paper Lanterns.html", "1.4.4"),
    ]
    assert all(g["version"] is None for g in games)


def test_get_single_game(api: TestClient) -> None:
    gid = game_id(api, "CK")
    assert api.get(f"/api/games/{gid}").json()["folder"] == "CK"
    assert api.get("/api/games/999").status_code == 404
    assert api.get("/api/games/999").json() == {"detail": "Game not found."}


def test_sources_without_adult_sites_by_default(api: TestClient) -> None:
    assert api.get("/api/sources").json() == ["itch.io", "Patreon", "Other"]


def test_sources_with_adult_sites(settings: Settings) -> None:
    settings.adult_sources = True
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/sources").json() == [
            "F95Zone",
            "itch.io",
            "Patreon",
            "TFGames",
            "Other",
        ]


def test_hidden_source_stays_valid(api: TestClient) -> None:
    gid = game_id(api, "CK")
    response = api.patch(f"/api/games/{gid}", json={"version": "1", "source": "F95Zone"})
    assert response.status_code == 200
    assert response.json()["source"] == "F95Zone"


def test_patch_all_fields(api: TestClient) -> None:
    gid = game_id(api, "Paper Lanterns 1.4.4")
    response = api.patch(
        f"/api/games/{gid}",
        json={
            "title": " Paper Lanterns ",
            "version": " 1.4.4 ",
            "source": "F95Zone",
            "update_url": "https://f95zone.to/threads/123/",
            "notes": "  Chapter 3  ",
            "entry": "extra/alt.html",
        },
    )
    assert response.status_code == 200, response.text
    game = response.json()
    assert game["title"] == "Paper Lanterns"
    assert game["version"] == "1.4.4"
    assert game["source"] == "F95Zone"
    assert game["update_url"] == "https://f95zone.to/threads/123/"
    assert game["notes"] == "Chapter 3"
    assert game["entry"] == "extra/alt.html"
    assert game["updated_at"]


def test_patch_clears_optional_fields(api: TestClient) -> None:
    gid = game_id(api, "CK")
    api.patch(f"/api/games/{gid}", json={"version": "1", "source": "Patreon", "notes": "x"})
    game = api.patch(f"/api/games/{gid}", json={"source": "", "update_url": "", "notes": ""}).json()
    assert (game["source"], game["update_url"], game["notes"]) == (None, None, None)
    assert game["version"] == "1"


@pytest.mark.parametrize("version", ["", "   ", None])
def test_version_must_not_be_empty(api: TestClient, version: str | None) -> None:
    gid = game_id(api, "CK")
    response = api.patch(f"/api/games/{gid}", json={"version": version})
    assert response.status_code == 422
    assert "required" in messages(response)["version"]


def test_version_required_when_editing_game_without_version(api: TestClient) -> None:
    gid = game_id(api, "CK")
    response = api.patch(f"/api/games/{gid}", json={"notes": "just a note"})
    assert response.status_code == 422
    assert "required" in messages(response)["version"]
    # With a version it works, afterwards also without
    assert api.patch(f"/api/games/{gid}", json={"version": "0.3"}).status_code == 200
    assert api.patch(f"/api/games/{gid}", json={"notes": "now"}).status_code == 200


@pytest.mark.parametrize("url", ["f95zone.to/x", "ftp://x", "javascript:alert(1)"])
def test_update_url_must_be_http(api: TestClient, url: str) -> None:
    gid = game_id(api, "CK")
    response = api.patch(f"/api/games/{gid}", json={"version": "1", "update_url": url})
    assert response.status_code == 422
    assert messages(response)["update_url"] == (
        "The update link must start with http:// or https://."
    )


def test_source_must_be_known(api: TestClient) -> None:
    gid = game_id(api, "CK")
    response = api.patch(f"/api/games/{gid}", json={"version": "1", "source": "Steam"})
    assert response.status_code == 422
    assert messages(response)["source"].startswith("Unknown source")


@pytest.mark.parametrize(
    "entry",
    ["missing.html", "credits.html", "../CK/index.html", "/etc/passwd", "../outside.html"],
)
def test_entry_must_be_html_in_game_folder(api: TestClient, entry: str) -> None:
    gid = game_id(api, "Paper Lanterns 1.4.4")
    response = api.patch(f"/api/games/{gid}", json={"version": "1", "entry": entry})
    assert response.status_code == 422
    assert "start file" in messages(response)["entry"]


def test_patch_rejects_unknown_fields_and_bad_types(api: TestClient) -> None:
    gid = game_id(api, "CK")
    response = api.patch(f"/api/games/{gid}", json={"version": "1", "play_count": 5})
    assert response.status_code == 422
    assert messages(response) == {"play_count": "Unknown field."}
    response = api.patch(f"/api/games/{gid}", json={"version": 3})
    assert response.status_code == 422


def test_patch_unknown_game(api: TestClient) -> None:
    assert api.patch("/api/games/999", json={"version": "1"}).status_code == 404


def test_html_files(api: TestClient) -> None:
    gid = game_id(api, "Paper Lanterns 1.4.4")
    assert api.get(f"/api/games/{gid}/html-files").json() == [
        "extra/alt.html",
        "Paper Lanterns.html",
    ]


def test_scan_reports_new_found_missing(api: TestClient, settings: Settings) -> None:
    assert api.post("/api/scan").json() == {"added": 0, "found": 2, "missing": 0}
    write(settings.games_dir / "Moonbeam" / "Moonbeam.html")
    (settings.games_dir / "CK" / "index.html").unlink()
    (settings.games_dir / "CK").rmdir()
    assert api.post("/api/scan").json() == {"added": 1, "found": 2, "missing": 1}
    kettle = next(g for g in api.get("/api/games").json() if g["folder"] == "CK")
    assert kettle["missing"] is True


def test_delete_only_missing_and_never_files(api: TestClient, settings: Settings) -> None:
    gid = game_id(api, "CK")
    response = api.delete(f"/api/games/{gid}")
    assert response.status_code == 409
    assert (settings.games_dir / "CK" / "index.html").exists()

    (settings.games_dir / "CK").rename(settings.games_dir.parent / "CK-gone")
    api.post("/api/scan")
    assert api.delete(f"/api/games/{gid}").status_code == 204
    assert api.get(f"/api/games/{gid}").status_code == 404
    assert (settings.games_dir.parent / "CK-gone" / "index.html").exists()
    assert api.delete(f"/api/games/{gid}").status_code == 404
    # Scanning again does not add anything back
    assert api.post("/api/scan").json()["found"] == 1


def test_html_files_of_missing_folder(api: TestClient, settings: Settings) -> None:
    gid = game_id(api, "CK")
    (settings.games_dir / "CK").rename(settings.games_dir.parent / "CK-gone")
    assert api.get(f"/api/games/{gid}/html-files").status_code == 409
