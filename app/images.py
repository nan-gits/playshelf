"""Cover images of the games.

The source is an image from the game folder (picked automatically or chosen in the
dialog) or an uploaded image. The original is never served, only a scaled-down WebP
from `DATA_DIR/thumbs` that is created on first request. Uploaded images are scaled
down right away and stored as `DATA_DIR/covers/<id>.webp`.
"""

import hashlib
import io
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app import scanner
from app.config import Settings
from app.db import Database

# Longest edge of thumbnails in pixels (tiles are about 300 px wide, doubled for HiDPI)
THUMB_EDGE = 640
# Uploaded images are kept a little larger in case the tiles grow later
COVER_EDGE = 1280
WEBP_QUALITY = 80
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
# Maximum number of images the edit dialog offers
MAX_CHOICES = 200


class ImageError(ValueError):
    """The image cannot be read or processed."""


def thumbs_dir(settings: Settings) -> Path:
    return settings.data_dir / "thumbs"


def cover_path(settings: Settings, game_id: int) -> Path:
    return settings.data_dir / "covers" / f"{game_id}.webp"


def source_path(settings: Settings, game: dict[str, Any]) -> Path | None:
    """File the cover image is made from; None if there is none."""
    if game["image_mode"] == "upload":
        path = cover_path(settings, game["id"])
        return path if path.is_file() else None
    if game["image_mode"] == "none" or not game["image"]:
        return None
    folder = (settings.games_dir / game["folder"]).resolve()
    path = (folder / game["image"]).resolve()
    # Only files inside the game folder, not even via symlinks pointing outside
    if not path.is_relative_to(folder) or not path.is_file():
        return None
    return path


def _shrink(data: bytes | Path, edge: int) -> bytes:
    """Reads an image, rotates it by EXIF, scales it down to `edge` and returns WebP."""
    try:
        with Image.open(data if isinstance(data, Path) else io.BytesIO(data)) as image:
            image.seek(0)  # first frame of a GIF
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA" if image.has_transparency_data else "RGB")
            image.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, "WEBP", quality=WEBP_QUALITY, method=4)
            return out.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ImageError("The file is not a readable image or is too large.") from exc
    except OSError as exc:
        raise ImageError(f"The image cannot be read: {exc}") from exc


def thumbnail(settings: Settings, game: dict[str, Any]) -> Path | None:
    """Path to the scaled-down cover image; creates it if needed. Raises `ImageError`."""
    source = source_path(settings, game)
    if source is None:
        return None
    stat = source.stat()
    key = hashlib.sha1(f"{source}|{stat.st_mtime_ns}|{stat.st_size}".encode()).hexdigest()[:16]
    target = thumbs_dir(settings) / f"{game['id']}-{key}.webp"
    if target.is_file():
        return target
    _write(target, _shrink(source, THUMB_EDGE))
    _remove_thumbs(settings, game["id"], keep=target)
    return target


def _remove_thumbs(settings: Settings, game_id: int, keep: Path | None = None) -> None:
    directory = thumbs_dir(settings)
    if not directory.is_dir():
        return
    for path in directory.glob(f"{game_id}-*.webp"):
        if path != keep:
            path.unlink(missing_ok=True)


def _images(settings: Settings, game: dict[str, Any]) -> list[str]:
    folder = settings.games_dir / game["folder"]
    return scanner.list_images(folder) if folder.is_dir() else []


def choices(settings: Settings, game: dict[str, Any]) -> dict[str, Any]:
    """Automatically picked image and the images in the game folder to choose from."""
    images = _images(settings, game)
    return {"auto": images[0] if images else None, "images": images[:MAX_CHOICES]}


def _write(target: Path, data: bytes) -> None:
    """Writes to a temporary file first, so a half-written file is never served."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{uuid.uuid4().hex}.tmp")
    temp.write_bytes(data)
    temp.replace(target)


def set_mode(
    settings: Settings, db: Database, game: dict[str, Any], mode: str, path: str | None = None
) -> dict[str, Any]:
    """Chooses the cover image: "auto", "file" (with `path`) or "none"."""
    available = choices(settings, game)
    if mode == "auto":
        image = available["auto"]
    elif mode == "file":
        if path not in available["images"]:
            raise ImageError("The image must be an image file in the game folder.")
        image = path
    elif mode == "none":
        image = None
    else:
        raise ImageError("Unknown choice for the cover image.")
    cover_path(settings, game["id"]).unlink(missing_ok=True)
    _remove_thumbs(settings, game["id"])
    return db.set_image(game["id"], mode, image)  # type: ignore[return-value]


def upload(settings: Settings, db: Database, game: dict[str, Any], data: bytes) -> dict[str, Any]:
    """Stores an uploaded image, scaled down, as the game's cover image."""
    if not data:
        raise ImageError("No file was received.")
    _write(cover_path(settings, game["id"]), _shrink(data, COVER_EDGE))
    _remove_thumbs(settings, game["id"])
    return db.set_image(game["id"], "upload", None)  # type: ignore[return-value]


def refresh(settings: Settings, db: Database, game: dict[str, Any]) -> dict[str, Any]:
    """Applies the image rules again after an import, update or restore."""
    if game["image_mode"] not in ("auto", "file"):
        return game
    images = _images(settings, game)
    mode, image = scanner.resolve_image(
        game["image_mode"], game["image"], images[0] if images else None, images
    )
    if (mode, image) == (game["image_mode"], game["image"]):
        return game
    return db.set_image(game["id"], mode, image)  # type: ignore[return-value]


def forget(settings: Settings, game_id: int) -> None:
    """Removes the uploaded image and thumbnails of a deleted entry."""
    cover_path(settings, game_id).unlink(missing_ok=True)
    _remove_thumbs(settings, game_id)
