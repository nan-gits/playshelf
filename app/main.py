"""FastAPI application."""

import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import cache
from html import escape
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app import PROJECT_URL, __version__, downloads, i18n, imports, scanner, stats, updates, users
from app.accounts import router as accounts_router
from app.api import router as api_router
from app.api import validation_error_handler
from app.auth import require_login, setup_auth
from app.auth import router as auth_router
from app.backup import run_daily_backups, try_daily_backup
from app.config import Settings, get_settings
from app.db import DB_FILENAME, Database
from app.inbox import watch_inbox
from app.play import games_files
from app.play import router as play_router

STATIC_DIR = Path(__file__).parent / "static"
TEMPLATE = Path(__file__).parent / "templates" / "index.html"
PLACEHOLDER = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


@cache
def render_dashboard(language: str) -> str:
    """The dashboard page in one language.

    `{{key}}` in the template becomes the text from the language table, `{{lang}}`
    the language code, `{{version}}` and `{{project_url}}` the app's version and
    repository, and `{{messages}}` the whole table for app.js.
    """
    table = i18n.messages(language)
    fixed = {"lang": language, "version": __version__, "project_url": PROJECT_URL}

    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key in fixed:
            return escape(fixed[key])
        if key == "messages":
            data = json.dumps({"language": language, "messages": table}, ensure_ascii=False)
            return data.replace("<", "\\u003c")  # no "</script>" inside the script tag
        return escape(table[key])

    return PLACEHOLDER.sub(value, TEMPLATE.read_text(encoding="utf-8"))


def create_app(settings: Settings | None = None) -> FastAPI:
    """Creates the app. Tests can pass their own settings (for example temporary folders)."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_directories()
        app.state.db = Database(settings.data_dir / DB_FILENAME)
        users.adopt_password_hash(settings, app.state.db)
        users.ensure_inbox_folders(settings, app.state.db)
        app.state.db.sync(scanner.scan(settings.games_dir))
        stats.fill_backup_sizes(settings, app.state.db)
        imports.cleanup(settings, app.state.db)
        app.state.downloads = downloads.Downloads(settings, app.state.db)
        try_daily_backup(settings)
        tasks = [asyncio.create_task(run_daily_backups(settings))]
        if settings.inbox_poll_seconds > 0:
            tasks.append(asyncio.create_task(watch_inbox(settings, app.state.db)))
        if settings.update_check_hours > 0:
            checks = updates.run_scheduled_checks(
                app.state.db,
                app.state.fetcher,
                settings.update_check_hours,
                settings.adult_sources,
            )
            tasks.append(asyncio.create_task(checks))
        yield
        for task in tasks:
            task.cancel()
        app.state.downloads.shutdown()

    app = FastAPI(title="Playshelf", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.auth = setup_auth(settings)
    app.state.fetcher = updates.Fetcher()
    app.middleware("http")(require_login)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(auth_router)
    app.include_router(accounts_router)
    app.include_router(api_router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        """Simple liveness endpoint for monitoring and tests."""
        return {"status": "ok", "version": __version__}

    @app.get("/", include_in_schema=False)
    def dashboard(request: Request) -> HTMLResponse:
        return HTMLResponse(
            render_dashboard(i18n.request_language(request)),
            headers={"Cache-Control": "no-cache", "Vary": "Accept-Language"},
        )

    app.include_router(play_router)
    app.mount("/games", games_files(settings), name="games")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
