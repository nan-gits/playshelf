"""Central configuration.

All settings are loaded here from environment variables or a `.env` file.
Relative paths are relative to the current working directory.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """The configuration is invalid, for example because a folder cannot be used."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    games_dir: Path = Field(default=Path("games"), description="Game library")
    data_dir: Path = Field(default=Path("data"), description="Database and backups")
    inbox_dir: Path = Field(default=Path("inbox"), description="Inbox folder for ZIP files")
    host: str = Field(default="127.0.0.1", description="Address the server binds to")
    port: int = Field(default=8000, ge=1, le=65535, description="Web server port")
    auth: Literal["accounts", "none"] = Field(
        default="accounts", description="accounts = sign in with user accounts, none = no login"
    )
    password_hash: str | None = Field(
        default=None,
        description="Password of older versions; becomes the first admin's password once",
    )
    max_upload_mb: int = Field(default=4096, ge=1, description="Maximum size of a ZIP archive")
    max_extracted_mb: int = Field(
        default=8192, ge=1, description="Maximum size of an archive's extracted content"
    )
    keep_backups: int = Field(default=3, ge=0, description="Backups of old versions kept per game")
    inbox_poll_seconds: float = Field(
        default=5, ge=0, description="Inbox polling interval, 0 = off"
    )
    inbox_settle_seconds: float = Field(
        default=10, ge=0, description="How long a ZIP file must stay unchanged"
    )

    update_check_hours: float = Field(
        default=24, ge=0, description="Online update check interval in hours, 0 = off"
    )
    adult_sources: bool = Field(
        default=False, description="Offer the adult game sites F95Zone and TFGames as sources"
    )
    # Not named LANGUAGE: that is the GNU locale variable, which Debian and
    # Ubuntu set in every shell (for example LANGUAGE=en_US:en).
    ui_language: Literal["auto", "en", "de"] = Field(
        default="auto", description="Interface language; auto = the browser's language"
    )

    @field_validator("auth", mode="before")
    @classmethod
    def _normalize_auth(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "accounts"
        return value

    @field_validator("ui_language", mode="before")
    @classmethod
    def _normalize_language(cls, value: object) -> object:
        # UI_LANGUAGE= (empty) means auto, and DE works as well as de
        if isinstance(value, str):
            return value.strip().lower() or "auto"
        return value

    @property
    def imports_dir(self) -> Path:
        """Staging folder for extracted imports that are not confirmed yet."""
        return self.data_dir / "imports"

    @property
    def backups_dir(self) -> Path:
        """Backups of replaced game versions: backups/<game>/<version>."""
        return self.data_dir / "backups"

    def directories(self) -> dict[str, Path]:
        return {
            "GAMES_DIR": self.games_dir,
            "DATA_DIR": self.data_dir,
            "INBOX_DIR": self.inbox_dir,
        }

    def ensure_directories(self) -> None:
        """Creates missing folders and stops with a clear message if that fails."""
        for name, path in self.directories().items():
            if path.exists() and not path.is_dir():
                raise ConfigError(f"{name}={path} exists but is not a folder.")
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise ConfigError(
                    f"{name}={path} cannot be created: {exc.strerror or exc}"
                ) from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()
