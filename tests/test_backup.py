import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app.backup import (
    KEEP,
    backups_dir,
    create_backup,
    daily_backup,
    list_backups,
    prune_backups,
)
from app.db import DB_FILENAME, Database


def make_db(path: Path) -> Database:
    db = Database(path)
    db.create_game(folder="Game 1_0", entry="index.html", title="Game")
    return db


def test_backup_is_consistent_copy(tmp_path: Path) -> None:
    make_db(tmp_path / DB_FILENAME)
    target = create_backup(tmp_path / DB_FILENAME, tmp_path / "backups")

    assert target.name.startswith("library-") and target.suffix == ".db"
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT title FROM games").fetchall() == [("Game",)]
    finally:
        conn.close()
    assert not list((tmp_path / "backups").glob("*.tmp"))


def test_backup_restores_into_working_database(tmp_path: Path) -> None:
    db = make_db(tmp_path / DB_FILENAME)
    backup = create_backup(db.path, tmp_path / "backups")
    db.delete_game(db.list_games()[0]["id"])

    backup.replace(db.path)  # restore as described in docs/INSTALL.md

    assert [g["title"] for g in Database(db.path).list_games()] == ["Game"]


def test_prune_keeps_newest(tmp_path: Path) -> None:
    make_db(tmp_path / DB_FILENAME)
    start = datetime(2026, 1, 1, 3, 0, 0)
    for day in range(KEEP + 3):
        create_backup(tmp_path / DB_FILENAME, tmp_path / "backups", start + timedelta(days=day))

    removed = prune_backups(tmp_path / "backups")

    remaining = list_backups(tmp_path / "backups")
    assert len(removed) == 3 and len(remaining) == KEEP
    assert remaining[0].name == "library-20260104-030000.db"
    assert remaining[-1].name == "library-20260117-030000.db"


def test_daily_backup_once_per_day(settings) -> None:
    make_db(settings.data_dir / DB_FILENAME)
    morning = datetime(2026, 10, 16, 8, 0, 0)

    first = daily_backup(settings, morning)
    again = daily_backup(settings, morning + timedelta(hours=5))
    next_day = daily_backup(settings, morning + timedelta(days=1))

    assert first is not None and again is None and next_day is not None
    assert len(list_backups(backups_dir(settings))) == 2


def test_daily_backup_without_database(settings) -> None:
    assert daily_backup(settings) is None
    assert list_backups(backups_dir(settings)) == []


def test_app_creates_backup_on_start(settings, client) -> None:
    client.get("/api/health")
    assert len(list_backups(backups_dir(settings))) == 1
