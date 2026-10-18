import io
import shutil
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import imports, scanner
from app.config import Settings
from app.main import create_app


def write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def zip_bytes(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


LANTERNS_UPDATE = {
    "Paper Lanterns 1.5.0/Paper Lanterns.html": "<title>PL</title>new",
    "Paper Lanterns 1.5.0/media/new.png": "png",
}


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    write(settings.games_dir / "Paper Lanterns 1.4.4" / "Paper Lanterns.html", "<title>PL</title>")
    write(settings.games_dir / "CK" / "index.html", "<title>Copper Kettle</title>")
    with TestClient(create_app(settings)) as client:
        yield client


def upload(client: TestClient, files: dict[str, str], filename: str = "download.zip"):
    return client.post("/api/imports", params={"filename": filename}, content=zip_bytes(files))


def game(client: TestClient, folder: str) -> dict:
    return next(g for g in client.get("/api/games").json() if g["folder"] == folder)


def test_upload_creates_pending_import_with_update_suggestion(
    api: TestClient, settings: Settings
) -> None:
    response = upload(api, LANTERNS_UPDATE)
    assert response.status_code == 201
    imp = response.json()
    assert imp["folder"] == "Paper Lanterns 1.5.0"
    assert imp["filename"] == "download"
    assert imp["title"] == "PL"
    assert imp["entry"] == "Paper Lanterns.html"
    assert imp["version_hint"] == "1.5.0"
    assert imp["html_files"] == ["Paper Lanterns.html"]
    assert imp["size"] > 0
    lanterns = game(api, "Paper Lanterns 1.4.4")
    assert imp["suggestion"] == {
        "game_id": lanterns["id"],
        "title": "PL",
        "version": None,
        "matches": ["folder", "title", "entry"],
        "reason": 'Same folder name, same title and same start file as "PL"',
    }
    assert (settings.imports_dir / imp["staging"] / imp["folder"]).is_dir()
    assert api.get("/api/imports").json() == [imp]
    # The uploaded archive itself is not kept
    assert not list(settings.imports_dir.glob("*.zip"))


def test_suggestion_by_title_only(api: TestClient) -> None:
    imp = upload(api, {"x/index.html": "<title>Copper Kettle</title>"}, "kettle.zip").json()
    assert imp["suggestion"]["reason"] == 'Same title as "Copper Kettle"'
    assert imp["suggestion"]["matches"] == ["title"]


def test_unrelated_archive_has_no_suggestion(api: TestClient) -> None:
    imp = upload(api, {"index.html": "<title>Brand new</title>"}, "New 0.1.zip").json()
    assert imp["suggestion"] is None
    assert imp["folder"] == "New 0.1"


def test_rejected_upload_explains_why(api: TestClient, settings: Settings) -> None:
    response = upload(api, {"Game/Game.exe": "MZ", "Game/index.html": "x"}, "Game.zip")
    assert response.status_code == 422
    assert response.json()["detail"].startswith('"Game.zip" was not imported: ')
    assert api.get("/api/imports").json() == []
    assert list(settings.imports_dir.iterdir()) == []


def test_broken_and_empty_uploads(api: TestClient) -> None:
    response = api.post("/api/imports", params={"filename": "x.zip"}, content=b"broken")
    assert response.status_code == 422
    assert "not a valid ZIP" in response.json()["detail"]
    response = api.post("/api/imports", content=b"")
    assert response.status_code == 422
    assert response.json()["detail"] == "No file was received."


def test_upload_size_limit(settings: Settings) -> None:
    settings.max_upload_mb = 1
    too_big = b"\0" * (1024 * 1024 + 1)
    with TestClient(create_app(settings)) as client:
        # With Content-Length it is rejected at once, without it while counting
        for content in (too_big, iter([too_big[:1000], too_big[1000:]])):
            response = client.post("/api/imports", content=content)
            assert response.status_code == 413
            assert "maximum is 1 MB" in response.json()["detail"]
    assert list(settings.imports_dir.iterdir()) == []


def test_confirm_as_new_game(api: TestClient, settings: Settings) -> None:
    imp = upload(api, {"New 0.1/index.html": "<title>New</title>"}).json()
    response = api.post(
        f"/api/imports/{imp['id']}/confirm",
        json={"version": " 0.1 ", "source": "itch.io", "update_url": "https://x.itch.io/new"},
    )
    assert response.status_code == 200
    new = response.json()
    assert new["folder"] == "New 0.1"
    assert new["version"] == "0.1"
    assert new["version_hint"] == "0.1"
    assert new["source"] == "itch.io"
    assert new["title"] == "New"
    assert (settings.games_dir / "New 0.1" / "index.html").is_file()
    assert api.get("/api/imports").json() == []
    assert list(settings.imports_dir.iterdir()) == []
    assert not [p for p in settings.games_dir.iterdir() if p.name.startswith(".")]
    # The game can be started right away
    assert api.get(f"/play/{new['id']}", follow_redirects=False).status_code == 303


def test_confirm_new_game_with_existing_folder_name(api: TestClient, settings: Settings) -> None:
    imp = upload(api, {"CK/index.html": "<title>Other CK</title>"}).json()
    new = api.post(
        f"/api/imports/{imp['id']}/confirm", json={"version": "1", "title": "CK 2"}
    ).json()
    assert new["folder"] == "CK (2)"
    assert new["title"] == "CK 2"
    assert (settings.games_dir / "CK" / "index.html").read_text() == "<title>Copper Kettle</title>"


def test_confirm_with_other_entry(api: TestClient) -> None:
    imp = upload(api, {"G/index.html": "a", "G/alt/start.html": "b"}).json()
    assert imp["html_files"] == ["alt/start.html", "index.html"]
    bad = api.post(f"/api/imports/{imp['id']}/confirm", json={"version": "1", "entry": "x.html"})
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["field"] == "entry"
    new = api.post(
        f"/api/imports/{imp['id']}/confirm", json={"version": "1", "entry": "alt/start.html"}
    ).json()
    assert new["entry"] == "alt/start.html"


def test_confirm_requires_version(api: TestClient) -> None:
    imp = upload(api, {"index.html": "x"}).json()
    for body in ({}, {"version": "  "}):
        response = api.post(f"/api/imports/{imp['id']}/confirm", json=body)
        assert response.status_code == 422
        assert response.json()["detail"][0]["field"] == "version"
    assert len(api.get("/api/imports").json()) == 1


def test_confirm_with_unknown_game_or_import(api: TestClient) -> None:
    imp = upload(api, {"index.html": "x"}).json()
    response = api.post(f"/api/imports/{imp['id']}/confirm", json={"version": "1", "game_id": 999})
    assert response.status_code == 422
    assert response.json()["detail"][0]["field"] == "game_id"
    assert api.post("/api/imports/999/confirm", json={"version": "1"}).status_code == 404


def test_discard_removes_staging(api: TestClient, settings: Settings) -> None:
    imp = upload(api, LANTERNS_UPDATE).json()
    assert api.delete(f"/api/imports/{imp['id']}").status_code == 204
    assert api.get("/api/imports").json() == []
    assert list(settings.imports_dir.iterdir()) == []
    assert api.delete(f"/api/imports/{imp['id']}").status_code == 404


def test_cleanup_on_startup(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        imp = upload(client, {"index.html": "x"}).json()
        gone = upload(client, {"index.html": "y"}, "two.zip").json()
    (settings.imports_dir / "leftover").mkdir()
    (settings.imports_dir / "half.zip").write_bytes(b"x")

    shutil.rmtree(settings.imports_dir / gone["staging"])
    with TestClient(create_app(settings)) as client:
        assert [i["id"] for i in client.get("/api/imports").json()] == [imp["id"]]
    assert [p.name for p in settings.imports_dir.iterdir()] == [imp["staging"]]


def test_move_into_library_rolls_back(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.ensure_directories()
    source = tmp_path / "staged" / "Game"
    write(source / "index.html", "x")

    def fail(*_args):
        raise OSError("broken")

    monkeypatch.setattr(imports.os, "rename", fail)
    with pytest.raises(OSError):
        imports.move_into_library(settings, source, "Game")
    assert (source / "index.html").is_file()
    assert list(settings.games_dir.iterdir()) == []


@pytest.mark.parametrize(
    ("imp", "expected"),
    [
        ({"folder": "Other", "title": "Other", "entry": "index.html"}, None),
        ({"folder": "Other", "title": "Other", "entry": "Game.html"}, "Same start file"),
        ({"folder": "game-v2", "title": "Other", "entry": "index.html"}, "Same folder name"),
    ],
)
def test_suggest_rules(imp: dict, expected: str | None) -> None:
    games = [{"id": 1, "folder": "Game v1", "title": "Game", "entry": "Game.html", "version": "1"}]
    result = imports.suggest(imp, games)
    assert (result["reason"].split(" as ")[0] if result else None) == expected


def test_suggest_prefers_strongest_match() -> None:
    games = [
        {"id": 1, "folder": "A", "title": "Shared", "entry": "index.html", "version": None},
        {"id": 2, "folder": "Game 0.1", "title": "X", "entry": "index.html", "version": None},
    ]
    imp = {"folder": "Game 0.2", "title": "Shared", "entry": "index.html"}
    assert imports.suggest(imp, games)["game_id"] == 2


# Update with backup


def prepare_lanterns(client: TestClient) -> dict:
    """Game "PL" with maintained data and one start."""
    lanterns = game(client, "Paper Lanterns 1.4.4")
    client.patch(
        f"/api/games/{lanterns['id']}",
        json={
            "version": "1.4.4",
            "title": "Paper Lanterns",
            "source": "F95Zone",
            "update_url": "https://f95zone.to/threads/lanterns",
            "notes": "Favourite game",
        },
    )
    client.get(f"/play/{lanterns['id']}", follow_redirects=False)
    return client.get(f"/api/games/{lanterns['id']}").json()


def apply_update(client: TestClient, files: dict[str, str], game_id: int, version: str, **kw):
    imp = upload(client, files).json()
    body = {"game_id": game_id, "version": version, **kw}
    return client.post(f"/api/imports/{imp['id']}/confirm", json=body)


def test_update_keeps_metadata_and_backs_up_old_version(
    api: TestClient, settings: Settings
) -> None:
    before = prepare_lanterns(api)
    response = apply_update(api, LANTERNS_UPDATE, before["id"], "1.5.0")
    assert response.status_code == 200
    after = response.json()
    for key in ("id", "title", "source", "update_url", "notes", "last_played", "play_count"):
        assert after[key] == before[key], key
    assert after["play_count"] == 1
    assert after["version"] == "1.5.0"
    assert after["version_hint"] == "1.5.0"
    assert after["folder"] == "Paper Lanterns 1.5.0"
    assert after["entry"] == "Paper Lanterns.html"

    games = settings.games_dir
    assert sorted(p.name for p in games.iterdir()) == ["CK", "Paper Lanterns 1.5.0"]
    assert (games / "Paper Lanterns 1.5.0" / "media" / "new.png").is_file()
    backup = settings.backups_dir / "Paper Lanterns" / "1.4.4"
    assert (backup / "Paper Lanterns.html").read_text() == "<title>PL</title>"
    assert api.get("/api/imports").json() == []
    assert list(settings.imports_dir.iterdir()) == []

    history = api.get(f"/api/games/{after['id']}/history").json()
    assert [(h["version"], h["action"]) for h in history["history"]] == [
        ("1.5.0", "update"),
        ("1.4.4", "edited"),
    ]
    assert [(b["version"], b["folder"]) for b in history["backups"]] == [
        ("1.4.4", "Paper Lanterns 1.4.4")
    ]


def test_update_keeps_manually_chosen_entry(api: TestClient, settings: Settings) -> None:
    write(settings.games_dir / "CK" / "start.html", "x")
    kettle = game(api, "CK")
    api.patch(f"/api/games/{kettle['id']}", json={"version": "1", "entry": "start.html"})
    files = {"CK 2/index.html": "<title>Copper Kettle</title>", "CK 2/start.html": "y"}
    updated = apply_update(api, files, kettle["id"], "2").json()
    assert updated["entry"] == "start.html"
    assert updated["folder"] == "CK 2"
    # Without the chosen file detection applies; an explicit choice always wins
    updated = apply_update(api, {"CK/index.html": "a", "CK/b.html": "b"}, kettle["id"], "3").json()
    assert updated["entry"] == "index.html"
    updated = apply_update(
        api, {"CK/index.html": "a", "CK/b.html": "b"}, kettle["id"], "4", entry="b.html"
    ).json()
    assert updated["entry"] == "b.html"
    assert updated["folder"] == "CK"


def test_update_with_same_folder_name(api: TestClient, settings: Settings) -> None:
    kettle = game(api, "CK")
    updated = apply_update(
        api, {"CK/index.html": "<title>Copper Kettle</title>new"}, kettle["id"], "2"
    )
    assert updated.json()["folder"] == "CK"
    assert "new" in (settings.games_dir / "CK" / "index.html").read_text()
    assert (settings.backups_dir / "Copper Kettle" / "no version" / "index.html").is_file()


def test_update_of_game_with_missing_folder(api: TestClient, settings: Settings) -> None:
    kettle = game(api, "CK")
    shutil.rmtree(settings.games_dir / "CK")
    api.post("/api/scan")
    updated = apply_update(api, {"CK/index.html": "new"}, kettle["id"], "2").json()
    assert updated["missing"] is False
    assert api.get(f"/api/games/{kettle['id']}/history").json()["backups"] == []


@pytest.mark.parametrize("failing_step", ["rename_new", "backup", "database"])
def test_failed_update_leaves_old_version_playable(
    api: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch, failing_step: str
) -> None:
    before = prepare_lanterns(api)
    imp = upload(api, LANTERNS_UPDATE).json()
    real_rename, real_move = imports.os.rename, imports.shutil.move

    def rename(src, dst):
        if failing_step == "rename_new" and Path(src).name.startswith(".import-"):
            raise OSError("Disk full")
        return real_rename(src, dst)

    def move(src, dst):
        if failing_step == "backup" and Path(src).name.startswith(".old-"):
            raise OSError("Disk full")
        return real_move(src, dst)

    def replace_version(*_args, **_kwargs):
        raise RuntimeError("Database locked")

    monkeypatch.setattr(imports.os, "rename", rename)
    monkeypatch.setattr(imports.shutil, "move", move)
    if failing_step == "database":
        monkeypatch.setattr(api.app.state.db, "replace_version", replace_version)

    with pytest.raises((OSError, RuntimeError)):
        api.post(
            f"/api/imports/{imp['id']}/confirm", json={"game_id": before["id"], "version": "2"}
        )

    assert sorted(p.name for p in settings.games_dir.iterdir()) == ["CK", "Paper Lanterns 1.4.4"]
    assert api.get(f"/api/games/{before['id']}").json() == before
    assert api.get(f"/play/{before['id']}", follow_redirects=False).status_code == 303
    assert not settings.backups_dir.exists() or not any(settings.backups_dir.rglob("*.html"))
    # The import is still pending and can be confirmed again
    monkeypatch.undo()
    response = api.post(
        f"/api/imports/{imp['id']}/confirm", json={"game_id": before["id"], "version": "2"}
    )
    assert response.status_code == 200


def test_restore_previous_version(api: TestClient, settings: Settings) -> None:
    before = prepare_lanterns(api)
    apply_update(api, LANTERNS_UPDATE, before["id"], "1.5.0")
    response = api.post(f"/api/games/{before['id']}/restore")
    assert response.status_code == 200
    restored = response.json()
    assert restored["version"] == "1.4.4"
    assert restored["folder"] == "Paper Lanterns 1.4.4"
    assert restored["entry"] == "Paper Lanterns.html"
    assert restored["notes"] == "Favourite game"
    assert (settings.games_dir / "Paper Lanterns 1.4.4" / "Paper Lanterns.html").is_file()
    assert not (settings.games_dir / "Paper Lanterns 1.5.0").exists()

    history = api.get(f"/api/games/{before['id']}/history").json()
    assert history["history"][0] == {**history["history"][0], "version": "1.4.4"}
    assert history["history"][0]["action"] == "restored"
    # The replaced 1.5.0 is now a backup in turn, so it is possible to switch back
    assert [b["version"] for b in history["backups"]] == ["1.5.0"]
    again = api.post(
        f"/api/games/{before['id']}/restore", json={"backup_id": history["backups"][0]["id"]}
    ).json()
    assert again["version"] == "1.5.0"
    assert (settings.games_dir / "Paper Lanterns 1.5.0" / "media" / "new.png").is_file()


def test_restore_without_backup(api: TestClient) -> None:
    kettle = game(api, "CK")
    response = api.post(f"/api/games/{kettle['id']}/restore")
    assert response.status_code == 404
    assert api.post(f"/api/games/{kettle['id']}/restore", json={"backup_id": 5}).status_code == 404
    assert api.post("/api/games/999/restore").status_code == 404


def test_old_backups_are_pruned(settings: Settings) -> None:
    settings.keep_backups = 2
    write(settings.games_dir / "G" / "index.html", "<title>G</title>")
    with TestClient(create_app(settings)) as client:
        gid = game(client, "G")["id"]
        for version in ("2", "3", "4", "5"):
            apply_update(client, {"G/index.html": f"<title>G</title>{version}"}, gid, version)
        backups = client.get(f"/api/games/{gid}/history").json()["backups"]
    assert [b["version"] for b in backups] == ["4", "3"]
    assert sorted(p.name for p in (settings.backups_dir / "G").iterdir()) == ["3", "4"]


def test_history_api(api: TestClient) -> None:
    kettle = game(api, "CK")
    assert api.get(f"/api/games/{kettle['id']}/history").json() == {"history": [], "backups": []}
    api.patch(f"/api/games/{kettle['id']}", json={"version": "1"})
    api.patch(f"/api/games/{kettle['id']}", json={"version": "1", "notes": "x"})
    history = api.get(f"/api/games/{kettle['id']}/history").json()["history"]
    assert [(h["version"], h["action"]) for h in history] == [("1", "edited")]
    assert api.get("/api/games/999/history").status_code == 404


def test_new_game_starts_history(api: TestClient) -> None:
    imp = upload(api, {"New/index.html": "x"}).json()
    new = api.post(f"/api/imports/{imp['id']}/confirm", json={"version": "0.1"}).json()
    history = api.get(f"/api/games/{new['id']}/history").json()["history"]
    assert [(h["version"], h["action"]) for h in history] == [("0.1", "new")]


def test_update_and_restore_keep_sizes(api: TestClient, settings: Settings) -> None:
    before = prepare_lanterns(api)
    old_size = before["size"]
    after = apply_update(api, LANTERNS_UPDATE, before["id"], "1.5.0").json()
    assert after["size"] == scanner.folder_size(settings.games_dir / after["folder"])
    assert after["size"] != old_size

    result = api.get("/api/stats").json()
    lanterns = next(g for g in result["by_size"] if g["id"] == before["id"])
    assert (lanterns["size"], lanterns["backups_size"], lanterns["backups"]) == (
        after["size"],
        old_size,
        1,
    )

    restored = api.post(f"/api/games/{before['id']}/restore").json()
    assert restored["size"] == old_size
    lanterns = next(g for g in api.get("/api/stats").json()["by_size"] if g["id"] == before["id"])
    assert lanterns["backups_size"] == after["size"]


def test_update_refreshes_automatic_image(api: TestClient) -> None:
    before = prepare_lanterns(api)
    assert before["image"] is None
    after = apply_update(api, LANTERNS_UPDATE, before["id"], "1.5.0").json()
    assert (after["image_mode"], after["image"]) == ("auto", "media/new.png")
