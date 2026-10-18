"""Game status and favourites."""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import DB_FILENAME, MIGRATIONS, STATUSES, Database
from app.main import create_app


def setup_game(settings: Settings) -> None:
    folder = settings.games_dir / "CK"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<title>CK</title>", encoding="utf-8")


def test_new_games_have_no_status_and_are_no_favorite(settings: Settings) -> None:
    setup_game(settings)
    with TestClient(create_app(settings)) as client:
        [game] = client.get("/api/games").json()
    assert game["status"] is None
    assert game["favorite"] is False


def test_statuses_are_listed(client: TestClient) -> None:
    assert client.get("/api/statuses").json() == STATUSES


def test_status_and_favorite_can_be_set_without_version(settings: Settings) -> None:
    """Set on the tile, even while the version is still missing."""
    setup_game(settings)
    with TestClient(create_app(settings)) as client:
        [game] = client.get("/api/games").json()
        response = client.patch(f"/api/games/{game['id']}", json={"favorite": True})
        assert response.status_code == 200
        assert response.json()["favorite"] is True
        response = client.patch(f"/api/games/{game['id']}", json={"status": "completed"})
        assert response.json()["status"] == "completed"
        assert response.json()["favorite"] is True

        # Reset the status
        response = client.patch(f"/api/games/{game['id']}", json={"status": ""})
        assert response.json()["status"] is None

        # Other fields still need a version
        response = client.patch(f"/api/games/{game['id']}", json={"notes": "x", "favorite": False})
        assert response.status_code == 422


def test_unknown_status_and_null_favorite_are_rejected(settings: Settings) -> None:
    setup_game(settings)
    with TestClient(create_app(settings)) as client:
        [game] = client.get("/api/games").json()
        response = client.patch(f"/api/games/{game['id']}", json={"status": "Whatever"})
        assert response.status_code == 422
        assert "Unknown status" in response.json()["detail"][0]["message"]
        response = client.patch(f"/api/games/{game['id']}", json={"favorite": None})
        assert response.status_code == 422


def test_migration_keeps_existing_games(tmp_path: Path) -> None:
    path = tmp_path / DB_FILENAME
    old = Database(path, MIGRATIONS[:3])
    with old.connect() as conn:
        conn.execute(
            "INSERT INTO games (folder, entry, title, added_at)"
            " VALUES ('CK', 'index.html', 'CK', '')"
        )
    db = Database(path)
    [game] = db.list_games()
    assert (game["title"], game["status"], game["favorite"]) == ("CK", None, False)


def test_quick_update_response_keeps_update_info(settings: Settings) -> None:
    """The tile replaces its data with the response; the update info must not be missing."""
    setup_game(settings)
    with TestClient(create_app(settings)) as client:
        [game] = client.get("/api/games").json()
        response = client.patch(f"/api/games/{game['id']}", json={"favorite": True})
    assert "update" in response.json()
