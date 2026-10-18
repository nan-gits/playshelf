"""Simple statistics: most played, not played for a long time, disk usage.

Disk usage is computed when scanning, importing and restoring and stored in the
database; the statistics themselves only use stored values.
"""

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from app import scanner
from app.config import Settings
from app.db import STATUSES, Database

# Number of entries in the rankings
TOP = 10
# After this many days without a start a game counts as "not played for a long time"
LONG_AGO_DAYS = 30


def fill_backup_sizes(settings: Settings, db: Database) -> int:
    """Computes missing backup sizes (for example of backups older than this feature)."""
    filled = 0
    for backup in db.list_all_backups():
        if backup["size"] is not None:
            continue
        path = settings.backups_dir / backup["path"]
        if path.is_dir():
            db.set_backup_size(backup["id"], scanner.folder_size(path))
            filled += 1
    return filled


def _entry(game: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"id": game["id"], "title": game["title"], "version": game["version"], **extra}


def compute(
    games: Iterable[dict[str, Any]],
    backups: Iterable[dict[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Statistics over all games whose folder exists."""
    now = now or datetime.now(UTC)
    games = [g for g in games if not g["missing"]]

    backup_size: dict[int, int] = {}
    backup_count: dict[int, int] = {}
    for backup in backups:
        game_id = backup["game_id"]
        backup_size[game_id] = backup_size.get(game_id, 0) + (backup["size"] or 0)
        backup_count[game_id] = backup_count.get(game_id, 0) + 1

    played = [g for g in games if g["play_count"]]
    most_played = sorted(played, key=lambda g: (-g["play_count"], g["title"].lower()))[:TOP]

    cutoff = now - timedelta(days=LONG_AGO_DAYS)
    long_ago = [
        g for g in games if g["last_played"] and datetime.fromisoformat(g["last_played"]) < cutoff
    ]
    long_ago.sort(key=lambda g: g["last_played"])
    never = sorted((g for g in games if not g["last_played"]), key=lambda g: g["title"].lower())

    by_size = sorted(
        games,
        key=lambda g: (-((g["size"] or 0) + backup_size.get(g["id"], 0)), g["title"].lower()),
    )

    games_size = sum(g["size"] or 0 for g in games)
    # Backups of games whose folder is missing or whose entry was removed count too
    backups_size = sum(backup_size.values())
    return {
        "games": len(games),
        "favorites": sum(1 for g in games if g.get("favorite")),
        "by_status": {
            status: sum(1 for g in games if g.get("status") == status) for status in STATUSES
        },
        "starts": sum(g["play_count"] for g in games),
        "size": {
            "games": games_size,
            "backups": backups_size,
            "total": games_size + backups_size,
            "backup_count": sum(backup_count.values()),
            "unknown": sum(1 for g in games if g["size"] is None),
        },
        "most_played": [
            _entry(g, play_count=g["play_count"], last_played=g["last_played"]) for g in most_played
        ],
        "long_ago_days": LONG_AGO_DAYS,
        "long_ago": [_entry(g, last_played=g["last_played"]) for g in long_ago[:TOP]],
        "long_ago_count": len(long_ago),
        "never_played": [_entry(g) for g in never[:TOP]],
        "never_played_count": len(never),
        "by_size": [
            _entry(
                g,
                size=g["size"],
                backups_size=backup_size.get(g["id"], 0),
                backups=backup_count.get(g["id"], 0),
            )
            for g in by_size
        ],
    }
