from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigError, Settings
from app.main import create_app


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GAMES_DIR", "DATA_DIR", "INBOX_DIR", "HOST", "PORT"):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.games_dir == Path("games")
    assert settings.data_dir == Path("data")
    assert settings.inbox_dir == Path("inbox")
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_reads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GAMES_DIR", str(tmp_path / "g"))
    monkeypatch.setenv("PORT", "9123")
    settings = Settings(_env_file=None)
    assert settings.games_dir == tmp_path / "g"
    assert settings.port == 9123


def test_reads_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DATA_DIR", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("DATA_DIR=/srv/dashboard/data\n", encoding="utf-8")
    assert Settings(_env_file=env_file).data_dir == Path("/srv/dashboard/data")


def test_invalid_port_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PORT", "70000")
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_startup_creates_missing_directories(settings: Settings) -> None:
    with TestClient(create_app(settings)):
        pass
    assert settings.games_dir.is_dir()
    assert settings.data_dir.is_dir()
    assert settings.inbox_dir.is_dir()


def test_file_instead_of_directory_gives_clear_error(settings: Settings) -> None:
    settings.games_dir.parent.mkdir(parents=True, exist_ok=True)
    settings.games_dir.write_text("not a folder")
    with pytest.raises(ConfigError, match="GAMES_DIR=.*not a folder"):
        settings.ensure_directories()


def test_uncreatable_directory_gives_clear_error(settings: Settings, tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    settings.data_dir = blocker / "subfolder"
    with pytest.raises(ConfigError, match="DATA_DIR=.*cannot be created"):
        settings.ensure_directories()
