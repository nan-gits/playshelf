"""Sign-in with user accounts.

With `AUTH=accounts` (the default) everyone signs in with a user name and password.
Until the first admin has a password, every page leads to the setup page, where
that account is created. A `PASSWORD_HASH` from older versions is taken over as
the first admin's password instead (see `users.adopt_password_hash`), so an
existing installation keeps its password; the user name is then `admin`.

With `AUTH=none` there is no login: everyone acts as the first admin.

After login the browser keeps the session in a signed cookie (30 days). Without
a valid session the API answers 401 and every other page redirects to the login.
Only the login and setup pages, the health endpoint and the UI files under
/static stay public.

`python -m app.auth` prints a password hash; `python -m app.auth --reset <name>`
sets a new password for an account directly in the database.
"""

import argparse
import asyncio
import base64
import getpass
import hashlib
import hmac
import secrets
import time
from collections.abc import Awaitable, Callable
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app import users
from app.config import Settings, get_settings
from app.db import DB_FILENAME, Database
from app.i18n import request_language, translate

COOKIE_NAME = "dashboard_session"
SESSION_SECONDS = 30 * 24 * 3600
ITERATIONS = 600_000
ALGORITHM = "pbkdf2_sha256"
KEY_FILENAME = "session.key"
PUBLIC_PATHS = {"/login", "/logout", "/setup", "/api/health"}
PUBLIC_PREFIXES = ("/static/",)
FAILED_LOGIN_DELAY = 1.0  # seconds, slows down password guessing


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str, iterations: int | None = None) -> str:
    """Hash in the format `pbkdf2_sha256:<iterations>:<salt>:<hash>` (without `$`, so that
    Docker Compose does not read the value in a .env file as a variable)."""
    iterations = iterations or ITERATIONS
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{ALGORITHM}:{iterations}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.strip().split(":")
        if algorithm != ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
        return hmac.compare_digest(digest, _unb64(expected))
    except (ValueError, TypeError):
        return False


def load_secret(data_dir: Path) -> bytes:
    """Key for signing sessions; created in DATA_DIR on first use."""
    path = data_dir / KEY_FILENAME
    if not path.is_file():
        path.write_bytes(secrets.token_bytes(32))
        path.chmod(0o600)
    return path.read_bytes()


class Auth:
    """Signs and checks session tokens `<user id>.<expires>.<signature>`."""

    def __init__(self, secret: bytes) -> None:
        self.secret = secret

    def _sign(self, user_id: int, expires: int, password_hash: str) -> str:
        # The password hash is part of the signature: a new password ends all sessions.
        message = f"{user_id}|{expires}|{password_hash}".encode()
        return _b64(hmac.new(self.secret, message, hashlib.sha256).digest())

    def make_token(self, user: dict[str, Any], now: float | None = None) -> str:
        expires = int(now if now is not None else time.time()) + SESSION_SECONDS
        return f"{user['id']}.{expires}.{self._sign(user['id'], expires, user['password_hash'])}"

    def user_for(
        self, token: str | None, db: Database, now: float | None = None
    ) -> dict[str, Any] | None:
        """The account a valid token belongs to, otherwise None."""
        parts = (token or "").split(".")
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            return None
        user_id, expires, signature = int(parts[0]), int(parts[1]), parts[2]
        if expires < (now if now is not None else time.time()):
            return None
        user = db.get_user(user_id)
        if user is None or not user["password_hash"]:
            return None
        if not hmac.compare_digest(signature, self._sign(user_id, expires, user["password_hash"])):
            return None
        return user


def setup_auth(settings: Settings) -> Auth:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return Auth(load_secret(settings.data_dir))


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def safe_next(target: str | None) -> str:
    """Only allow paths inside the app as redirect targets."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


def _unauthorized(message: str) -> JSONResponse:
    return JSONResponse({"detail": [{"field": None, "message": message}]}, status_code=401)


async def require_login(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Middleware: finds the signed-in account and keeps everyone else out.

    The account is put in `request.state.user` (None on public pages).
    """
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db
    path = request.url.path
    if settings.auth == "none":
        request.state.user = db.first_admin()
        return await call_next(request)
    request.state.user = None
    if is_public(path):
        return await call_next(request)
    if db.setup_needed():
        if path.startswith("/api/"):
            return _unauthorized("Please create the admin account first.")
        return RedirectResponse("/setup", status_code=303)
    user = request.app.state.auth.user_for(request.cookies.get(COOKIE_NAME), db)
    if user is not None:
        request.state.user = user
        return await call_next(request)
    if path.startswith("/api/"):
        return _unauthorized("Please log in first.")
    target = path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)


def current_user(request: Request) -> dict[str, Any]:
    """The signed-in account; only for routes behind the middleware."""
    return request.state.user


def _page(request: Request, title_key: str, form: str, status: int = 200) -> HTMLResponse:
    language = request_language(request)
    body = f"""<!doctype html>
<html lang="{language}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{escape(translate(language, title_key))} · Playshelf</title>
<link rel="stylesheet" href="/static/style.css">
</head><body>
<main class="login">
{form}
</main>
</body></html>"""
    return HTMLResponse(
        body,
        status_code=status,
        headers={"Cache-Control": "no-store", "Vary": "Accept-Language"},
    )


def _error(language: str, key: str = "", message: str = "") -> str:
    text = escape(translate(language, key)) if key else escape(message)
    return f'<p class="error" role="alert">{text}</p>' if text else ""


def login_page(
    request: Request, next_url: str, error_key: str = "", username: str = "", status: int = 200
) -> HTMLResponse:
    language = request_language(request)

    def t(key: str) -> str:
        return escape(translate(language, key))

    form = f"""  <form method="post" action="/login">
    <h1>Playshelf</h1>
    <input type="hidden" name="next" value="{escape(next_url)}">
    <label>{t("login.username")}<input name="username" required autocomplete="username"
      autocapitalize="none" spellcheck="false" value="{escape(username)}"
      {"" if username else "autofocus"}></label>
    <label>{t("login.password")}<input type="password" name="password" required
      autocomplete="current-password" {"autofocus" if username else ""}></label>
    {_error(language, error_key)}
    <button type="submit">{t("login.submit")}</button>
  </form>"""
    return _page(request, "login.title", form, status)


def setup_page(
    request: Request, username: str = "admin", message: str = "", status: int = 200
) -> HTMLResponse:
    language = request_language(request)

    def t(key: str) -> str:
        return escape(translate(language, key))

    form = f"""  <form method="post" action="/setup">
    <h1>Playshelf</h1>
    <p class="muted small-text">{t("setup.intro")}</p>
    <label>{t("login.username")}<input name="username" required autocomplete="username"
      autocapitalize="none" spellcheck="false" maxlength="32" value="{escape(username)}"></label>
    <label>{t("login.password")}<input type="password" name="password" required minlength="8"
      autocomplete="new-password" autofocus></label>
    <label>{t("setup.repeat")}<input type="password" name="repeat" required minlength="8"
      autocomplete="new-password"></label>
    {_error(language, message=message)}
    <button type="submit">{t("setup.submit")}</button>
  </form>"""
    return _page(request, "setup.title", form, status)


def set_session_cookie(request: Request, response: Response, user: dict[str, Any]) -> None:
    response.set_cookie(
        COOKIE_NAME,
        request.app.state.auth.make_token(user),
        max_age=SESSION_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.headers.get("x-forwarded-proto", request.url.scheme) == "https",
    )


def _signed_in(request: Request, user: dict[str, Any], target: str) -> RedirectResponse:
    response = RedirectResponse(target, status_code=303)
    set_session_cookie(request, response, user)
    return response


router = APIRouter(include_in_schema=False)


@router.get("/login", response_model=None)
def login_form(request: Request, next: str = "/") -> Response:
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db
    if settings.auth == "none":
        return RedirectResponse(safe_next(next), status_code=303)
    if db.setup_needed():
        return RedirectResponse("/setup", status_code=303)
    if request.app.state.auth.user_for(request.cookies.get(COOKIE_NAME), db):
        return RedirectResponse(safe_next(next), status_code=303)
    return login_page(request, safe_next(next))


@router.post("/login", response_model=None)
async def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/"),
) -> Response:
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db
    target = safe_next(next)
    if settings.auth == "none":
        return RedirectResponse(target, status_code=303)
    user = db.get_user_by_name(username)
    if (
        user is None
        or not user["password_hash"]
        or not verify_password(password, user["password_hash"])
    ):
        await asyncio.sleep(FAILED_LOGIN_DELAY)
        return login_page(request, target, "login.wrong_password", username.strip(), status=401)
    return _signed_in(request, user, target)


@router.get("/setup", response_model=None)
def setup_form(request: Request) -> Response:
    settings: Settings = request.app.state.settings
    if settings.auth == "none" or not request.app.state.db.setup_needed():
        return RedirectResponse("/", status_code=303)
    return setup_page(request)


@router.post("/setup", response_model=None)
def setup(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    repeat: str = Form(""),
) -> Response:
    """Gives the first admin account its name and password, then signs in."""
    settings: Settings = request.app.state.settings
    db: Database = request.app.state.db
    if settings.auth == "none" or not db.setup_needed():
        return RedirectResponse("/", status_code=303)
    language = request_language(request)
    try:
        name = users.clean_username(username)
        users.check_password(password)
    except users.AccountError as exc:
        message = translate(language, exc.key, **exc.params)
        return setup_page(request, username.strip(), message, status=422)
    if password != repeat:
        message = translate(language, "account.error.mismatch")
        return setup_page(request, username.strip(), message, status=422)
    admin = db.first_admin()
    user = db.update_user(admin["id"], {"username": name, "password_hash": hash_password(password)})
    users.rename_inbox_folder(settings, admin["username"], name)
    users.ensure_inbox_folders(settings, db)
    return _signed_in(request, user, "/")  # type: ignore[arg-type]


@router.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


def main(argv: list[str] | None = None) -> None:
    """Prints a password hash, or with --reset sets an account's password."""
    parser = argparse.ArgumentParser(prog="python -m app.auth", description=main.__doc__)
    parser.add_argument(
        "--reset", metavar="USERNAME", help="set a new password for this account in the database"
    )
    args = parser.parse_args(argv)
    db = user = None
    if args.reset:
        db = Database(get_settings().data_dir / DB_FILENAME)
        user = db.get_user_by_name(args.reset)
        if user is None:
            raise SystemExit(f'There is no account named "{args.reset}".')
    password = getpass.getpass("New password: ")
    try:
        users.check_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if getpass.getpass("Repeat: ") != password:
        raise SystemExit("The passwords do not match.")
    if db is not None and user is not None:
        db.update_user(user["id"], {"password_hash": hash_password(password)})
        print(f'The password of "{user["username"]}" has been changed.')
    else:
        print(f"PASSWORD_HASH={hash_password(password)}")


if __name__ == "__main__":
    main()
