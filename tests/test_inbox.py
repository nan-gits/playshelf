import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import DB_FILENAME, Database
from app.inbox import InboxWatcher
from app.main import create_app


def zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:  # uncompressed, so the file is large
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


GAME = zip_bytes({"New Game 0.2/index.html": "<title>New Game</title>" + "x" * 5000})


@pytest.fixture
def watcher(settings: Settings) -> InboxWatcher:
    settings.inbox_settle_seconds = 10
    settings.ensure_directories()
    return InboxWatcher(settings, Database(settings.data_dir / DB_FILENAME))


def test_waits_until_copy_is_finished(watcher: InboxWatcher, settings: Settings) -> None:
    target = settings.inbox_dir / "New Game 0.2.zip"
    # copying: the file grows over several polls
    assert len(GAME) > 3000
    target.write_bytes(GAME[:1000])
    assert watcher.scan_once(now=0) == []
    with target.open("ab") as file:
        file.write(GAME[1000:2000])
    assert watcher.scan_once(now=8) == []
    with target.open("ab") as file:
        file.write(GAME[2000:])
    assert watcher.scan_once(now=16) == []
    # processed only after 10 seconds without change
    assert watcher.scan_once(now=25) == []
    assert watcher.scan_once(now=26) == [target]

    assert not target.exists()
    assert (settings.inbox_dir / "processed" / "New Game 0.2.zip").read_bytes() == GAME
    pending = watcher.db.list_imports()
    assert [(i["title"], i["folder"], i["version_hint"]) for i in pending] == [
        ("New Game", "New Game 0.2", "0.2")
    ]


def test_rejected_archive_goes_to_error_folder(watcher: InboxWatcher, settings: Settings) -> None:
    (settings.inbox_dir / "Game.zip").write_bytes(zip_bytes({"S/S.exe": "MZ", "S/index.html": ""}))
    (settings.inbox_dir / "broken.zip").write_bytes(b"not a zip")
    watcher.scan_once(now=0)
    assert len(watcher.scan_once(now=10)) == 2
    failed = settings.inbox_dir / "failed"
    assert sorted(p.name for p in failed.iterdir()) == [
        "Game.zip",
        "Game.zip.txt",
        "broken.zip",
        "broken.zip.txt",
    ]
    assert "Windows program" in (failed / "Game.zip.txt").read_text(encoding="utf-8")
    assert "not a valid ZIP" in (failed / "broken.zip.txt").read_text(encoding="utf-8")
    assert watcher.db.list_imports() == []
    assert list(settings.imports_dir.iterdir()) == []


def test_ignores_other_files_and_subfolders(watcher: InboxWatcher, settings: Settings) -> None:
    (settings.inbox_dir / "notiz.txt").write_text("x")
    (settings.inbox_dir / ".versteckt.zip").write_bytes(GAME)
    (settings.inbox_dir / "~kopie.zip").write_bytes(GAME)
    (settings.inbox_dir / "leer.zip").write_bytes(b"")
    (settings.inbox_dir / "processed").mkdir()
    (settings.inbox_dir / "processed" / "alt.zip").write_bytes(GAME)
    watcher.scan_once(now=0)
    assert watcher.scan_once(now=100) == []
    assert watcher.db.list_imports() == []


def test_other_archives_go_to_error_folder(watcher: InboxWatcher, settings: Settings) -> None:
    (settings.inbox_dir / "Game v1.3.rar").write_bytes(b"Rar!")
    (settings.inbox_dir / "Game.7z").write_bytes(b"7z")
    watcher.scan_once(now=0)
    assert len(watcher.scan_once(now=10)) == 2
    failed = settings.inbox_dir / "failed"
    assert sorted(p.name for p in failed.iterdir()) == [
        "Game v1.3.rar",
        "Game v1.3.rar.txt",
        "Game.7z",
        "Game.7z.txt",
    ]
    assert "Only ZIP files" in (failed / "Game v1.3.rar.txt").read_text(encoding="utf-8")
    assert watcher.db.list_imports() == []


def test_same_name_again_does_not_overwrite(watcher: InboxWatcher, settings: Settings) -> None:
    for round_ in range(2):
        (settings.inbox_dir / "Game.zip").write_bytes(GAME)
        watcher.scan_once(now=round_ * 100)
        watcher.scan_once(now=round_ * 100 + 10)
    done = sorted(p.name for p in (settings.inbox_dir / "processed").iterdir())
    assert len(done) == 2
    assert "Game.zip" in done
    other = next(name for name in done if name != "Game.zip")
    assert other.startswith("Game 20") and other.endswith(".zip")
    assert len(watcher.db.list_imports()) == 2


def test_file_removed_while_waiting_is_forgotten(watcher: InboxWatcher, settings: Settings) -> None:
    target = settings.inbox_dir / "Game.zip"
    target.write_bytes(GAME)
    watcher.scan_once(now=0)
    target.unlink()
    assert watcher.scan_once(now=20) == []
    assert watcher.seen == {}


def test_unexpected_error_is_not_retried_forever(
    watcher: InboxWatcher, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args):
        raise OSError("Disk full")

    monkeypatch.setattr("app.inbox.imports.receive", boom)
    (settings.inbox_dir / "Game.zip").write_bytes(GAME)
    watcher.scan_once(now=0)
    watcher.scan_once(now=10)
    reason = (settings.inbox_dir / "failed" / "Game.zip.txt").read_text(encoding="utf-8")
    assert "Disk full" in reason


def test_files_present_at_startup_are_imported(settings: Settings) -> None:
    """Files already present are picked up after a restart too."""
    settings.inbox_poll_seconds = 0.05
    settings.inbox_settle_seconds = 0.1
    settings.ensure_directories()
    (settings.inbox_dir / "New Game 0.2.zip").write_bytes(GAME)
    with TestClient(create_app(settings)) as client:
        deadline = time.monotonic() + 5
        while not client.get("/api/imports").json() and time.monotonic() < deadline:
            time.sleep(0.05)
        imports = client.get("/api/imports").json()
    assert [i["title"] for i in imports] == ["New Game"]
    assert (settings.inbox_dir / "processed" / "New Game 0.2.zip").is_file()


def test_watcher_can_be_switched_off(settings: Settings, tmp_path: Path) -> None:
    settings.inbox_poll_seconds = 0
    settings.ensure_directories()
    (settings.inbox_dir / "Game.zip").write_bytes(GAME)
    with TestClient(create_app(settings)) as client:
        time.sleep(0.2)
        assert client.get("/api/imports").json() == []
