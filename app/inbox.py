"""Inbox folder: import new ZIP files in INBOX_DIR automatically.

The folder is polled regularly. A ZIP file only counts as completely copied once
its size and modification time stay unchanged for `settle_seconds`, so files that
are still being copied (for example over SMB) are not touched too early. It then
goes through the same import as an upload and appears under pending imports. The
file itself is moved to `processed/`, or to `failed/` if it was rejected,
together with a text file giving the reason.

Files already present at startup are handled the same way, including files that
were dropped in during a restart.

Each account has its own subfolder, `INBOX_DIR/<user name>/`, with its own
`processed/` and `failed/`; its imports belong to that account. ZIP files placed
directly in `INBOX_DIR` belong to the first admin. Subfolders that match no
account are ignored.

Other archives (RAR, 7z, tar) are not imported; they are moved to `failed/` with
a note, so they do not lie in the inbox without any feedback.
"""

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app import imports
from app.config import Settings
from app.db import Database
from app.importer import ImportRejected

DONE_DIR = "processed"
FAILED_DIR = "failed"
OTHER_ARCHIVES = {".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}
NOT_A_ZIP = "Only ZIP files are imported. Pack the game folder as a .zip file and try again."

log = logging.getLogger(__name__)


@dataclass
class _Seen:
    size: int
    mtime: float
    since: float  # since when size and modification time have been unchanged


class InboxWatcher:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.seen: dict[Path, _Seen] = {}

    def folders(self) -> dict[Path, int]:
        """Inbox folders to watch and the account each one belongs to."""
        inbox = self.settings.inbox_dir
        folders = {inbox: self.db.first_admin()["id"]}
        if self.settings.auth == "accounts":
            for user in self.db.list_users():
                folders[inbox / user["username"]] = user["id"]
        return folders

    def candidates(self) -> list[tuple[Path, int]]:
        """Archives waiting in the inbox folders, each with the account it belongs to."""
        found = []
        for folder, user_id in self.folders().items():
            if not folder.is_dir():
                continue
            found.extend(
                (p, user_id)
                for p in folder.iterdir()
                if p.is_file()
                and p.suffix.lower() in {".zip", *OTHER_ARCHIVES}
                and not p.name.startswith((".", "~"))
            )
        return sorted(found)

    def scan_once(self, now: float | None = None) -> list[Path]:
        """Processes all completely copied ZIPs; returns the processed paths."""
        now = time.monotonic() if now is None else now
        ready = []
        current = set()
        for path, user_id in self.candidates():
            try:
                stat = path.stat()
            except OSError:
                continue
            current.add(path)
            seen = self.seen.get(path)
            if seen is None or (seen.size, seen.mtime) != (stat.st_size, stat.st_mtime):
                self.seen[path] = _Seen(stat.st_size, stat.st_mtime, now)
            elif stat.st_size and now - seen.since >= self.settings.inbox_settle_seconds:
                ready.append((path, user_id))
        # Forget vanished files
        for path in set(self.seen) - current:
            del self.seen[path]
        for path, user_id in ready:
            self.process(path, user_id)
            self.seen.pop(path, None)
        return [path for path, _ in ready]

    def process(self, path: Path, user_id: int) -> None:
        if path.suffix.lower() != ".zip":
            self._move(path, FAILED_DIR, NOT_A_ZIP)
            log.info("Inbox: %s rejected: not a ZIP file", path.name)
            return
        try:
            imp = imports.receive(self.settings, self.db, path, path.name, user_id)
        except ImportRejected as exc:
            self._move(path, FAILED_DIR, str(exc))
            log.info("Inbox: %s rejected: %s", path.name, exc)
        except Exception as exc:  # for example a full disk; do not retry forever
            log.exception("Inbox: %s could not be processed", path.name)
            self._move(path, FAILED_DIR, f"Unexpected error: {exc}")
        else:
            self._move(path, DONE_DIR)
            log.info("Inbox: %s is waiting as import %s", path.name, imp["id"])

    def _move(self, path: Path, folder: str, reason: str | None = None) -> None:
        target_dir = path.parent / folder
        target_dir.mkdir(exist_ok=True)
        target = target_dir / path.name
        if target.exists():
            target = target_dir / f"{path.stem} {datetime.now():%Y-%m-%d %H%M%S}{path.suffix}"
        try:
            shutil.move(path, target)
        except OSError:
            log.exception("Inbox: %s could not be moved", path.name)
            return
        if reason:
            target.with_name(f"{target.name}.txt").write_text(
                f'"{path.name}" was not imported:\n{reason}\n', encoding="utf-8"
            )


async def watch_inbox(settings: Settings, db: Database) -> None:
    """Polls the inbox folder at fixed intervals until the app stops."""
    watcher = InboxWatcher(settings, db)
    while True:
        try:
            await asyncio.to_thread(watcher.scan_once)
        except Exception:
            log.exception("Inbox folder could not be polled")
        await asyncio.sleep(settings.inbox_poll_seconds)
