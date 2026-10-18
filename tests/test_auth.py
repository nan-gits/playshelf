import pytest
from fastapi.testclient import TestClient

from app.auth import COOKIE_NAME, Auth, hash_password, main, safe_next, verify_password
from app.config import Settings
from app.db import DB_FILENAME, Database
from app.main import create_app

PASSWORD = "geheim-genug"


@pytest.fixture(scope="module")
def password_hash() -> str:
    return hash_password(PASSWORD, iterations=1000)


@pytest.fixture
def protected(settings: Settings, password_hash: str, monkeypatch):
    """Accounts on, with the password of older versions taken over by `admin`."""
    monkeypatch.setattr("app.auth.FAILED_LOGIN_DELAY", 0)
    settings.auth = "accounts"
    settings.password_hash = password_hash
    game = settings.games_dir / "Game 1_0"
    game.mkdir(parents=True)
    (game / "index.html").write_text("<title>Game</title>")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def fresh(settings: Settings, monkeypatch):
    """Accounts on, no admin password yet: the first start."""
    monkeypatch.setattr("app.auth.FAILED_LOGIN_DELAY", 0)
    monkeypatch.setattr("app.auth.ITERATIONS", 1000)
    settings.auth = "accounts"
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def login(client: TestClient, password: str = PASSWORD, next_url: str = "/", username="admin"):
    return client.post(
        "/login",
        data={"username": username, "password": password, "next": next_url},
        follow_redirects=False,
    )


def test_hash_roundtrip(password_hash: str) -> None:
    assert verify_password(PASSWORD, password_hash)
    assert not verify_password("falsch", password_hash)
    assert not verify_password(PASSWORD, "kaputt")
    assert "$" not in password_hash  # Docker Compose would read $ as a variable


def test_token_expires_and_is_bound_to_password(tmp_path, password_hash: str) -> None:
    db = Database(tmp_path / DB_FILENAME)
    user = db.update_user(1, {"password_hash": password_hash})
    auth = Auth(b"x" * 32)
    token = auth.make_token(user, now=1000)
    assert auth.user_for(token, db, now=2000)["id"] == 1
    assert auth.user_for(token, db, now=1000 + 31 * 24 * 3600) is None
    assert auth.user_for(token + "x", db, now=2000) is None
    assert auth.user_for("2" + token[1:], db, now=2000) is None  # other account
    assert Auth(b"y" * 32).user_for(token, db, now=2000) is None
    assert auth.user_for(None, db) is None and auth.user_for("a.b.c", db) is None
    db.update_user(1, {"password_hash": hash_password("anderes-pw", 1000)})
    assert auth.user_for(token, db, now=2000) is None


@pytest.mark.parametrize(
    ("target", "expected"),
    [("/play/1", "/play/1"), ("//evil.example", "/"), ("https://evil.example", "/"), (None, "/")],
)
def test_safe_next(target, expected) -> None:
    assert safe_next(target) == expected


def test_no_login_mode(client: TestClient) -> None:
    assert client.get("/api/games").status_code == 200
    me = client.get("/api/me").json()
    assert me["accounts"] is False
    assert me["user"]["username"] == "admin" and me["user"]["is_admin"]
    for path in ["/login", "/setup"]:
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 303 and response.headers["location"] == "/"


def test_first_start_asks_for_an_admin(fresh: TestClient, settings: Settings) -> None:
    assert fresh.get("/api/games").status_code == 401
    for path in ["/", "/login", "/play/1"]:
        response = fresh.get(path, follow_redirects=False)
        assert response.headers["location"] == "/setup", path
    assert "Create the admin account" in fresh.get("/setup").text

    form = {"username": "Alice", "password": "kurz", "repeat": "kurz"}
    response = fresh.post("/setup", data=form)
    assert response.status_code == 422 and "at least 8 characters" in response.text
    form = {"username": "alice", "password": PASSWORD, "repeat": "anders-anders"}
    assert "do not match" in fresh.post("/setup", data=form).text
    form = {"username": "failed", "password": PASSWORD, "repeat": PASSWORD}
    assert "reserved" in fresh.post("/setup", data=form).text
    german = fresh.post("/setup", data=form, headers={"Accept-Language": "de"})
    assert "reserviert" in german.text

    form = {"username": " Alice ", "password": PASSWORD, "repeat": PASSWORD}
    response = fresh.post("/setup", data=form, follow_redirects=False)
    assert response.status_code == 303 and COOKIE_NAME in response.cookies
    me = fresh.get("/api/me").json()["user"]
    assert (me["id"], me["username"], me["role"]) == (1, "alice", "admin")
    assert (settings.inbox_dir / "alice").is_dir()
    # Setup happens only once
    assert fresh.get("/setup", follow_redirects=False).headers["location"] == "/"
    fresh.post("/logout")
    assert login(fresh, username="alice").status_code == 303


def test_old_password_becomes_admin(protected: TestClient) -> None:
    assert protected.get("/setup", follow_redirects=False).headers["location"] == "/"
    assert protected.get("/api/games").status_code == 401
    assert login(protected).status_code == 303
    assert protected.get("/api/me").json()["user"]["username"] == "admin"
    # Existing games stay with the admin
    assert [g["folder"] for g in protected.get("/api/games").json()] == ["Game 1_0"]


def test_protected_without_login(protected: TestClient) -> None:
    assert protected.get("/api/games").status_code == 401
    assert protected.post("/api/scan").status_code == 401
    assert protected.get("/api/games").json()["detail"][0]["message"]

    for path in ["/", "/play/1", "/games/Game%201_0/index.html"]:
        response = protected.get(path, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/login?next=")


def test_public_paths_stay_open(protected: TestClient) -> None:
    assert protected.get("/api/health").status_code == 200
    assert protected.get("/static/style.css").status_code == 200
    page = protected.get("/login").text
    assert "User name" in page and "Password" in page
    german = protected.get("/login", headers={"Accept-Language": "de-DE,de;q=0.9"})
    assert 'lang="de"' in german.text and "Passwort" in german.text


@pytest.mark.parametrize(("username", "password"), [("admin", "falsch"), ("niemand", PASSWORD)])
def test_wrong_login(protected: TestClient, username: str, password: str) -> None:
    response = login(protected, password, username=username)
    assert response.status_code == 401
    assert "user name or password is wrong" in response.text
    assert f'value="{username}"' in response.text  # the name stays filled in
    assert COOKIE_NAME not in response.cookies
    assert protected.get("/api/games").status_code == 401


def test_login_session_and_logout(protected: TestClient) -> None:
    response = login(protected, next_url="/games/Game%201_0/index.html", username=" ADMIN ")
    assert response.status_code == 303
    assert response.headers["location"] == "/games/Game%201_0/index.html"
    assert "httponly" in response.headers["set-cookie"].lower()

    assert protected.get("/api/games").status_code == 200
    assert protected.get("/api/me").json()["accounts"] is True
    assert protected.get("/games/Game%201_0/index.html").status_code == 200

    protected.post("/logout", follow_redirects=False)
    assert protected.get("/api/games").status_code == 401


def test_login_rejects_foreign_redirect(protected: TestClient) -> None:
    response = login(protected, next_url="https://evil.example/")
    assert response.headers["location"] == "/"


def test_session_key_is_private(protected: TestClient, settings: Settings) -> None:
    key = settings.data_dir / "session.key"
    assert key.is_file() and oct(key.stat().st_mode & 0o777) == "0o600"


def test_reset_password_from_the_command_line(settings: Settings, monkeypatch, capsys) -> None:
    db = Database(settings.data_dir / DB_FILENAME)
    monkeypatch.setattr("app.auth.get_settings", lambda: settings)
    monkeypatch.setattr("app.auth.ITERATIONS", 1000)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "ein-neues-passwort")
    main(["--reset", "ADMIN"])
    assert "has been changed" in capsys.readouterr().out
    assert verify_password("ein-neues-passwort", db.get_user(1)["password_hash"])
    with pytest.raises(SystemExit, match="no account"):
        main(["--reset", "niemand"])
    main([])
    assert capsys.readouterr().out.startswith("PASSWORD_HASH=pbkdf2_sha256:")
