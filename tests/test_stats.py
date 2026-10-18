"""Statistics and disk usage."""

import os
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app import scanner, stats
from app.config import Settings
from app.db import DB_FILENAME, Database
from app.main import create_app

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def write(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)


def game(id: int, title: str, **fields) -> dict:
    return {
        "id": id,
        "title": title,
        "version": "1.0",
        "missing": False,
        "favorite": False,
        "status": None,
        "play_count": 0,
        "last_played": None,
        "size": 0,
        **fields,
    }


def test_folder_size_counts_all_files_but_not_symlinks(tmp_path: Path) -> None:
    write(tmp_path / "game" / "index.html", 100)
    write(tmp_path / "game" / "a" / "b" / "c" / "d" / "deep.png", 1000)
    write(tmp_path / "outside.bin", 5000)
    os.symlink(tmp_path / "outside.bin", tmp_path / "game" / "link.bin")
    assert scanner.folder_size(tmp_path / "game") == 1100
    assert scanner.folder_size(tmp_path / "nothing") == 0


def test_size_is_stored_when_scanning(settings: Settings) -> None:
    write(settings.games_dir / "CK" / "index.html", 300)
    write(settings.games_dir / "CK" / "img" / "a.png", 700)
    with TestClient(create_app(settings)) as client:
        [kettle] = client.get("/api/games").json()
        assert kettle["size"] == 1000

        # A rescan refreshes the value
        write(settings.games_dir / "CK" / "img" / "b.png", 1000)
        assert client.get("/api/games").json()[0]["size"] == 1000
        client.post("/api/scan")
        assert client.get("/api/games").json()[0]["size"] == 2000


def test_compute_rankings() -> None:
    games = [
        game(1, "Alpha", play_count=5, last_played="2026-10-16T10:00:00+00:00", size=100),
        game(2, "Beta", play_count=9, last_played="2026-08-01T10:00:00+00:00", size=300),
        game(3, "Gamma", play_count=5, last_played="2026-01-01T10:00:00+00:00", size=None),
        game(4, "Delta"),
        game(5, "Weg", play_count=50, missing=True, size=999),
        game(6, "Epsilon", favorite=True, status="completed", size=200),
    ]
    backups = [
        {"game_id": 1, "size": 400},
        {"game_id": 1, "size": None},
        {"game_id": 77, "size": 50},  # game entry removed, backup still there
    ]
    result = stats.compute(games, backups, NOW)

    assert result["games"] == 5
    assert result["favorites"] == 1
    assert result["by_status"]["completed"] == 1
    assert result["starts"] == 19
    assert result["size"] == {
        "games": 600,
        "backups": 450,
        "total": 1050,
        "backup_count": 3,
        "unknown": 1,
    }
    # Most starts first, ties by name; missing folders do not count
    assert [g["title"] for g in result["most_played"]] == ["Beta", "Alpha", "Gamma"]
    # More than 30 days ago, oldest first; never played separately
    assert [g["title"] for g in result["long_ago"]] == ["Gamma", "Beta"]
    assert result["long_ago_count"] == 2
    assert [g["title"] for g in result["never_played"]] == ["Delta", "Epsilon"]
    # By disk usage including backups
    by_size = [(g["title"], g["size"], g["backups_size"]) for g in result["by_size"]]
    assert by_size == [
        ("Alpha", 100, 400),
        ("Beta", 300, 0),
        ("Epsilon", 200, 0),
        ("Delta", 0, 0),
        ("Gamma", None, 0),
    ]


def test_compute_empty_library() -> None:
    result = stats.compute([], [], NOW)
    assert result["games"] == 0
    assert result["size"]["total"] == 0
    assert result["most_played"] == result["by_size"] == []


def test_rankings_are_limited() -> None:
    games = [game(i, f"Game {i:02}", play_count=i + 1) for i in range(15)]
    result = stats.compute(games, [], NOW)
    assert len(result["most_played"]) == stats.TOP
    assert len(result["by_size"]) == 15


def test_stats_endpoint(settings: Settings) -> None:
    write(settings.games_dir / "CK" / "index.html", 300)
    with TestClient(create_app(settings)) as client:
        [kettle] = client.get("/api/games").json()
        client.get(f"/play/{kettle['id']}", follow_redirects=False)
        result = client.get("/api/stats").json()
    assert result["games"] == 1
    assert result["size"]["games"] == 300
    assert [(g["title"], g["play_count"]) for g in result["most_played"]] == [("CK", 1)]


def test_missing_backup_sizes_are_filled(settings: Settings) -> None:
    db = Database(settings.data_dir / DB_FILENAME)
    game_row = db.create_game("CK", "index.html", "CK")
    write(settings.backups_dir / "CK" / "1.0" / "index.html", 123)
    db.replace_version(
        game_row["id"],
        folder="CK",
        entry="index.html",
        version="2.0",
        version_hint=None,
        action="update",
        backup={"version": "1.0", "folder": "CK", "entry": "index.html", "path": "CK/1.0"},
    )
    assert db.list_all_backups()[0]["size"] is None
    assert stats.fill_backup_sizes(settings, db) == 1
    assert db.list_all_backups()[0]["size"] == 123
    assert stats.fill_backup_sizes(settings, db) == 0
