"""Scanner tests with made-up folders that mimic typical downloads."""

from pathlib import Path

import pytest

from app.db import DB_FILENAME, Database
from app.scanner import list_html_files, scan, scan_folder, strip_version, version_hint


def make(root: Path, files: dict[str, str | int]) -> Path:
    """Creates files; an int creates a file of that size, a str its content."""
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, int):
            path.write_bytes(b"x" * content)
        else:
            path.write_text(content, encoding="utf-8")
    return root


def page(title: str) -> str:
    return f"<!DOCTYPE html><html><head><title>{title}</title></head><body></body></html>"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Paper Lanterns 1.4.4", "1.4.4"),
        ("Quiet Valley 0_11", "0.11"),
        ("SH 0.2.1", "0.2.1"),
        ("Lumen_v1.3", "1.3"),
        ("StarHarbor0.6.1", "0.6.1"),
        ("OAK v 0.191.0", "0.191.0"),
        ("ClockworkGarden_V263", "263"),
        ("The Keeper's Lantern v19", "19"),
        ("voyage-v0.1.7-northpath-hotfix1", "0.1.7"),
        ("moon_garden_0_4_7", "0.4.7"),
        ("Fractured-0.23.2-pc", "0.23.2"),
        ("Lantern_v0.34", "0.34"),
        ("My Journey (Public)", None),
        ("QXKV0143", None),
        ("archive1115", None),
        ("glass_bridge_release_3", "3"),
        ("Glass Bridge Release 3", "3"),
        ("released", None),
        ("PB", None),
    ],
)
def test_version_hint(name: str, expected: str | None) -> None:
    assert version_hint(name) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Paper Lanterns 1.4.4", "Paper Lanterns"),
        ("Lumen_v1.3", "Lumen"),
        ("voyage-v0.1.7-northpath-hotfix1", "voyage northpath"),
        ("My Journey (Public)", "My Journey"),
        ("OAK v 0.191.0", "OAK"),
    ],
)
def test_strip_version(name: str, expected: str) -> None:
    assert strip_version(name) == expected


def test_index_html_wins_over_bigger_changelog(tmp_path: Path) -> None:
    folder = make(
        tmp_path / "PB", {"changelog.html": 5000, "index.html": page("PB"), "a.html": 9000}
    )
    game = scan_folder(folder)
    assert game is not None
    assert game.entry == "index.html"
    assert game.title == "PB"
    assert "changelog.html" not in game.html_files


def test_index_is_case_insensitive(tmp_path: Path) -> None:
    folder = make(tmp_path / "Lumen_v1.3", {"Index.html": page("Lumen"), "x.html": 99999})
    assert scan_folder(folder).entry == "Index.html"


def test_shallowest_index_wins(tmp_path: Path) -> None:
    folder = make(tmp_path / "G", {"a/b/index.html": "", "a/index.html": "", "z.html": 10})
    assert scan_folder(folder).entry == "a/index.html"


def test_index_two_levels_deep_with_macosx(tmp_path: Path) -> None:
    folder = make(
        tmp_path / "The Keeper's Lantern v19",
        {
            "v19/The Keeper's Lantern v19/index.html": page("The Keeper's Lantern"),
            "__MACOSX/v19/The Keeper's Lantern v19/._index.html": 220,
        },
    )
    game = scan_folder(folder)
    assert game.entry == "v19/The Keeper's Lantern v19/index.html"
    assert game.html_files == ("v19/The Keeper's Lantern v19/index.html",)
    assert game.version_hint == "19"


def test_too_deep_is_not_found(tmp_path: Path) -> None:
    folder = make(tmp_path / "G", {"a/b/c/d/index.html": ""})
    assert scan_folder(folder) is None


def test_similar_name_wins_over_bigger_file(tmp_path: Path) -> None:
    folder = make(
        tmp_path / "Quiet Valley 0_11",
        {"Quiet Valley.html": 100, "bonus.html": 50000, "credits.html": 90000},
    )
    game = scan_folder(folder)
    assert game.entry == "Quiet Valley.html"
    assert game.version_hint == "0.11"


def test_similar_name_without_spaces(tmp_path: Path) -> None:
    folder = make(
        tmp_path / "The orchard", {"TheOrchard.html": 10, "__MACOSX/._TheOrchard.html": 1}
    )
    assert scan_folder(folder).entry == "TheOrchard.html"


def test_similar_to_subfolder_name(tmp_path: Path) -> None:
    folder = make(
        tmp_path / "Lark",
        {"Lantern_v0.34/Lantern.html": 100, "Lantern_v0.34/extra.html": 500},
    )
    game = scan_folder(folder)
    assert game.entry == "Lantern_v0.34/Lantern.html"
    assert game.version_hint == "0.34"


def test_head_html_is_ignored(tmp_path: Path) -> None:
    folder = make(
        tmp_path / "archive1115",
        {"archive1115/head.html": 99999, "archive1115/archive1115.html": 10},
    )
    game = scan_folder(folder)
    assert game.entry == "archive1115/archive1115.html"
    assert game.version_hint is None


def test_largest_file_as_fallback(tmp_path: Path) -> None:
    folder = make(tmp_path / "WTRF", {"Water-for-the-Fields.html": 900, "small.html": 10})
    assert scan_folder(folder).entry == "Water-for-the-Fields.html"


def test_readme_only_is_not_a_game(tmp_path: Path) -> None:
    folder = make(tmp_path / "G", {"readme.html": 10, "README.htm": 10, "credits.html": 1})
    assert scan_folder(folder) is None


def test_renpy_and_exe_folders_are_skipped(tmp_path: Path) -> None:
    renpy = make(tmp_path / "RK-1.0.26-pc", {"RK.exe": 1, "RK.py": 1, "renpy/x.py": 1})
    nwjs = make(
        tmp_path / "Desk story v1.1",
        {"Desk story/Game.exe": 1, "Desk story/index.html": 645},
    )
    assert scan_folder(renpy) is None
    assert scan_folder(nwjs) is None


def test_title_from_html_with_entities_and_whitespace(tmp_path: Path) -> None:
    folder = make(tmp_path / "G", {"index.html": page("\n  Harbor Town USA:\n Hometown &amp; Co ")})
    assert scan_folder(folder).title == "Harbor Town USA: Hometown & Co"


def test_title_fallbacks(tmp_path: Path) -> None:
    named = make(tmp_path / "Moonbeam", {"Moonbeam.html": "<html>no title</html>"})
    index = make(tmp_path / "Paper Lanterns 1.4.4", {"index.html": "<title> </title>"})
    assert scan_folder(named).title == "Moonbeam"
    assert scan_folder(index).title == "Paper Lanterns"


def test_scan_skips_files_hidden_and_macosx(tmp_path: Path) -> None:
    make(
        tmp_path,
        {
            "b game/index.html": page("B"),
            "A game/index.html": page("A"),
            "Ridgetown/RIDGETOWN 11.1.exe": 1,
            "__MACOSX/x/index.html": 1,
            ".hidden/index.html": 1,
            "download.zip": 1,
            "loose.html": 1,
        },
    )
    assert [g.folder for g in scan(tmp_path)] == ["A game", "b game"]


def test_scan_missing_dir(tmp_path: Path) -> None:
    assert scan(tmp_path / "does-not-exist") == []


def test_list_html_files(tmp_path: Path) -> None:
    folder = make(tmp_path / "G", {"index.html": 1, "sub/Other.htm": 1, "changelog.html": 1})
    assert list_html_files(folder) == ["index.html", "sub/Other.htm"]


# Syncing with the database


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "data" / DB_FILENAME)


def test_sync_adds_new_and_keeps_existing_data(tmp_path: Path, db: Database) -> None:
    games = make(tmp_path / "games", {"SH 0.2.1/Silver Harbor.html": page("Silver Harbor")})
    assert db.sync(scan(games)) == {"added": 1, "found": 1, "missing": 0}
    [game] = db.list_games()
    assert (game["title"], game["entry"], game["version_hint"]) == (
        "Silver Harbor",
        "Silver Harbor.html",
        "0.2.1",
    )
    db.update_game(game["id"], {"title": "My title", "version": "0.2.1", "notes": "n"})
    admin = db.first_admin()
    db.mark_played(admin["id"], game["id"])

    assert db.sync(scan(games)) == {"added": 0, "found": 1, "missing": 0}
    [again] = db.list_games(admin)
    assert (again["title"], again["version"], again["notes"]) == ("My title", "0.2.1", "n")
    assert again["play_count"] == 1
    assert again["added_at"] == game["added_at"]


def test_sync_marks_missing_and_recovers(tmp_path: Path, db: Database) -> None:
    games = make(tmp_path / "games", {"A/index.html": "", "B/index.html": ""})
    db.sync(scan(games))
    (games / "B" / "index.html").unlink()
    (games / "B").rmdir()
    assert db.sync(scan(games)) == {"added": 0, "found": 1, "missing": 1}
    assert {g["folder"]: g["missing"] for g in db.list_games()} == {"A": False, "B": True}

    make(games, {"B/index.html": ""})
    db.sync(scan(games))
    assert all(not g["missing"] for g in db.list_games())


def test_sync_keeps_manual_entry_while_it_exists(tmp_path: Path, db: Database) -> None:
    games = make(tmp_path / "games", {"G/index.html": "", "G/other.html": ""})
    db.sync(scan(games))
    [game] = db.list_games()
    db.update_game(game["id"], {"entry": "other.html"})
    db.sync(scan(games))
    assert db.get_game(game["id"])["entry"] == "other.html"

    (games / "G" / "other.html").unlink()
    db.sync(scan(games))
    assert db.get_game(game["id"])["entry"] == "index.html"
