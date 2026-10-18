"""Account API: your own account under /api/me, all accounts (admins only) under /api/users."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from app import users
from app.auth import current_user, hash_password, set_session_cookie, verify_password
from app.config import Settings
from app.db import ROLE_ADMIN, Database

router = APIRouter(prefix="/api")

USER_NOT_FOUND = "Account not found."


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_config(request: Request) -> Settings:
    return request.app.state.settings


def require_admin(request: Request) -> dict[str, Any]:
    user = current_user(request)
    if user["role"] != ROLE_ADMIN:
        raise HTTPException(403, "Only admins can do this.")
    return user


DB = Annotated[Database, Depends(get_db)]
Config = Annotated[Settings, Depends(get_config)]
User = Annotated[dict[str, Any], Depends(current_user)]
Admin = Annotated[dict[str, Any], Depends(require_admin)]


def _field_error(field: str, message: str) -> HTTPException:
    return HTTPException(422, [{"field": field, "message": message}])


def _check(field: str, check: Any, value: str) -> str:
    try:
        return check(value)
    except users.AccountError as exc:
        raise _field_error(field, str(exc)) from exc


# Your own account


class MeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None = the browser's or the server's language
    language: Literal["en", "de"] | None


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: str
    new: str


@router.get("/me")
def me(user: User, settings: Config) -> dict[str, Any]:
    """The signed-in account; `accounts` is false when login is off (AUTH=none)."""
    return {"accounts": settings.auth == "accounts", "user": users.public(user)}


@router.patch("/me")
def update_me(body: MeUpdate, user: User, db: DB, settings: Config) -> dict[str, Any]:
    updated = db.update_user(user["id"], {"language": body.language})
    return {"accounts": settings.auth == "accounts", "user": users.public(updated)}  # type: ignore[arg-type]


@router.post("/me/password", status_code=204)
def change_password(body: PasswordChange, request: Request, user: User, db: DB) -> Response:
    """Sets a new password; this ends all sessions, so a new cookie is sent along."""
    if not verify_password(body.current, user["password_hash"]):
        raise _field_error("current", "The current password is wrong.")
    _check("new", users.check_password, body.new)
    updated = db.update_user(user["id"], {"password_hash": hash_password(body.new)})
    response = Response(status_code=204)
    set_session_cookie(request, response, updated)  # type: ignore[arg-type]
    return response


# All accounts (admins only)


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    password: str
    role: Literal["admin", "user"] = "user"


class UserUpdate(BaseModel):
    """Only fields that are sent are changed."""

    model_config = ConfigDict(extra="forbid")

    username: str | None = None
    password: str | None = None
    role: Literal["admin", "user"] | None = None


def _user_or_404(db: Database, user_id: int) -> dict[str, Any]:
    user = db.get_user(user_id)
    if user is None:
        raise HTTPException(404, USER_NOT_FOUND)
    return user


@router.get("/users")
def list_users(_: Admin, db: DB) -> list[dict[str, Any]]:
    return [users.public(user) for user in db.list_users()]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, _: Admin, db: DB, settings: Config) -> dict[str, Any]:
    name = _check("username", users.clean_username, body.username)
    _check("password", users.check_password, body.password)
    try:
        user = db.create_user(name, hash_password(body.password), body.role)
    except ValueError as exc:
        raise _field_error("username", str(exc)) from exc
    users.ensure_inbox_folders(settings, db)
    return users.public(user)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int, body: UserUpdate, _: Admin, db: DB, settings: Config
) -> dict[str, Any]:
    user = _user_or_404(db, user_id)
    fields: dict[str, Any] = {}
    if body.username is not None:
        fields["username"] = _check("username", users.clean_username, body.username)
    if body.password is not None:
        fields["password_hash"] = hash_password(
            _check("password", users.check_password, body.password)
        )
    if body.role is not None and body.role != user["role"]:
        if user["role"] == ROLE_ADMIN and db.count_admins() == 1:
            raise _field_error("role", "There must be at least one admin.")
        fields["role"] = body.role
    try:
        updated = db.update_user(user_id, fields)
    except ValueError as exc:
        raise _field_error("username", str(exc)) from exc
    if "username" in fields and fields["username"] != user["username"]:
        users.rename_inbox_folder(settings, user["username"], fields["username"])
        users.ensure_inbox_folders(settings, db)
    return users.public(updated)  # type: ignore[arg-type]


@router.delete("/users/{user_id}", status_code=204)
def delete_user(user_id: int, admin: Admin, db: DB) -> Response:
    """Removes an account and its play data. Games and files stay; pending imports
    go to the admin who removes the account. The inbox folder stays on disk."""
    user = _user_or_404(db, user_id)
    if user["id"] == admin["id"]:
        raise HTTPException(409, "You cannot remove your own account.")
    if user["role"] == ROLE_ADMIN and db.count_admins() == 1:
        raise HTTPException(409, "There must be at least one admin.")
    db.delete_user(user_id, heir_id=admin["id"])
    return Response(status_code=204)
