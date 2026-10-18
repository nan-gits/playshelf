"""Backups of the SQLite database.

The backup uses SQLite's backup API and is therefore consistent even while the
app is running. Backups are stored in `DATA_DIR/db-backups` as
`library-YYYYMMDD-HHMMSS.db`; only the newest `KEEP` are kept.

The app creates one backup per day on its own (checked at startup and then every
hour). By hand: `python -m app.backup`, or in the container
`docker exec <container> python -m app.backup`.
"""

import asyncio
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from app.config import Settings, get_settings
from app.db import DB_FILENAME

KEEP = 14
PREFIX = "library-"
SUFFIX = ".db"
CHECK_INTERVAL = 3600  # seconds

log = logging.getLogger(__name__)


def backups_dir(settings: Settings) -> Path:
    return settings.data_dir / "db-backups"


def list_backups(directory: Path) -> list[Path]:
    """Existing backups, oldest first (the timestamp in the name sorts correctly)."""
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob(f"{PREFIX}*{SUFFIX}") if p.is_file())


def create_backup(db_path: Path, directory: Path, when: datetime | None = None) -> Path:
    """Writes a consistent copy of the database and returns its path."""
    when = when or datetime.now()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{PREFIX}{when:%Y%m%d-%H%M%S}{SUFFIX}"
    partial = target.with_suffix(".tmp")
    source = sqlite3.connect(db_path)
    try:
        dest = sqlite3.connect(partial)
        try:
            source.backup(dest)
        finally:
            dest.close()
    finally:
        source.close()
    # Rename only once fully written: half-written files never count as a backup
    partial.replace(target)
    return target


def prune_backups(directory: Path, keep: int = KEEP) -> list[Path]:
    """Deletes all but the newest `keep` backups; returns the deleted ones."""
    backups = list_backups(directory)
    removed = backups[:-keep] if keep > 0 else backups
    for path in removed:
        path.unlink()
    return removed


def has_backup_for(directory: Path, day: datetime) -> bool:
    return any(p.name.startswith(f"{PREFIX}{day:%Y%m%d}-") for p in list_backups(directory))


def daily_backup(settings: Settings, now: datetime | None = None) -> Path | None:
    """Creates the day's backup if there is none yet and removes old ones."""
    now = now or datetime.now()
    db_path = settings.data_dir / DB_FILENAME
    directory = backups_dir(settings)
    if not db_path.is_file() or has_backup_for(directory, now):
        return None
    path = create_backup(db_path, directory, now)
    prune_backups(directory)
    log.info("Database backup created: %s", path)
    return path


def try_daily_backup(settings: Settings) -> None:
    """Like `daily_backup`, but a failed backup does not stop the app."""
    try:
        daily_backup(settings)
    except Exception:
        log.exception("Database backup failed")


async def run_daily_backups(settings: Settings) -> None:
    """Background task of the app: checks every hour whether the day's backup is missing."""
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        await asyncio.to_thread(try_daily_backup, settings)


def main() -> None:
    """Immediate backup by hand, independent of the daily backup."""
    settings = get_settings()
    db_path = settings.data_dir / DB_FILENAME
    if not db_path.is_file():
        raise SystemExit(f"No database found: {db_path}")
    path = create_backup(db_path, backups_dir(settings))
    prune_backups(backups_dir(settings))
    print(f"Backup created: {path}")


if __name__ == "__main__":
    main()
