import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import DB_FILENAME, MIGRATIONS, Database
from app.main import create_app


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "data" / DB_FILENAME)


def test_database_is_created_in_data_dir_on_startup(settings: Settings) -> None:
    with TestClient(create_app(settings)):
        pass
    db_file = settings.data_dir / DB_FILENAME
    assert db_file.is_file()
    assert Database(db_file).schema_version == len(MIGRATIONS)


def test_create_and_read(db: Database) -> None:
    game = db.create_game("Paper Lanterns 1.4.4", "Paper Lanterns.html", "Paper Lanterns", "1.4.4")
    assert game["id"] > 0
    assert game["folder"] == "Paper Lanterns 1.4.4"
    assert game["version"] is None
    assert game["version_hint"] == "1.4.4"
    assert game["play_count"] == 0
    assert game["missing"] is False
    assert game["added_at"]
    assert db.get_game(game["id"]) == game
    assert db.list_games() == [game]


def test_folder_is_unique(db: Database) -> None:
    db.create_game("CK", "index.html", "CK")
    with pytest.raises(sqlite3.IntegrityError):
        db.create_game("CK", "index.html", "CK")


def test_list_is_sorted_by_title_case_insensitive(db: Database) -> None:
    for title in ("beta", "Alpha", "gamma"):
        db.create_game(title, "index.html", title)
    assert [g["title"] for g in db.list_games()] == ["Alpha", "beta", "gamma"]


def test_update(db: Database) -> None:
    game = db.create_game("CK", "index.html", "CK")
    updated = db.update_game(game["id"], {"version": "0.5", "source": "F95Zone", "notes": "gut"})
    assert updated is not None
    assert (updated["version"], updated["source"], updated["notes"]) == ("0.5", "F95Zone", "gut")
    assert updated["updated_at"]
    assert updated["title"] == "CK"


def test_update_rejects_internal_fields(db: Database) -> None:
    game = db.create_game("CK", "index.html", "CK")
    with pytest.raises(ValueError, match="play_count"):
        db.update_game(game["id"], {"play_count": 99})


def test_update_unknown_game_returns_none(db: Database) -> None:
    assert db.update_game(404, {"version": "1"}) is None


def test_mark_played_and_missing(db: Database) -> None:
    admin = db.first_admin()
    game = db.create_game("CK", "index.html", "CK")
    db.mark_played(admin["id"], game["id"])
    db.mark_played(admin["id"], game["id"])
    db.set_missing(game["id"])
    game = db.get_game(game["id"], admin)
    assert game["play_count"] == 2
    assert game["last_played"]
    assert game["missing"] is True


def test_delete(db: Database) -> None:
    game = db.create_game("CK", "index.html", "CK")
    assert db.delete_game(game["id"]) is True
    assert db.get_game(game["id"]) is None
    assert db.delete_game(game["id"]) is False


def test_migrations_run_once_and_keep_data(tmp_path: Path) -> None:
    path = tmp_path / DB_FILENAME
    db = Database(path)
    db.create_game("CK", "index.html", "CK")
    assert db.schema_version == len(MIGRATIONS)

    # A later change adds a field
    extended = [*MIGRATIONS, "ALTER TABLE games ADD COLUMN rating INTEGER NOT NULL DEFAULT 0"]
    db = Database(path, extended)
    assert db.schema_version == len(extended)
    [game] = db.list_games()
    assert game["title"] == "CK"
    assert game["rating"] == 0

    # Opening again runs nothing twice (ALTER TABLE would fail otherwise)
    assert Database(path, extended).schema_version == len(extended)


def test_failed_migration_is_rolled_back(tmp_path: Path) -> None:
    path = tmp_path / DB_FILENAME
    Database(path)
    broken = [*MIGRATIONS, "ALTER TABLE games ADD COLUMN a TEXT; BROKEN"]
    with pytest.raises(sqlite3.Error):
        Database(path, broken)
    db = Database(path)
    assert db.schema_version == len(MIGRATIONS)
    with db.connect() as conn:
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(games)")]
    assert "a" not in columns


def test_migration_turns_german_values_into_codes(tmp_path: Path) -> None:
    path = tmp_path / DB_FILENAME
    old = Database(path, MIGRATIONS[:7])
    with old.connect() as conn:
        conn.execute(
            "INSERT INTO games (folder, entry, title, added_at, status, source)"
            " VALUES ('A', 'index.html', 'A', '', 'Warte auf Update', 'Sonstige'),"
            " ('B', 'index.html', 'B', '', 'Abgeschlossen', 'itch.io')"
        )
        conn.executemany(
            "INSERT INTO game_history (game_id, version, action, at) VALUES (1, '1', ?, '')",
            [("neu",), ("update",), ("wiederhergestellt",), ("eingetragen",)],
        )
        conn.execute(
            "INSERT INTO update_checks (game_id, url, status, checked_at)"
            " VALUES (1, 'https://a.org', 'manuell', ''), (2, 'https://b.org', 'fehler', '')"
        )
    db = Database(path)
    assert [(g["status"], g["source"]) for g in db.list_games(db.first_admin())] == [
        ("waiting", "Other"),
        ("completed", "itch.io"),
    ]
    assert sorted(h["action"] for h in db.list_history(1)) == [
        "edited",
        "new",
        "restored",
        "update",
    ]
    with db.connect() as conn:
        statuses = [row[0] for row in conn.execute("SELECT status FROM update_checks")]
    assert sorted(statuses) == ["error", "manual"]
