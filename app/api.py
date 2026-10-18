"""REST API of the library under /api."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, field_validator

from app import images, importer, imports, scanner, stats, updates
from app.accounts import Admin, User
from app.config import Settings
from app.db import (
    ACTION_EDIT,
    PLAY_FIELDS,
    ROLE_ADMIN,
    SOURCES,
    STATUSES,
    Database,
    available_sources,
)
from app.importer import ImportRejected, format_size

router = APIRouter(prefix="/api")


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_config(request: Request) -> Settings:
    return request.app.state.settings


DB = Annotated[Database, Depends(get_db)]
Config = Annotated[Settings, Depends(get_config)]

MB = 1024 * 1024
NOT_FOUND = "Game not found."
IMPORT_NOT_FOUND = "Import not found."
VERSION_REQUIRED = "The version is required. Please enter a version."
# Fields set directly on the tile; they do not need a version. They are kept per user.
QUICK_FIELDS = PLAY_FIELDS


class GameUpdate(BaseModel):
    """Editable fields; only fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    version: str | None = None
    source: str | None = None
    update_url: str | None = None
    notes: str | None = None
    entry: str | None = None
    status: str | None = None
    favorite: bool | None = None

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("The name must not be empty.")
        return value.strip()

    @field_validator("version")
    @classmethod
    def version_not_blank(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("The version is required and must not be empty.")
        return value.strip()

    @field_validator("source")
    @classmethod
    def known_source(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and value not in SOURCES:
            raise ValueError(f"Unknown source. Allowed: {', '.join(SOURCES)}.")
        return value or None

    @field_validator("update_url")
    @classmethod
    def http_url(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and not value.lower().startswith(("http://", "https://")):
            raise ValueError("The update link must start with http:// or https://.")
        return value or None

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, value: str | None) -> str | None:
        return (value or "").strip() or None

    @field_validator("entry")
    @classmethod
    def entry_not_blank(cls, value: str | None) -> str:
        if value is None or not value.strip():
            raise ValueError("The start file must not be empty.")
        return value.strip()

    @field_validator("status")
    @classmethod
    def known_status(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        if value and value not in STATUSES:
            raise ValueError(f"Unknown status. Allowed: {', '.join(STATUSES)}.")
        return value or None

    @field_validator("favorite", mode="before")
    @classmethod
    def favorite_not_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("Favorite must be true or false.")
        return value


def _game_or_404(db: Database, game_id: int, user: dict[str, Any]) -> dict[str, Any]:
    """The game as the user sees it; games the user may not see do not exist for them."""
    game = db.get_game(game_id, user)
    if game is None:
        raise HTTPException(404, NOT_FOUND)
    return game


def _annotate(
    db: Database, user: dict[str, Any], games: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Adds update check results and, for admins, who owns each game."""
    games = updates.annotate(db, games)
    if user["role"] == ROLE_ADMIN:
        owners = db.owners()
        for game in games:
            game["owners"] = owners.get(game["id"], [])
    return games


def _view(db: Database, user: dict[str, Any], game_id: int) -> dict[str, Any]:
    """A game freshly read for the API response, with the user's play data."""
    return _annotate(db, user, [_game_or_404(db, game_id, user)])[0]


@router.get("/games")
def list_games(db: DB, user: User) -> list[dict[str, Any]]:
    return _annotate(db, user, db.list_games(user))


@router.get("/games/{game_id}")
def get_game(game_id: int, db: DB, user: User) -> dict[str, Any]:
    return _view(db, user, game_id)


@router.get("/games/{game_id}/html-files")
def list_html_files(game_id: int, db: DB, settings: Config, user: User) -> list[str]:
    """Possible start files for the edit dialog."""
    game = _game_or_404(db, game_id, user)
    folder = settings.games_dir / game["folder"]
    if not folder.is_dir():
        raise HTTPException(409, "The game folder no longer exists.")
    return scanner.list_html_files(folder)


@router.patch("/games/{game_id}")
def update_game(
    game_id: int, body: GameUpdate, db: DB, settings: Config, user: User
) -> dict[str, Any]:
    """Changes the game; status and favorite only for the signed-in user."""
    game = _game_or_404(db, game_id, user)
    fields = body.model_dump(exclude_unset=True)
    if set(fields) - QUICK_FIELDS and not fields.get("version", game["version"]):
        raise _field_error("version", VERSION_REQUIRED)
    if "entry" in fields:
        folder = settings.games_dir / game["folder"]
        if fields["entry"] not in scanner.list_html_files(folder):
            raise _field_error(
                "entry", "The start file must be an existing HTML file in the game folder."
            )
    play = {name: fields.pop(name) for name in PLAY_FIELDS & set(fields)}
    db.set_play_data(user["id"], game_id, play)
    db.update_game(game_id, fields)
    if "version" in fields and fields["version"] != game["version"]:
        db.add_history(game_id, fields["version"], ACTION_EDIT)
    return _view(db, user, game_id)


class OwnersUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_ids: list[int]


@router.put("/games/{game_id}/owners")
def set_owners(game_id: int, body: OwnersUpdate, db: DB, admin: Admin) -> dict[str, Any]:
    """Who sees the game besides the admins (admins only)."""
    _game_or_404(db, game_id, admin)
    known = {u["id"] for u in db.list_users()}
    if set(body.user_ids) - known:
        raise _field_error("user_ids", "Unknown account.")
    db.set_owners(game_id, body.user_ids)
    return _view(db, admin, game_id)


@router.delete("/games/{game_id}", status_code=204)
def delete_game(game_id: int, db: DB, settings: Config, admin: Admin) -> Response:
    """Removes only the entry of a game whose folder is missing. Files are never touched.
    Admins only, because the entry belongs to everyone who sees the game."""
    game = _game_or_404(db, game_id, admin)
    if not game["missing"]:
        raise HTTPException(
            409,
            "Only entries whose folder is missing can be removed. Files are never deleted.",
        )
    db.delete_game(game_id)
    images.forget(settings, game_id)
    return Response(status_code=204)


# Cover images


class ImageChoice(BaseModel):
    """Choice of cover image; `path` only with mode "file"."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["auto", "file", "none"]
    path: str | None = None


@router.get("/games/{game_id}/image", response_model=None)
def game_image(game_id: int, request: Request, db: DB, settings: Config, user: User) -> Response:
    """Scaled-down cover image. The browser revalidates every time (ETag) so that a
    changed image shows up at once; an unchanged one only returns "304 Not Modified"."""
    game = _game_or_404(db, game_id, user)
    try:
        path = images.thumbnail(settings, game)
    except images.ImageError as exc:
        raise HTTPException(404, str(exc)) from exc
    if path is None:
        raise HTTPException(404, "This game has no cover image.")
    etag = f'"{path.stem}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return FileResponse(path, media_type="image/webp", headers=headers)


@router.get("/games/{game_id}/images")
def game_images(game_id: int, db: DB, settings: Config, user: User) -> dict[str, Any]:
    """Images in the game folder for the dialog; `auto` is the automatic choice."""
    return images.choices(settings, _game_or_404(db, game_id, user))


@router.put("/games/{game_id}/image")
def choose_image(
    game_id: int, body: ImageChoice, db: DB, settings: Config, user: User
) -> dict[str, Any]:
    game = _game_or_404(db, game_id, user)
    try:
        images.set_mode(settings, db, game, body.mode, body.path)
    except images.ImageError as exc:
        raise _field_error("path", str(exc)) from exc
    return _view(db, user, game_id)


@router.post("/games/{game_id}/image")
async def upload_image(
    game_id: int, request: Request, db: DB, settings: Config, user: User
) -> dict[str, Any]:
    """Accepts a custom image as raw request body and stores a scaled-down copy."""
    game = _game_or_404(db, game_id, user)
    limit = images.MAX_UPLOAD_BYTES
    too_big = HTTPException(413, f"The image is too large. The maximum is {limit // MB} MB.")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        raise too_big
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise too_big
    try:
        await run_in_threadpool(images.upload, settings, db, game, bytes(data))
    except images.ImageError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _view(db, user, game_id)


@router.post("/scan")
def rescan(db: DB, settings: Config, _: Admin) -> dict[str, int]:
    """Rescans the games folder: number of new, found and missing games. Admins only;
    new folders have no owner yet, so only admins see them until they share them."""
    result = db.sync(scanner.scan(settings.games_dir))
    stats.fill_backup_sizes(settings, db)
    return result


@router.get("/stats")
def get_stats(db: DB, user: User) -> dict[str, Any]:
    """Statistics of the games the user sees, with their own play data, from stored
    values; does not recompute folder sizes. Admins also count backups of removed games."""
    games = db.list_games(user)
    backups = db.list_all_backups()
    if user["role"] != ROLE_ADMIN:
        visible = {g["id"] for g in games}
        backups = [b for b in backups if b["game_id"] in visible]
    return stats.compute(games, backups)


@router.get("/sources")
def list_sources(settings: Config) -> list[str]:
    return available_sources(settings.adult_sources)


@router.get("/statuses")
def list_statuses() -> list[str]:
    return STATUSES


# Archive import


class ImportConfirm(GameUpdate):
    """Confirms an import: empty `game_id` = new game, otherwise an update of that game."""

    game_id: int | None = None


def _import_or_404(db: Database, import_id: int, user: dict[str, Any]) -> dict[str, Any]:
    """A pending import of the user; each account only sees its own imports."""
    imp = db.get_import(import_id)
    if imp is None or imp["user_id"] != user["id"]:
        raise HTTPException(404, IMPORT_NOT_FOUND)
    return imp


@router.post("/imports", status_code=201)
async def upload_import(
    request: Request,
    db: DB,
    settings: Config,
    user: User,
    filename: Annotated[str, Query(min_length=1, max_length=255)] = "Upload.zip",
) -> dict[str, Any]:
    """Accepts an archive as raw request body (not a form), extracts and checks it.

    This lets the browser send the upload with progress, and large archives go
    straight into the data folder instead of a temporary system folder.
    """
    limit = settings.max_upload_mb * MB
    too_big = HTTPException(
        413,
        f"The archive is too large. The maximum is {format_size(limit)} (MAX_UPLOAD_MB).",
    )
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > limit:
        raise too_big

    settings.imports_dir.mkdir(parents=True, exist_ok=True)
    archive = settings.imports_dir / f"{imports.new_token()}.upload"
    try:
        received = 0
        with archive.open("wb") as file:
            async for chunk in request.stream():
                received += len(chunk)
                if received > limit:
                    raise too_big
                await run_in_threadpool(file.write, chunk)
        if not received:
            raise HTTPException(422, "No file was received.")
        imp = await run_in_threadpool(imports.receive, settings, db, archive, filename, user["id"])
    except ImportRejected as exc:
        raise HTTPException(422, f'"{filename}" was not imported: {exc}') from exc
    finally:
        archive.unlink(missing_ok=True)
    return next(p for p in imports.pending(settings, db, user) if p["id"] == imp["id"])


@router.get("/imports/limits")
def import_limits(settings: Config, _: User) -> dict[str, Any]:
    """Size limits and archive formats for imports, so the browser can refuse a file
    before uploading it."""
    return {
        "max_upload_bytes": settings.max_upload_mb * MB,
        "max_extracted_bytes": settings.max_extracted_mb * MB,
        "formats": importer.supported_suffixes(),
    }


@router.get("/imports")
def list_imports(db: DB, settings: Config, user: User) -> list[dict[str, Any]]:
    """The user's pending imports with a suggested match."""
    return imports.pending(settings, db, user)


@router.post("/imports/{import_id}/confirm")
def confirm_import(
    import_id: int, body: ImportConfirm, db: DB, settings: Config, user: User
) -> dict[str, Any]:
    """Installs an import as a new game (owned by the uploader) or as an update of a
    game the user sees; returns the game."""
    imp = _import_or_404(db, import_id, user)
    fields = body.model_dump(exclude_unset=True)
    game_id = fields.pop("game_id", None)
    if not fields.get("version"):
        raise _field_error("version", VERSION_REQUIRED)
    files = scanner.list_html_files(imports.game_dir(settings, imp))
    if "entry" in fields and fields["entry"] not in files:
        raise _field_error("entry", "The start file must be an HTML file from the import.")
    entry = fields.pop("entry", None)
    try:
        if game_id is None:
            game = imports.install_new(settings, db, imp, fields, entry)
            return _view(db, user, game["id"])
        target = db.get_game(game_id, user)
        if target is None:
            raise _field_error("game_id", "The selected game does not exist.")
        # An update keeps title, source, update link and notes
        imports.install_update(settings, db, imp, target, fields["version"], entry)
        return _view(db, user, game_id)
    except imports.ImportConflict as exc:
        raise HTTPException(409, str(exc)) from exc


class RestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backup_id: int | None = None


@router.get("/games/{game_id}/history")
def game_history(game_id: int, db: DB, user: User) -> dict[str, list[dict[str, Any]]]:
    """Version history and existing backups, newest first."""
    _game_or_404(db, game_id, user)
    backups = [
        {key: b[key] for key in ("id", "version", "folder", "created_at")}
        for b in db.list_backups(game_id)
    ]
    return {"history": db.list_history(game_id), "backups": backups}


@router.post("/games/{game_id}/restore")
def restore_game(
    game_id: int, db: DB, settings: Config, user: User, body: RestoreRequest | None = None
) -> dict[str, Any]:
    """Restores a backup; the newest one if none is given."""
    game = _game_or_404(db, game_id, user)
    backups = db.list_backups(game_id)
    backup_id = body.backup_id if body else None
    backup = next((b for b in backups if backup_id in (None, b["id"])), None)
    if backup is None:
        raise HTTPException(404, "This game has no such backup.")
    try:
        imports.restore(settings, db, game, backup)
    except imports.ImportConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    return _view(db, user, game_id)


@router.delete("/imports/{import_id}", status_code=204)
def discard_import(import_id: int, db: DB, settings: Config, user: User) -> Response:
    """Discards an import and deletes its staging folder."""
    imports.discard(settings, db, _import_or_404(db, import_id, user))
    return Response(status_code=204)


# Online update check


def get_fetcher(request: Request) -> updates.Fetch:
    return request.app.state.fetcher


Fetcher = Annotated[updates.Fetch, Depends(get_fetcher)]


@router.post("/games/{game_id}/check-update")
def check_game_update(
    game_id: int, db: DB, settings: Config, fetch: Fetcher, user: User
) -> dict[str, Any]:
    """Checks right away whether a newer version is online; returns the game."""
    game = _game_or_404(db, game_id, user)
    if not game["update_url"]:
        raise HTTPException(409, "This game has no update link.")
    updates.check_game(db, game, fetch, settings.adult_sources)
    return _view(db, user, game_id)


@router.post("/updates/check", status_code=202)
def check_all_updates(
    db: DB, settings: Config, fetch: Fetcher, background: BackgroundTasks
) -> dict[str, Any]:
    """Starts checking all games with an update link in the background."""
    if updates.is_running():
        return {"started": False, "running": True, "count": 0}
    count = len(updates.due_games(db))
    background.add_task(updates.check_all, db, fetch, None, settings.adult_sources)
    return {"started": True, "running": True, "count": count}


@router.get("/updates/status")
def update_status() -> dict[str, bool]:
    return {"running": updates.is_running()}


# Uniform error messages: {"detail": [{"field": ..., "message": ...}]}

_GENERIC_MESSAGES = {
    "extra_forbidden": "Unknown field.",
    "missing": "Required value is missing.",
    "json_invalid": "Invalid JSON.",
    "string_type": "Text expected.",
    "int_parsing": "Whole number expected.",
}


def _field_error(field: str, message: str) -> HTTPException:
    return HTTPException(422, [{"field": field, "message": message}])


async def validation_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    errors = []
    for error in exc.errors():
        loc = [str(part) for part in error.get("loc", ()) if part not in ("body", "path", "query")]
        if error.get("type") == "value_error":
            message = str(error.get("ctx", {}).get("error", error.get("msg")))
        else:
            message = _GENERIC_MESSAGES.get(error.get("type", ""), "Invalid value.")
        errors.append({"field": ".".join(loc), "message": message})
    return JSONResponse(status_code=422, content=jsonable_encoder({"detail": errors}))
