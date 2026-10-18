"""Pending imports: extract archives, suggest a match, confirm or discard.

Until it is confirmed, an import stays extracted in `DATA_DIR/imports/<staging>/<folder>`.
Only confirming moves it into the library; discarding deletes the staging folder.
"""

import contextlib
import os
import shutil
import threading
import uuid
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from app import images, importer, scanner
from app.config import Settings
from app.db import ACTION_NEW, ACTION_RESTORE, ACTION_UPDATE, PLAY_FIELDS, Database

MB = 1024 * 1024

# Installing, discarding and restoring move folders; never two at the same time
library_lock = threading.Lock()


class ImportConflict(RuntimeError):
    """The import cannot be installed in the current state."""


def new_token() -> str:
    return uuid.uuid4().hex


def staging_dir(settings: Settings, imp: dict[str, Any]) -> Path:
    return settings.imports_dir / imp["staging"]


def game_dir(settings: Settings, imp: dict[str, Any]) -> Path:
    return staging_dir(settings, imp) / imp["folder"]


def receive(
    settings: Settings, db: Database, archive: Path, filename: str, user_id: int
) -> dict[str, Any]:
    """Extracts an archive as a new pending import of `user_id`. Raises `ImportRejected`."""
    token = new_token()
    extracted = importer.extract_game(
        archive, settings.imports_dir / token, filename, settings.max_extracted_mb * MB
    )
    size = sum(f.stat().st_size for f in extracted.path.rglob("*") if f.is_file())
    try:
        return db.create_import(
            token, importer.archive_stem(filename), extracted.game, size, user_id
        )
    except BaseException:
        shutil.rmtree(settings.imports_dir / token, ignore_errors=True)
        raise


def cleanup(settings: Settings, db: Database) -> None:
    """Reconciles staging folders and database, for example after a crash while extracting."""
    if not settings.imports_dir.is_dir():
        return
    known = set()
    for imp in db.list_imports():
        if game_dir(settings, imp).is_dir():
            known.add(imp["staging"])
        else:
            db.delete_import(imp["id"])
    for path in settings.imports_dir.iterdir():
        if path.name not in known:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


# Match: "new game" or "update of …"


def _key(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _entry_key(entry: str) -> str:
    stem = PurePosixPath(entry).stem
    return "" if stem.lower() == "index" else _key(stem)


REASONS = {"folder": "same folder name", "title": "same title", "entry": "same start file"}


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


def suggest(imp: dict[str, Any], games: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Game the import is probably an update of, with a reason; otherwise None.

    Weights: same folder name (without version) 3, same title 2, same start file 1
    (an `index.html` does not count, many games have one).
    """
    folder = _key(scanner.strip_version(imp["folder"]))
    title = _key(imp["title"])
    entry = _entry_key(imp["entry"])
    best: tuple[int, dict[str, Any], list[str]] | None = None
    for game in games:
        score, reasons = 0, []
        if folder and folder == _key(scanner.strip_version(game["folder"])):
            score += 3
            reasons.append("folder")
        if title and title == _key(game["title"]):
            score += 2
            reasons.append("title")
        if entry and entry == _entry_key(game["entry"]):
            score += 1
            reasons.append("entry")
        if score and (best is None or score > best[0]):
            best = (score, game, reasons)
    if best is None:
        return None
    _, game, matches = best
    reason = _join([REASONS[match] for match in matches])
    return {
        "game_id": game["id"],
        "title": game["title"],
        "version": game["version"],
        # Codes for the UI, which words them in its own language
        "matches": matches,
        "reason": f'{reason[0].upper()}{reason[1:]} as "{game["title"]}"',
    }


def pending(settings: Settings, db: Database, user: dict[str, Any]) -> list[dict[str, Any]]:
    """The user's pending imports with a suggestion among the games they see and the
    possible start files."""
    games = db.list_games(user)
    result = []
    for imp in db.list_imports(user["id"]):
        result.append(
            {
                **imp,
                "suggestion": suggest(imp, games),
                "html_files": scanner.list_html_files(game_dir(settings, imp)),
            }
        )
    return result


def discard(settings: Settings, db: Database, imp: dict[str, Any]) -> None:
    with library_lock:
        db.delete_import(imp["id"])
        shutil.rmtree(staging_dir(settings, imp), ignore_errors=True)


# Installing into the library


def unique_folder(games_dir: Path, name: str) -> str:
    """`name`, or if taken `name (2)`, `name (3)` …"""
    candidate, number = name, 1
    while (games_dir / candidate).exists():
        number += 1
        candidate = f"{name} ({number})"
    return candidate


def move_into_library(settings: Settings, source: Path, folder: str) -> None:
    """Moves `source` to `GAMES_DIR/<folder>` without a half-copied folder ever being there.

    If staging and games are on different drives, the files are first copied into a
    hidden folder inside the games folder and then renamed in one step.
    """
    incoming = settings.games_dir / f".import-{new_token()}"
    shutil.move(source, incoming)
    try:
        os.rename(incoming, settings.games_dir / folder)
    except OSError:
        shutil.move(incoming, source)
        raise


def _staged_source(settings: Settings, imp: dict[str, Any]) -> Path:
    source = game_dir(settings, imp)
    if not source.is_dir():
        raise ImportConflict("The extracted import no longer exists.")
    return source


def _finish_import(settings: Settings, db: Database, imp: dict[str, Any]) -> None:
    db.delete_import(imp["id"])
    shutil.rmtree(staging_dir(settings, imp), ignore_errors=True)


def install_new(
    settings: Settings,
    db: Database,
    imp: dict[str, Any],
    fields: dict[str, Any],
    entry: str | None = None,
) -> dict[str, Any]:
    """Adds the import as a new game owned by its uploader. `fields` contains at least
    the version; status and favorite are not stored here, they are per user."""
    with library_lock:
        source = _staged_source(settings, imp)
        folder = unique_folder(settings.games_dir, imp["folder"])
        move_into_library(settings, source, folder)
        game = db.create_game(
            folder, entry or imp["entry"], imp["title"], imp["version_hint"], imp["user_id"]
        )
        db.set_size(game["id"], scanner.folder_size(settings.games_dir / folder))
        play = {name: fields[name] for name in PLAY_FIELDS & set(fields)}
        db.set_play_data(imp["user_id"], game["id"], play)
        game_fields = {k: v for k, v in fields.items() if k not in PLAY_FIELDS}
        game = db.update_game(game["id"], game_fields)  # type: ignore[assignment]
        db.add_history(game["id"], game["version"], ACTION_NEW)
        _finish_import(settings, db, imp)
    return images.refresh(settings, db, game)


# Update with backup, restore


def _backup_path(settings: Settings, game: dict[str, Any]) -> Path:
    """Free folder backups/<game>/<version> for the version currently installed."""
    parent = settings.backups_dir / importer.clean_folder_name(game["title"])
    return parent / unique_folder(
        parent, importer.clean_folder_name(game["version"] or "no version")
    )


def _swap(
    settings: Settings,
    game: dict[str, Any],
    source: Path,
    folder: str,
    commit: Callable[[dict[str, Any] | None], dict[str, Any]],
) -> dict[str, Any]:
    """Installs `source` as the game's new folder and keeps the previous one as a backup.

    Order: move the new version, hidden, into the games folder; rename the old version
    aside; rename the new version; move the old one into the backups; database. Both
    renames inside the games folder are instant; if any step fails, all previous steps
    are undone and the old version stays playable unchanged.
    """
    games_dir = settings.games_dir
    current = games_dir / game["folder"]
    token = new_token()
    incoming = games_dir / f".import-{token}"
    parked = games_dir / f".old-{token}"
    undo: list[Callable[[], object]] = []
    try:
        shutil.move(source, incoming)
        undo.append(lambda: shutil.move(incoming, source))
        backup = None
        if current.is_dir():
            os.rename(current, parked)
            undo.append(lambda: os.rename(parked, current))
        target = games_dir / folder
        os.rename(incoming, target)
        undo.append(lambda: os.rename(target, incoming))
        if parked.is_dir():
            destination = _backup_path(settings, game)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(parked, destination)
            undo.append(lambda: shutil.move(destination, parked))
            backup = {
                "version": game["version"],
                "folder": game["folder"],
                "entry": game["entry"],
                "path": destination.relative_to(settings.backups_dir).as_posix(),
                "size": game.get("size"),
            }
        return commit(backup)
    except BaseException:
        for step in reversed(undo):
            with contextlib.suppress(OSError):
                step()
        raise


def _target_folder(settings: Settings, game: dict[str, Any], wanted: str) -> str:
    """New folder name; the game's previous folder becomes free in the process."""
    if wanted == game["folder"]:
        return wanted
    return unique_folder(settings.games_dir, wanted)


def prune_backups(settings: Settings, db: Database, game_id: int) -> None:
    """Deletes the oldest backups so that at most KEEP_BACKUPS remain."""
    for backup in db.list_backups(game_id)[settings.keep_backups :]:
        db.delete_backup(backup["id"])
        shutil.rmtree(settings.backups_dir / backup["path"], ignore_errors=True)


def install_update(
    settings: Settings,
    db: Database,
    imp: dict[str, Any],
    game: dict[str, Any],
    version: str,
    entry: str | None = None,
) -> dict[str, Any]:
    """Replaces a game's files with the import; the old version goes into the backups."""
    with library_lock:
        source = _staged_source(settings, imp)
        if entry is None:
            # Keep a start file chosen by hand if it still exists
            files = scanner.list_html_files(source)
            entry = game["entry"] if game["entry"] in files else imp["entry"]
        folder = _target_folder(settings, game, imp["folder"])

        def commit(backup: dict[str, Any] | None) -> dict[str, Any]:
            return db.replace_version(
                game["id"],
                folder=folder,
                entry=entry,
                version=version,
                version_hint=imp["version_hint"],
                action=ACTION_UPDATE,
                backup=backup,
                size=scanner.folder_size(settings.games_dir / folder),
            )

        updated = _swap(settings, game, source, folder, commit)
        _finish_import(settings, db, imp)
        prune_backups(settings, db, game["id"])
    return images.refresh(settings, db, updated)


def restore(
    settings: Settings, db: Database, game: dict[str, Any], backup: dict[str, Any]
) -> dict[str, Any]:
    """Restores a backup; the current version is backed up in turn."""
    with library_lock:
        source = settings.backups_dir / backup["path"]
        if not source.is_dir():
            raise ImportConflict("The backup folder no longer exists.")
        folder = _target_folder(settings, game, backup["folder"])

        def commit(new_backup: dict[str, Any] | None) -> dict[str, Any]:
            return db.replace_version(
                game["id"],
                folder=folder,
                entry=backup["entry"],
                version=backup["version"],
                version_hint=scanner.version_hint(backup["folder"]),
                action=ACTION_RESTORE,
                backup=new_backup,
                restored_backup_id=backup["id"],
                size=scanner.folder_size(settings.games_dir / folder),
            )

        restored = _swap(settings, game, source, folder, commit)
        prune_backups(settings, db, game["id"])
        # Remove the restored backup's empty parent folder
        with contextlib.suppress(OSError):
            source.parent.rmdir()
    return images.refresh(settings, db, restored)
