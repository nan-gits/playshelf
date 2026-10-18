"""User accounts: names, roles and the inbox folder of each account.

Every account has a user name, a password hash and a role. Admins see every game
and manage accounts; users see the games they own or that were shared with them.
Play data (last played, starts, favourite, status) is kept per account.

Each account gets its own inbox subfolder, `INBOX_DIR/<user name>/`, which is why
user names are restricted to characters that are safe in folder and SMB user
names. ZIP files directly in `INBOX_DIR` belong to the first admin.
"""

import logging
import re
from typing import Any

from app.config import Settings
from app.db import ROLE_ADMIN, Database

USERNAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,31}")
# Subfolders of the inbox that the watcher uses itself
RESERVED_NAMES = {"processed", "failed"}
MIN_PASSWORD_LENGTH = 8

log = logging.getLogger(__name__)


class AccountError(ValueError):
    """An unusable user name or password. `key` names the text in the language tables
    for pages rendered by the server; the message itself is English, like the API's."""

    def __init__(self, key: str, message: str, **params: object) -> None:
        super().__init__(message)
        self.key = key
        self.params = params


def clean_username(name: str) -> str:
    """The user name in its stored form (lower case); raises AccountError if unusable."""
    name = name.strip().lower()
    if not USERNAME.fullmatch(name):
        raise AccountError(
            "account.error.username",
            "User names have 1 to 32 characters: letters a-z, digits and . _ -,"
            " starting with a letter or digit.",
        )
    if name in RESERVED_NAMES:
        raise AccountError(
            "account.error.reserved",
            f'"{name}" is reserved and cannot be used as a user name.',
            name=name,
        )
    return name


def check_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AccountError(
            "account.error.password_short",
            f"The password must be at least {MIN_PASSWORD_LENGTH} characters long.",
            count=MIN_PASSWORD_LENGTH,
        )
    return password


def public(user: dict[str, Any]) -> dict[str, Any]:
    """An account as the API shows it, without the password hash."""
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "language": user["language"],
        "is_admin": user["role"] == ROLE_ADMIN,
        "created_at": user["created_at"],
    }


def adopt_password_hash(settings: Settings, db: Database) -> None:
    """Before the first login: an existing PASSWORD_HASH becomes the first admin's password.

    This carries the single password of older versions over to the account that
    keeps all existing data. Afterwards PASSWORD_HASH is no longer used.
    """
    if settings.password_hash and db.setup_needed():
        admin = db.first_admin()
        db.update_user(admin["id"], {"password_hash": settings.password_hash.strip()})
        log.info('PASSWORD_HASH is now the password of the admin account "%s"', admin["username"])


def ensure_inbox_folders(settings: Settings, db: Database) -> None:
    """Creates the inbox subfolder of every account (only when accounts are on)."""
    if settings.auth != "accounts":
        return
    for user in db.list_users():
        try:
            (settings.inbox_dir / user["username"]).mkdir(exist_ok=True)
        except OSError:
            log.exception("Inbox folder for %s could not be created", user["username"])


def rename_inbox_folder(settings: Settings, old: str, new: str) -> None:
    """Moves an account's inbox folder along with a new user name, if nothing is in the way."""
    source, target = settings.inbox_dir / old, settings.inbox_dir / new
    if source.is_dir() and not target.exists():
        try:
            source.rename(target)
        except OSError:
            log.exception("Inbox folder %s could not be renamed to %s", old, new)
