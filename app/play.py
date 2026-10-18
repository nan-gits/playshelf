"""Start games (/play/{id}) and serve their files (/games/...)."""

import mimetypes
from html import escape
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope

from app.config import Settings
from app.db import Database
from app.i18n import request_language, translate

# Not every system knows all file types (for example slim Docker images without
# /etc/mime.types). What games typically ship with is registered explicitly.
MEDIA_TYPES = {
    ".html": "text/html",
    ".htm": "text/html",
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".json": "application/json",
    ".map": "application/json",
    ".wasm": "application/wasm",
    ".txt": "text/plain",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".avif": "image/avif",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".bmp": "image/bmp",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".ogv": "video/ogg",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
}


def register_media_types() -> None:
    for suffix, media_type in MEDIA_TYPES.items():
        mimetypes.add_type(media_type, suffix)


router = APIRouter()


def game_url(folder: str, entry: str) -> str:
    """URL of the start file; spaces and special characters are encoded."""
    return f"/games/{quote(folder, safe='')}/{quote(entry, safe='/')}"


def error_page(request: Request, status: int, key: str, **params: str) -> HTMLResponse:
    """Error page; `key` names `<key>.heading` and `<key>.text` in the language table."""
    language = request_language(request)
    heading = translate(language, f"{key}.heading")
    text = translate(language, f"{key}.text", **params)
    back = translate(language, "play.back")
    body = f"""<!doctype html>
<html lang="{language}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(heading)}</title>
<style>
body{{font:16px/1.5 system-ui,sans-serif;max-width:36rem;margin:4rem auto;padding:0 1rem}}
@media (prefers-color-scheme:dark){{body{{background:#15151a;color:#ececf1}}a{{color:#8b80ff}}}}
</style>
</head><body><h1>{escape(heading)}</h1><p>{escape(text)}</p>
<p><a href="/">{escape(back)}</a></p></body></html>"""
    return HTMLResponse(body, status_code=status, headers={"Vary": "Accept-Language"})


@router.get("/play/{game_id}", response_model=None)
def play(game_id: int, request: Request) -> Response:
    """Records "last played" for the user, counts the start and redirects to the start file."""
    db: Database = request.app.state.db
    settings: Settings = request.app.state.settings
    user = request.state.user
    game = db.get_game(game_id, user)
    if game is None:
        return error_page(request, 404, "play.not_found")

    folder = settings.games_dir / game["folder"]
    if not folder.is_dir():
        db.set_missing(game_id)
        return error_page(request, 404, "play.folder_missing", folder=game["folder"])
    if not (folder / game["entry"]).is_file():
        return error_page(
            request, 404, "play.entry_missing", entry=game["entry"], folder=game["folder"]
        )

    db.mark_played(user["id"], game_id)
    return RedirectResponse(
        game_url(game["folder"], game["entry"]),
        status_code=303,
        headers={"Cache-Control": "no-store"},
    )


class GameFiles(StaticFiles):
    """Serves the games folder, but only the folders of games the user may see.

    StaticFiles blocks access outside the folder (also via symlinks) and only serves
    files, never folder listings.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        user: dict[str, Any] | None = scope.get("state", {}).get("user")
        db: Database = scope["app"].state.db
        folder = path.replace("\\", "/").split("/", 1)[0]
        if user is None or not db.can_see_folder(user, folder):
            raise HTTPException(404)
        return await super().get_response(path, scope)


def games_files(settings: Settings) -> StaticFiles:
    register_media_types()
    return GameFiles(directory=settings.games_dir, check_dir=False)
