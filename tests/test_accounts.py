"""Several accounts: who sees which games, per-user play data, imports and the admin API."""

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.auth import hash_password
from app.config import Settings
from app.db import DB_FILENAME, MIGRATIONS, Database
from app.inbox import InboxWatcher
from app.main import create_app

PASSWORD = "geheim-genug"


def zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def app_settings(settings: Settings, monkeypatch) -> Settings:
    monkeypatch.setattr("app.auth.FAILED_LOGIN_DELAY", 0)
    monkeypatch.setattr("app.auth.ITERATIONS", 1000)
    settings.auth = "accounts"
    settings.password_hash = hash_password(PASSWORD, 1000)
    for folder in ["Shared 1_0", "Admin Only"]:
        (settings.games_dir / folder).mkdir(parents=True)
        (settings.games_dir / folder / "index.html").write_text(f"<title>{folder}</title>")
    return settings


@pytest.fixture
def app(app_settings: Settings):
    application = create_app(app_settings)
    with TestClient(application):  # runs startup: database, admin password
        yield application


def signed_in(app, username: str = "admin", password: str = PASSWORD) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/login", data={"username": username, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return client


@pytest.fixture
def admin(app) -> TestClient:
    return signed_in(app)


@pytest.fixture
def anna(app, admin: TestClient) -> TestClient:
    response = admin.post("/api/users", json={"username": "Anna", "password": "anna-passwort"})
    assert response.status_code == 201, response.text
    return signed_in(app, "anna", "anna-passwort")


def game_id(client: TestClient, folder: str) -> int:
    return next(g["id"] for g in client.get("/api/games").json() if g["folder"] == folder)


def share(admin: TestClient, folder: str, *usernames: str) -> None:
    ids = {u["username"]: u["id"] for u in admin.get("/api/users").json()}
    response = admin.put(
        f"/api/games/{game_id(admin, folder)}/owners",
        json={"user_ids": [ids[name] for name in usernames]},
    )
    assert response.status_code == 200, response.text


def test_users_see_only_their_games(admin: TestClient, anna: TestClient) -> None:
    assert anna.get("/api/games").json() == []
    share(admin, "Shared 1_0", "admin", "anna")

    assert [g["folder"] for g in anna.get("/api/games").json()] == ["Shared 1_0"]
    assert "owners" not in anna.get("/api/games").json()[0]
    hidden = game_id(admin, "Admin Only")
    for path in [f"/api/games/{hidden}", f"/api/games/{hidden}/history", f"/play/{hidden}"]:
        assert anna.get(path).status_code == 404, path
    assert anna.patch(f"/api/games/{hidden}", json={"favorite": True}).status_code == 404
    assert anna.get("/games/Admin%20Only/index.html").status_code == 404
    assert anna.get("/games/Shared%201_0/../Admin%20Only/index.html").status_code == 404
    assert anna.get("/games/Shared%201_0/index.html").status_code == 200
    assert admin.get("/games/Admin%20Only/index.html").status_code == 200

    # Admins see everything, with the owners
    games = {g["folder"]: g for g in admin.get("/api/games").json()}
    assert set(games) == {"Shared 1_0", "Admin Only"}
    assert len(games["Shared 1_0"]["owners"]) == 2


def test_play_data_is_per_user(admin: TestClient, anna: TestClient) -> None:
    share(admin, "Shared 1_0", "admin", "anna")
    shared = game_id(admin, "Shared 1_0")
    anna.get(f"/play/{shared}", follow_redirects=False)
    anna.get(f"/play/{shared}", follow_redirects=False)
    anna.patch(f"/api/games/{shared}", json={"favorite": True, "status": "playing"})
    admin.patch(f"/api/games/{shared}", json={"status": "completed"})

    mine = anna.get(f"/api/games/{shared}").json()
    theirs = admin.get(f"/api/games/{shared}").json()
    assert (mine["play_count"], mine["favorite"], mine["status"]) == (2, True, "playing")
    assert mine["last_played"]
    assert (theirs["play_count"], theirs["favorite"], theirs["status"]) == (0, False, "completed")
    assert theirs["last_played"] is None

    stats = anna.get("/api/stats").json()
    assert (stats["games"], stats["starts"], stats["favorites"]) == (1, 2, 1)
    assert admin.get("/api/stats").json()["games"] == 2


def test_remove_from_own_library(
    app_settings: Settings, admin: TestClient, anna: TestClient
) -> None:
    share(admin, "Shared 1_0", "admin", "anna")
    shared = game_id(admin, "Shared 1_0")
    anna.patch(f"/api/games/{shared}", json={"favorite": True})
    assert anna.post(f"/api/games/{shared}/remove").json() == {"deleted": False}
    assert anna.get("/api/games").json() == []
    assert (app_settings.games_dir / "Shared 1_0" / "index.html").is_file()
    owners = next(g["owners"] for g in admin.get("/api/games").json() if g["id"] == shared)
    assert len(owners) == 1
    assert anna.post(f"/api/games/{shared}/remove").status_code == 404


def test_remove_keeps_a_game_someone_else_played(
    app_settings: Settings, admin: TestClient, anna: TestClient
) -> None:
    share(admin, "Admin Only", "anna")
    game = game_id(admin, "Admin Only")
    admin.get(f"/play/{game}", follow_redirects=False)
    assert anna.post(f"/api/games/{game}/remove").json() == {"deleted": False}
    assert (app_settings.games_dir / "Admin Only").is_dir()


def test_remove_by_the_last_user_deletes_files(
    app_settings: Settings, admin: TestClient, anna: TestClient
) -> None:
    share(admin, "Admin Only", "anna")
    game = game_id(admin, "Admin Only")
    assert anna.post(f"/api/games/{game}/remove").json() == {"deleted": True}
    assert not (app_settings.games_dir / "Admin Only").exists()
    assert admin.get(f"/api/games/{game}").status_code == 404


def test_only_admins_delete_for_everyone(
    app_settings: Settings, admin: TestClient, anna: TestClient
) -> None:
    share(admin, "Shared 1_0", "admin", "anna")
    shared = game_id(admin, "Shared 1_0")
    assert anna.delete(f"/api/games/{shared}").status_code == 403
    assert admin.delete(f"/api/games/{shared}").status_code == 204
    assert anna.get("/api/games").json() == []
    assert not (app_settings.games_dir / "Shared 1_0").exists()


def test_overview_shows_who_has_which_games(admin: TestClient, anna: TestClient) -> None:
    share(admin, "Shared 1_0", "admin", "anna")
    shared = game_id(admin, "Shared 1_0")
    anna.get(f"/play/{shared}", follow_redirects=False)
    assert anna.get("/api/overview").status_code == 403
    data = admin.get("/api/overview").json()
    people = {u["username"]: u for u in data["users"]}
    assert [g["title"] for g in people["anna"]["games"]] == ["Shared 1_0"]
    assert people["anna"]["games"][0]["play_count"] == 1
    assert people["admin"]["games"][0]["play_count"] == 0
    assert [g["title"] for g in data["unowned"]] == ["Admin Only"]


def test_backups_are_per_visible_game(app, admin: TestClient, anna: TestClient) -> None:
    db = app.state.db
    hidden = game_id(admin, "Admin Only")
    db.replace_version(
        hidden,
        folder="Admin Only",
        entry="index.html",
        version="2",
        version_hint=None,
        action="update",
        backup={"version": "1", "folder": "Admin Only", "entry": "index.html", "path": "A/1"},
    )
    [backup] = admin.get("/api/backups").json()["backups"]
    assert anna.get("/api/backups").json() == {"backups": [], "keep": 3}
    assert anna.delete(f"/api/backups/{backup['id']}").status_code == 404
    assert anna.delete("/api/backups", params={"path": "A/1"}).status_code == 403


def test_only_admins_manage(anna: TestClient) -> None:
    assert anna.get("/api/me").json()["user"]["is_admin"] is False
    for method, path in [
        ("get", "/api/users"),
        ("post", "/api/scan"),
        ("put", "/api/games/1/owners"),
        ("delete", "/api/users/1"),
    ]:
        response = getattr(anna, method)(
            path, **({"json": {"user_ids": []}} if method == "put" else {})
        )
        assert response.status_code == 403, path


def test_imports_belong_to_the_uploader(app, admin: TestClient, anna: TestClient) -> None:
    archive = zip_bytes({"Annas Game 0.1/index.html": "<title>Annas Game</title>"})
    imp = anna.post("/api/imports?filename=a.zip", content=archive).json()
    assert admin.get("/api/imports").json() == []
    assert admin.post(f"/api/imports/{imp['id']}/confirm", json={"version": "1"}).status_code == 404

    hidden = game_id(admin, "Admin Only")
    response = anna.post(
        f"/api/imports/{imp['id']}/confirm", json={"version": "0.1", "game_id": hidden}
    )
    assert response.status_code == 422  # a game Anna does not see is no update target
    game = anna.post(
        f"/api/imports/{imp['id']}/confirm", json={"version": "0.1", "favorite": True}
    ).json()
    assert game["title"] == "Annas Game" and game["favorite"] is True
    assert [g["title"] for g in anna.get("/api/games").json()] == ["Annas Game"]
    assert admin.get(f"/api/games/{game['id']}").json()["favorite"] is False


def test_inbox_folder_per_user(app, app_settings: Settings, anna: TestClient) -> None:
    db: Database = app.state.db
    inbox = app_settings.inbox_dir
    assert (inbox / "admin").is_dir() and (inbox / "anna").is_dir()
    (inbox / "anna" / "Anna.zip").write_bytes(zip_bytes({"Anna 1/index.html": ""}))
    (inbox / "Root.zip").write_bytes(zip_bytes({"Root 1/index.html": ""}))
    (inbox / "nobody").mkdir()
    (inbox / "nobody" / "Lost.zip").write_bytes(zip_bytes({"Lost/index.html": ""}))
    app_settings.inbox_settle_seconds = 0
    watcher = InboxWatcher(app_settings, db)
    watcher.scan_once(now=0)
    assert sorted(watcher.scan_once(now=1)) == [inbox / "Root.zip", inbox / "anna" / "Anna.zip"]

    owners = {i["filename"]: i["user_id"] for i in db.list_imports()}
    assert owners == {"Anna": db.get_user_by_name("anna")["id"], "Root": 1}
    assert (inbox / "anna" / "processed" / "Anna.zip").is_file()
    assert (inbox / "processed" / "Root.zip").is_file()
    assert (inbox / "nobody" / "Lost.zip").is_file()  # no such account: left alone
    assert [i["title"] for i in anna.get("/api/imports").json()] == ["Anna 1"]


def test_user_management(app, app_settings: Settings, admin: TestClient, anna: TestClient) -> None:
    users = {u["username"]: u for u in admin.get("/api/users").json()}
    assert set(users) == {"admin", "anna"}
    assert "password_hash" not in users["anna"]
    anna_id, admin_id = users["anna"]["id"], users["admin"]["id"]

    assert (
        admin.post("/api/users", json={"username": "ANNA", "password": "12345678"}).status_code
        == 422
    )
    assert (
        admin.post("/api/users", json={"username": "a b", "password": "12345678"}).status_code
        == 422
    )
    assert admin.post("/api/users", json={"username": "ben", "password": "kurz"}).status_code == 422

    # The last admin stays an admin, and nobody removes themselves
    assert admin.patch(f"/api/users/{admin_id}", json={"role": "user"}).status_code == 422
    assert admin.delete(f"/api/users/{admin_id}").status_code == 409

    # Renaming moves the inbox folder; a new password ends Anna's sessions
    response = admin.patch(f"/api/users/{anna_id}", json={"username": "annika"})
    assert response.json()["username"] == "annika"
    assert (app_settings.inbox_dir / "annika").is_dir()
    assert not (app_settings.inbox_dir / "anna").exists()
    assert anna.get("/api/me").status_code == 200  # still signed in after a rename
    admin.patch(f"/api/users/{anna_id}", json={"password": "neues-passwort"})
    assert anna.get("/api/me").status_code == 401
    annika = signed_in(app, "annika", "neues-passwort")

    # Promoted, Annika may demote the first admin
    admin.patch(f"/api/users/{anna_id}", json={"role": "admin"})
    assert annika.get("/api/users").status_code == 200

    # Removing an account keeps its games; its pending imports go to the remover
    imp = annika.post("/api/imports?filename=x.zip", content=zip_bytes({"X/index.html": ""})).json()
    assert admin.delete(f"/api/users/{anna_id}").status_code == 204
    assert annika.get("/api/me").status_code == 401
    assert [i["id"] for i in admin.get("/api/imports").json()] == [imp["id"]]
    assert admin.delete(f"/api/users/{anna_id}").status_code == 404


def test_own_password_and_language(app, anna: TestClient) -> None:
    wrong = anna.post("/api/me/password", json={"current": "falsch", "new": "neues-passwort"})
    assert wrong.status_code == 422
    response = anna.post(
        "/api/me/password", json={"current": "anna-passwort", "new": "neues-passwort"}
    )
    assert response.status_code == 204
    assert anna.get("/api/me").status_code == 200  # the new cookie keeps this session
    signed_in(app, "anna", "neues-passwort")

    assert anna.patch("/api/me", json={"language": "de"}).json()["user"]["language"] == "de"
    page = anna.get("/", headers={"Accept-Language": "en"}).text
    assert 'lang="de"' in page
    anna.patch("/api/me", json={"language": None})
    assert 'lang="en"' in anna.get("/", headers={"Accept-Language": "en"}).text


def test_migration_gives_existing_data_to_the_first_admin(tmp_path: Path) -> None:
    path = tmp_path / DB_FILENAME
    old = Database(path, MIGRATIONS[:8])
    with old.connect() as conn:
        conn.execute(
            "INSERT INTO games (folder, entry, title, added_at, last_played, play_count,"
            " favorite, status) VALUES ('A', 'index.html', 'A', '', '2026-01-01', 3, 1, 'playing'),"
            " ('B', 'index.html', 'B', '', NULL, 0, 0, NULL)"
        )
        conn.execute(
            "INSERT INTO imports (staging, filename, folder, entry, title, created_at)"
            " VALUES ('s', 'f', 'F', 'index.html', 'F', '')"
        )
    db = Database(path)
    admin = db.first_admin()
    assert (admin["id"], admin["username"], admin["password_hash"]) == (1, "admin", "")
    assert db.setup_needed()
    games = {g["folder"]: g for g in db.list_games(admin)}
    assert (games["A"]["play_count"], games["A"]["favorite"], games["A"]["status"]) == (
        3,
        True,
        "playing",
    )
    assert games["A"]["last_played"] == "2026-01-01"
    assert db.owners() == {1: [1], 2: [1]}
    assert db.list_imports()[0]["user_id"] == 1
    # Another account sees none of it
    other = db.create_user("ben", "", "user")
    assert db.list_games(other) == []
