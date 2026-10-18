"""SQLite database of the game library.

The schema version is kept in `PRAGMA user_version`. Each migration in `MIGRATIONS`
raises the version by one; on open only the missing ones run. New migrations are
appended at the end; existing ones are never changed.
"""

import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.scanner import FoundGame, resolve_image

DB_FILENAME = "library.db"

SOURCES = ["F95Zone", "itch.io", "Patreon", "TFGames", "Other"]
# Adult game sites; only offered with ADULT_SOURCES=true
ADULT_SOURCES = {"F95Zone", "TFGames"}

# Game status codes; no status = not sorted yet. The UI shows translated labels.
STATUSES = ["playing", "completed", "dropped", "waiting"]

MIGRATIONS: list[str] = [
    # 1: games table
    """
    CREATE TABLE games (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        folder        TEXT NOT NULL UNIQUE,
        entry         TEXT NOT NULL,
        title         TEXT NOT NULL,
        version       TEXT,
        version_hint  TEXT,
        source        TEXT,
        update_url    TEXT,
        notes         TEXT,
        added_at      TEXT NOT NULL,
        updated_at    TEXT,
        last_played   TEXT,
        play_count    INTEGER NOT NULL DEFAULT 0,
        missing       INTEGER NOT NULL DEFAULT 0
    );
    """,
    # 2: pending imports (extracted into the staging folder, not confirmed yet)
    """
    CREATE TABLE imports (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        staging       TEXT NOT NULL UNIQUE,
        filename      TEXT NOT NULL,
        folder        TEXT NOT NULL,
        entry         TEXT NOT NULL,
        title         TEXT NOT NULL,
        version_hint  TEXT,
        size          INTEGER NOT NULL DEFAULT 0,
        created_at    TEXT NOT NULL
    );
    """,
    # 3: version history and backups of replaced versions
    """
    CREATE TABLE game_history (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id  INTEGER NOT NULL,
        version  TEXT,
        action   TEXT NOT NULL,
        at       TEXT NOT NULL
    );
    CREATE INDEX game_history_game ON game_history (game_id);
    CREATE TABLE game_backups (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        game_id     INTEGER NOT NULL,
        version     TEXT,
        folder      TEXT NOT NULL,
        entry       TEXT NOT NULL,
        path        TEXT NOT NULL UNIQUE,
        created_at  TEXT NOT NULL
    );
    CREATE INDEX game_backups_game ON game_backups (game_id);
    """,
    # 4: latest online update check per game
    """
    CREATE TABLE update_checks (
        game_id     INTEGER PRIMARY KEY,
        url         TEXT NOT NULL,
        status      TEXT NOT NULL,
        version     TEXT,
        date        TEXT,
        error       TEXT,
        checked_at  TEXT NOT NULL
    );
    """,
    # 5: game status and favourites
    """
    ALTER TABLE games ADD COLUMN status TEXT;
    ALTER TABLE games ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0;
    """,
    # 6: disk usage in bytes, NULL = not computed yet
    """
    ALTER TABLE games ADD COLUMN size INTEGER;
    ALTER TABLE game_backups ADD COLUMN size INTEGER;
    """,
    # 7: cover image: mode auto | file | upload | none, path relative to the game folder
    """
    ALTER TABLE games ADD COLUMN image TEXT;
    ALTER TABLE games ADD COLUMN image_mode TEXT NOT NULL DEFAULT 'auto';
    """,
    # 8: language-neutral codes instead of German words for stored values
    """
    UPDATE games SET status = CASE status
        WHEN 'Spiele gerade' THEN 'playing'
        WHEN 'Abgeschlossen' THEN 'completed'
        WHEN 'Abgebrochen' THEN 'dropped'
        WHEN 'Warte auf Update' THEN 'waiting'
        ELSE status END;
    UPDATE games SET source = 'Other' WHERE source = 'Sonstige';
    UPDATE game_history SET action = CASE action
        WHEN 'neu' THEN 'new'
        WHEN 'wiederhergestellt' THEN 'restored'
        WHEN 'eingetragen' THEN 'edited'
        ELSE action END;
    UPDATE update_checks SET status = CASE status
        WHEN 'fehler' THEN 'error'
        WHEN 'manuell' THEN 'manual'
        ELSE status END;
    """,
    # 9: user accounts. Account 1 is the first admin; it starts without a password
    # (set from PASSWORD_HASH or on the setup page) and keeps all existing data.
    # Play data moves from the games table into play_data, one row per user and game.
    """
    CREATE TABLE users (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        username       TEXT NOT NULL UNIQUE,
        password_hash  TEXT NOT NULL DEFAULT '',
        role           TEXT NOT NULL DEFAULT 'user',
        language       TEXT,
        created_at     TEXT NOT NULL
    );
    INSERT INTO users (id, username, role, created_at)
        VALUES (1, 'admin', 'admin', strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now'));
    CREATE TABLE game_owners (
        game_id  INTEGER NOT NULL,
        user_id  INTEGER NOT NULL,
        PRIMARY KEY (game_id, user_id)
    );
    CREATE INDEX game_owners_user ON game_owners (user_id);
    CREATE TABLE play_data (
        user_id      INTEGER NOT NULL,
        game_id      INTEGER NOT NULL,
        last_played  TEXT,
        play_count   INTEGER NOT NULL DEFAULT 0,
        favorite     INTEGER NOT NULL DEFAULT 0,
        status       TEXT,
        PRIMARY KEY (user_id, game_id)
    );
    INSERT INTO game_owners (game_id, user_id) SELECT id, 1 FROM games;
    INSERT INTO play_data (user_id, game_id, last_played, play_count, favorite, status)
        SELECT 1, id, last_played, play_count, favorite, status FROM games
        WHERE last_played IS NOT NULL OR play_count > 0 OR favorite OR status IS NOT NULL;
    ALTER TABLE games DROP COLUMN last_played;
    ALTER TABLE games DROP COLUMN play_count;
    ALTER TABLE games DROP COLUMN favorite;
    ALTER TABLE games DROP COLUMN status;
    ALTER TABLE imports ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1;
    """,
]

# Account roles: admins see every game and manage accounts
ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = (ROLE_ADMIN, ROLE_USER)

# Per-user fields of a game, stored in play_data
PLAY_FIELDS = {"status", "favorite"}

# A game together with the play data of one user (user id 0 = nobody: empty play data).
# The visibility condition takes two parameters: "is admin" and the user id.
_GAME_SELECT = (
    "SELECT g.*, p.last_played, COALESCE(p.play_count, 0) AS play_count,"
    " COALESCE(p.favorite, 0) AS favorite, p.status"
    " FROM games g LEFT JOIN play_data p ON p.game_id = g.id AND p.user_id = ?"
)
_VISIBLE = "(? OR EXISTS (SELECT 1 FROM game_owners o WHERE o.game_id = g.id AND o.user_id = ?))"

# Cover image: picked automatically, chosen from the game folder, uploaded, none
IMAGE_MODES = ("auto", "file", "upload", "none")

# Version history entries
ACTION_NEW = "new"
ACTION_UPDATE = "update"
ACTION_RESTORE = "restored"
ACTION_EDIT = "edited"

# Fields that may be changed through the API or by hand
EDITABLE_FIELDS = {
    "entry",
    "title",
    "version",
    "source",
    "update_url",
    "notes",
}


def available_sources(adult_sources: bool) -> list[str]:
    """Sources offered in the UI. Stored values of hidden sources stay valid."""
    return [s for s in SOURCES if adult_sources or s not in ADULT_SOURCES]


def now() -> str:
    """Current time as ISO 8601 in UTC, to the second."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path, migrations: Sequence[str] = MIGRATIONS) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate(migrations)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # commit on success, rollback on error
                yield conn
        finally:
            conn.close()

    @property
    def schema_version(self) -> int:
        with self.connect() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self, migrations: Sequence[str]) -> None:
        """Runs all migrations not applied yet, each in its own transaction."""
        current = self.schema_version
        for version, script in enumerate(migrations[current:], start=current + 1):
            conn = sqlite3.connect(self.path)
            try:
                # executescript commits on its own; BEGIN/COMMIT in the script makes it atomic
                conn.executescript(f"BEGIN; {script}; PRAGMA user_version = {version}; COMMIT;")
            except sqlite3.Error:
                conn.rollback()
                raise
            finally:
                conn.close()

    # Reading

    def list_games(self, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Games the user may see, with that user's play data; without a user all games."""
        query, params = _games_query(user)
        with self.connect() as conn:
            rows = conn.execute(f"{query} ORDER BY g.title COLLATE NOCASE", params).fetchall()
        return [_to_dict(row) for row in rows]

    def get_game(self, game_id: int, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """The game, or None if it does not exist or the user may not see it."""
        query, params = _games_query(user)
        with self.connect() as conn:
            row = conn.execute(f"{query} AND g.id = ?", (*params, game_id)).fetchone()
        return _to_dict(row) if row else None

    def can_see_folder(self, user: dict[str, Any], folder: str) -> bool:
        """Whether the user may open files of the game in `folder`."""
        if user["role"] == ROLE_ADMIN:
            return True
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM games g JOIN game_owners o ON o.game_id = g.id"
                " WHERE g.folder = ? AND o.user_id = ?",
                (folder, user["id"]),
            ).fetchone()
        return row is not None

    # Writing

    def create_game(
        self,
        folder: str,
        entry: str,
        title: str,
        version_hint: str | None = None,
        owner_id: int | None = None,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO games (folder, entry, title, version_hint, added_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (folder, entry, title, version_hint, now()),
            )
            game_id = cur.lastrowid
            if owner_id is not None:
                conn.execute(
                    "INSERT INTO game_owners (game_id, user_id) VALUES (?, ?)", (game_id, owner_id)
                )
        return self.get_game(game_id)  # type: ignore[return-value]

    def update_game(self, game_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        unknown = set(fields) - EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"Fields that cannot be changed: {', '.join(sorted(unknown))}")
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            with self.connect() as conn:
                conn.execute(
                    f"UPDATE games SET {assignments}, updated_at = ? WHERE id = ?",
                    (*fields.values(), now(), game_id),
                )
        return self.get_game(game_id)

    def delete_game(self, game_id: int) -> bool:
        """Removes the game and its history; backup folders on disk are kept."""
        with self.connect() as conn:
            cur = conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
            conn.execute("DELETE FROM game_history WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM game_backups WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM update_checks WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM game_owners WHERE game_id = ?", (game_id,))
            conn.execute("DELETE FROM play_data WHERE game_id = ?", (game_id,))
        return cur.rowcount > 0

    # Play data of one user

    def mark_played(self, user_id: int, game_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO play_data (user_id, game_id, last_played, play_count)"
                " VALUES (?, ?, ?, 1) ON CONFLICT (user_id, game_id) DO UPDATE SET"
                " last_played = excluded.last_played, play_count = play_count + 1",
                (user_id, game_id, now()),
            )

    def set_play_data(self, user_id: int, game_id: int, fields: dict[str, Any]) -> None:
        """Sets status and/or favorite of a game for one user."""
        unknown = set(fields) - PLAY_FIELDS
        if unknown:
            raise ValueError(f"Fields that cannot be changed: {', '.join(sorted(unknown))}")
        if not fields:
            return
        names = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        updates = ", ".join(f"{name} = excluded.{name}" for name in fields)
        with self.connect() as conn:
            conn.execute(
                f"INSERT INTO play_data (user_id, game_id, {names}) VALUES (?, ?, {marks})"
                f" ON CONFLICT (user_id, game_id) DO UPDATE SET {updates}",
                (user_id, game_id, *fields.values()),
            )

    # Owners: who may see a game. Admins see every game, owners or not.

    def owners(self) -> dict[int, list[int]]:
        """User ids per game id."""
        result: dict[int, list[int]] = {}
        with self.connect() as conn:
            for row in conn.execute("SELECT * FROM game_owners ORDER BY user_id"):
                result.setdefault(row["game_id"], []).append(row["user_id"])
        return result

    def set_owners(self, game_id: int, user_ids: Iterable[int]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM game_owners WHERE game_id = ?", (game_id,))
            conn.executemany(
                "INSERT INTO game_owners (game_id, user_id) VALUES (?, ?)",
                [(game_id, user_id) for user_id in sorted(set(user_ids))],
            )

    def set_size(self, game_id: int, size: int | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE games SET size = ? WHERE id = ?", (size, game_id))

    def set_image(self, game_id: int, mode: str, image: str | None) -> dict[str, Any] | None:
        if mode not in IMAGE_MODES:
            raise ValueError(f"Unknown image mode: {mode}")
        with self.connect() as conn:
            conn.execute(
                "UPDATE games SET image_mode = ?, image = ?, updated_at = ? WHERE id = ?",
                (mode, image, now(), game_id),
            )
        return self.get_game(game_id)

    def set_missing(self, game_id: int, missing: bool = True) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE games SET missing = ? WHERE id = ?", (int(missing), game_id))

    def sync(self, found: Iterable[FoundGame]) -> dict[str, int]:
        """Brings the database in line with a scan result.

        New folders are added, vanished ones are marked as missing. For known
        folders, data maintained by hand stays untouched; only the version hint,
        the disk usage and an automatically picked cover image are refreshed, and
        the start file if the previous one no longer exists.
        """
        found = list(found)
        added = 0
        with self.connect() as conn:
            known = {row["folder"]: row for row in conn.execute("SELECT * FROM games")}
            for game in found:
                row = known.get(game.folder)
                if row is None:
                    conn.execute(
                        "INSERT INTO games"
                        " (folder, entry, title, version_hint, size, image, added_at)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            game.folder,
                            game.entry,
                            game.title,
                            game.version_hint,
                            game.size,
                            game.image,
                            now(),
                        ),
                    )
                    added += 1
                    continue
                entry = row["entry"] if row["entry"] in game.html_files else game.entry
                mode, image = resolve_image(
                    row["image_mode"], row["image"], game.image, game.images
                )
                conn.execute(
                    "UPDATE games SET entry = ?, version_hint = ?, size = ?, image_mode = ?,"
                    " image = ?, missing = 0 WHERE id = ?",
                    (entry, game.version_hint, game.size, mode, image, row["id"]),
                )
            seen = {game.folder for game in found}
            gone = [folder for folder in known if folder not in seen]
            conn.executemany("UPDATE games SET missing = 1 WHERE folder = ?", [(f,) for f in gone])
        return {"added": added, "found": len(found), "missing": len(gone)}

    # Pending imports

    def create_import(
        self,
        staging: str,
        filename: str,
        game: FoundGame,
        size: int,
        user_id: int,
    ) -> dict[str, Any]:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO imports (staging, filename, folder, entry, title, version_hint,"
                " size, created_at, user_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    staging,
                    filename,
                    game.folder,
                    game.entry,
                    game.title,
                    game.version_hint,
                    size,
                    now(),
                    user_id,
                ),
            )
            import_id = cur.lastrowid
        return self.get_import(import_id)  # type: ignore[return-value]

    def list_imports(self, user_id: int | None = None) -> list[dict[str, Any]]:
        """Pending imports of one user, or of everyone."""
        where, params = ("WHERE user_id = ?", (user_id,)) if user_id is not None else ("", ())
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM imports {where} ORDER BY created_at, id", params
            ).fetchall()
        return [dict(row) for row in rows]

    def get_import(self, import_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM imports WHERE id = ?", (import_id,)).fetchone()
        return dict(row) if row else None

    def delete_import(self, import_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM imports WHERE id = ?", (import_id,))

    # Version history and backups

    def add_history(self, game_id: int, version: str | None, action: str) -> None:
        with self.connect() as conn:
            _insert_history(conn, game_id, version, action)

    def list_history(self, game_id: int) -> list[dict[str, Any]]:
        """Newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT version, action, at FROM game_history WHERE game_id = ?"
                " ORDER BY at DESC, id DESC",
                (game_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_backups(self, game_id: int) -> list[dict[str, Any]]:
        """Newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM game_backups WHERE game_id = ? ORDER BY created_at DESC, id DESC",
                (game_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_all_backups(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM game_backups ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def set_backup_size(self, backup_id: int, size: int | None) -> None:
        with self.connect() as conn:
            conn.execute("UPDATE game_backups SET size = ? WHERE id = ?", (size, backup_id))

    def get_backup(self, backup_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM game_backups WHERE id = ?", (backup_id,)).fetchone()
        return dict(row) if row else None

    def delete_backup(self, backup_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM game_backups WHERE id = ?", (backup_id,))

    def replace_version(
        self,
        game_id: int,
        *,
        folder: str,
        entry: str,
        version: str | None,
        version_hint: str | None,
        action: str,
        backup: dict[str, Any] | None = None,
        restored_backup_id: int | None = None,
        size: int | None = None,
    ) -> dict[str, Any]:
        """Records a new version of a game, all in one transaction.

        Title, source, update link, notes and play statistics stay untouched.
        `backup` describes the replaced version (version, folder, entry, path, size),
        `restored_backup_id` the backup that was just restored,
        `size` the disk usage of the new version.
        """
        with self.connect() as conn:
            conn.execute(
                "UPDATE games SET folder = ?, entry = ?, version = ?, version_hint = ?,"
                " size = ?, missing = 0, updated_at = ? WHERE id = ?",
                (folder, entry, version, version_hint, size, now(), game_id),
            )
            if backup is not None:
                conn.execute(
                    "INSERT INTO game_backups"
                    " (game_id, version, folder, entry, path, size, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        game_id,
                        backup["version"],
                        backup["folder"],
                        backup["entry"],
                        backup["path"],
                        backup.get("size"),
                        now(),
                    ),
                )
            if restored_backup_id is not None:
                conn.execute("DELETE FROM game_backups WHERE id = ?", (restored_backup_id,))
            _insert_history(conn, game_id, version, action)
        return self.get_game(game_id)  # type: ignore[return-value]

    # User accounts

    def list_users(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY username").fetchall()
        return [dict(row) for row in rows]

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_name(self, username: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
            ).fetchone()
        return dict(row) if row else None

    def first_admin(self) -> dict[str, Any]:
        """The admin with the lowest id. It owns the shared inbox folder and is the
        only user when login is off."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE role = ? ORDER BY id LIMIT 1", (ROLE_ADMIN,)
            ).fetchone()
        if row is None:  # cannot happen through the app; repair instead of failing
            with self.connect() as conn:
                conn.execute("UPDATE users SET role = ? WHERE id = 1", (ROLE_ADMIN,))
            return self.first_admin()
        return dict(row)

    def setup_needed(self) -> bool:
        """True until an admin has a password, i.e. before the first login is possible."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM users WHERE role = ? AND password_hash != ''", (ROLE_ADMIN,)
            ).fetchone()
        return row is None

    def count_admins(self) -> int:
        with self.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM users WHERE role = ?", (ROLE_ADMIN,)
            ).fetchone()[0]

    def create_user(self, username: str, password_hash: str, role: str) -> dict[str, Any]:
        """Raises ValueError if the name is taken."""
        try:
            with self.connect() as conn:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at)"
                    " VALUES (?, ?, ?, ?)",
                    (username, password_hash, role, now()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("This user name is already taken.") from exc
        return self.get_user(cur.lastrowid)  # type: ignore[arg-type,return-value]

    def update_user(self, user_id: int, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Changes username, password_hash, role and/or language. Raises ValueError if
        the new name is taken."""
        unknown = set(fields) - {"username", "password_hash", "role", "language"}
        if unknown:
            raise ValueError(f"Fields that cannot be changed: {', '.join(sorted(unknown))}")
        if fields:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            try:
                with self.connect() as conn:
                    conn.execute(
                        f"UPDATE users SET {assignments} WHERE id = ?", (*fields.values(), user_id)
                    )
            except sqlite3.IntegrityError as exc:
                raise ValueError("This user name is already taken.") from exc
        return self.get_user(user_id)

    def delete_user(self, user_id: int, heir_id: int) -> None:
        """Removes an account with its play data. Its games stay (without this owner;
        admins still see them) and its pending imports go to `heir_id`."""
        with self.connect() as conn:
            conn.execute("DELETE FROM play_data WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM game_owners WHERE user_id = ?", (user_id,))
            conn.execute("UPDATE imports SET user_id = ? WHERE user_id = ?", (heir_id, user_id))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def _insert_history(
    conn: sqlite3.Connection, game_id: int, version: str | None, action: str
) -> None:
    conn.execute(
        "INSERT INTO game_history (game_id, version, action, at) VALUES (?, ?, ?, ?)",
        (game_id, version, action, now()),
    )


def _games_query(user: dict[str, Any] | None) -> tuple[str, tuple[Any, ...]]:
    """SELECT with a WHERE clause for the games a user may see; None = all, no play data."""
    if user is None:
        return f"{_GAME_SELECT} WHERE 1", (0,)
    is_admin = int(user["role"] == ROLE_ADMIN)
    return f"{_GAME_SELECT} WHERE {_VISIBLE}", (user["id"], is_admin, user["id"])


def _to_dict(row: sqlite3.Row) -> dict[str, Any]:
    game = dict(row)
    game["missing"] = bool(game["missing"])
    game["favorite"] = bool(game["favorite"])
    return game
