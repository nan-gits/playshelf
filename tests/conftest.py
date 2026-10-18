from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Settings with temporary folders, so tests never touch anything real.
    Login is off; tests for accounts turn it on."""
    return Settings(
        _env_file=None,
        games_dir=tmp_path / "games",
        data_dir=tmp_path / "data",
        inbox_dir=tmp_path / "inbox",
        update_check_hours=0,
        auth="none",
    )


@pytest.fixture
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client
