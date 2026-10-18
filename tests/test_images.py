"""Cover images."""

import io
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import images, scanner
from app.config import Settings
from app.main import create_app


def png(path: Path, size: tuple[int, int] = (40, 30), color: str = "red") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)


def write(path: Path, content: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def image_bytes(size: tuple[int, int], fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "blue").save(out, fmt)
    return out.getvalue()


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    folder = settings.games_dir / "CK"
    write(folder / "index.html", "<title>CK</title>")
    png(folder / "img" / "background.png", (1600, 1000))
    png(folder / "img" / "Cover_art.jpg", (300, 200))
    png(folder / "img" / "icon.png", (16, 16))
    write(settings.games_dir / "Plain" / "index.html", "<title>Plain</title>")
    with TestClient(create_app(settings)) as client:
        yield client


def game(client: TestClient, folder: str) -> dict:
    return next(g for g in client.get("/api/games").json() if g["folder"] == folder)


# Auswahlregel


def test_keyword_beats_size_and_order_of_keywords(tmp_path: Path) -> None:
    png(tmp_path / "big.png", (500, 500))
    png(tmp_path / "a" / "title_screen.png", (100, 100))
    png(tmp_path / "a" / "b" / "cover.png", (50, 50))
    found = scanner.scan_folder(_with_html(tmp_path))
    assert found is not None
    assert found.image == "a/b/cover.png"
    assert scanner.list_images(tmp_path) == ["a/b/cover.png", "a/title_screen.png", "big.png"]


def test_largest_image_without_keyword(tmp_path: Path) -> None:
    png(tmp_path / "small.png", (10, 10))
    png(tmp_path / "img" / "bg.jpg", (400, 300))
    assert scanner.scan_folder(_with_html(tmp_path)).image == "img/bg.jpg"  # type: ignore[union-attr]


def test_no_images(tmp_path: Path) -> None:
    assert scanner.scan_folder(_with_html(tmp_path)).image is None  # type: ignore[union-attr]


def _with_html(folder: Path) -> Path:
    write(folder / "index.html")
    return folder


@pytest.mark.parametrize(
    ("mode", "current", "expected"),
    [
        ("auto", "old.png", ("auto", "cover.png")),
        ("file", "bg.png", ("file", "bg.png")),
        ("file", "gone.png", ("auto", "cover.png")),
        ("upload", None, ("upload", None)),
        ("none", None, ("none", None)),
    ],
)
def test_resolve_image(mode: str, current: str | None, expected: tuple) -> None:
    assert scanner.resolve_image(mode, current, "cover.png", ["cover.png", "bg.png"]) == expected


# API


def test_auto_image_is_chosen_on_scan_and_served_small(api: TestClient) -> None:
    kettle = game(api, "CK")
    assert (kettle["image_mode"], kettle["image"]) == ("auto", "img/Cover_art.jpg")
    response = api.get(f"/api/games/{kettle['id']}/image")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(response.content)).size == (300, 200)

    plain = game(api, "Plain")
    assert plain["image"] is None
    assert api.get(f"/api/games/{plain['id']}/image").status_code == 404


def test_thumbnail_is_shrunk_cached_and_revalidated(api: TestClient, settings: Settings) -> None:
    kettle = game(api, "CK")
    api.put(f"/api/games/{kettle['id']}/image", json={"mode": "file", "path": "img/background.png"})
    first = api.get(f"/api/games/{kettle['id']}/image")
    assert Image.open(io.BytesIO(first.content)).size == (images.THUMB_EDGE, 400)
    assert len(list(images.thumbs_dir(settings).glob(f"{kettle['id']}-*.webp"))) == 1

    etag = first.headers["etag"]
    again = api.get(f"/api/games/{kettle['id']}/image", headers={"If-None-Match": etag})
    assert again.status_code == 304

    # Changed file: new thumbnail, the old one is removed
    png(settings.games_dir / "CK" / "img" / "background.png", (800, 800), "green")
    changed = api.get(f"/api/games/{kettle['id']}/image", headers={"If-None-Match": etag})
    assert changed.status_code == 200
    assert changed.headers["etag"] != etag
    assert len(list(images.thumbs_dir(settings).glob(f"{kettle['id']}-*.webp"))) == 1


def test_choose_image_in_dialog(api: TestClient) -> None:
    kettle = game(api, "CK")
    choices = api.get(f"/api/games/{kettle['id']}/images").json()
    assert choices == {
        "auto": "img/Cover_art.jpg",
        "images": ["img/Cover_art.jpg", "img/background.png", "img/icon.png"],
    }
    url = f"/api/games/{kettle['id']}/image"

    response = api.put(url, json={"mode": "file", "path": "img/icon.png"})
    assert (response.json()["image_mode"], response.json()["image"]) == ("file", "img/icon.png")
    # The user's choice survives a rescan
    api.post("/api/scan")
    assert game(api, "CK")["image"] == "img/icon.png"

    response = api.put(url, json={"mode": "none"})
    assert (response.json()["image_mode"], response.json()["image"]) == ("none", None)
    assert api.get(url).status_code == 404
    api.post("/api/scan")
    assert game(api, "CK")["image_mode"] == "none"

    response = api.put(url, json={"mode": "auto"})
    assert response.json()["image"] == "img/Cover_art.jpg"


def test_choose_image_rejects_files_outside_the_list(api: TestClient) -> None:
    kettle = game(api, "CK")
    url = f"/api/games/{kettle['id']}/image"
    for path in ["../Plain/index.html", "index.html", "img/missing.png", None]:
        response = api.put(url, json={"mode": "file", "path": path})
        assert response.status_code == 422, path
    assert api.put(url, json={"mode": "upload"}).status_code == 422


def test_vanished_file_falls_back_to_auto(api: TestClient, settings: Settings) -> None:
    kettle = game(api, "CK")
    api.put(f"/api/games/{kettle['id']}/image", json={"mode": "file", "path": "img/icon.png"})
    (settings.games_dir / "CK" / "img" / "icon.png").unlink()
    api.post("/api/scan")
    after = game(api, "CK")
    assert (after["image_mode"], after["image"]) == ("auto", "img/Cover_art.jpg")


def test_upload_is_shrunk_and_replaces_auto(api: TestClient, settings: Settings) -> None:
    plain = game(api, "Plain")
    url = f"/api/games/{plain['id']}/image"
    response = api.post(url, content=image_bytes((3000, 1500), "JPEG"))
    assert response.status_code == 200
    assert response.json()["image_mode"] == "upload"
    stored = images.cover_path(settings, plain["id"])
    assert Image.open(stored).size == (images.COVER_EDGE, 640)
    served = api.get(url)
    assert Image.open(io.BytesIO(served.content)).size == (images.THUMB_EDGE, 320)

    # A rescan leaves the uploaded image alone
    api.post("/api/scan")
    assert game(api, "Plain")["image_mode"] == "upload"

    # Switching back to automatic deletes the uploaded image
    api.put(url, json={"mode": "auto"})
    assert not stored.exists()


def test_upload_rejects_non_images_and_too_large(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    kettle = game(api, "CK")
    url = f"/api/games/{kettle['id']}/image"
    response = api.post(url, content=b"not an image")
    assert response.status_code == 422
    assert "not a readable image" in response.json()["detail"]
    assert api.post(url, content=b"").status_code == 422
    monkeypatch.setattr(images, "MAX_UPLOAD_BYTES", 100)
    assert api.post(url, content=image_bytes((500, 500))).status_code == 413
    assert game(api, "CK")["image_mode"] == "auto"


def test_unknown_game(api: TestClient) -> None:
    assert api.get("/api/games/999/image").status_code == 404
    assert api.get("/api/games/999/images").status_code == 404
    assert api.put("/api/games/999/image", json={"mode": "auto"}).status_code == 404


def test_symlink_out_of_game_folder_is_not_served(api: TestClient, settings: Settings) -> None:
    png(settings.data_dir / "secret.png")
    (settings.games_dir / "Plain" / "cover.png").symlink_to(settings.data_dir / "secret.png")
    api.post("/api/scan")
    plain = game(api, "Plain")
    assert plain["image"] == "cover.png"
    assert api.get(f"/api/games/{plain['id']}/image").status_code == 404
