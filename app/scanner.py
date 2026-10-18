"""Finds HTML games in the games folder and detects their start file.

Every subfolder of GAMES_DIR is a game candidate. Folders without an HTML file
or with an `.exe` (Ren'Py, RPG Maker, NW.js etc.) are skipped.
"""

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from stat import S_ISREG

# Folders and files that never belong to the game or are never the start file
IGNORED_DIRS = {"__macosx", ".git", "node_modules"}
IGNORED_STEMS = {"changelog", "credits", "head", "readme", "license"}
HTML_SUFFIXES = (".html", ".htm")
# Possible cover images
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")
# Images with these words in their name are preferred, in this order
IMAGE_KEYWORDS = ("cover", "poster", "banner", "title", "splash", "thumb", "preview")
# Number of folder levels below the game folder that are searched
MAX_DEPTH = 3
# The <title> is in the <head>, near the start; do not read large game files completely
TITLE_READ_BYTES = 512 * 1024

# Version patterns in folder names, tried in this order
_VERSION_PATTERNS = [
    # 1.4.4, 0.191.0, also attached to the name: StarHarbor0.6.1
    re.compile(r"(?<![\d.])(\d+(?:\.\d+)+)(?![\d.])"),
    # 0_11, 0_4_7, only as a separate word: Quiet Valley 0_11, moon_garden_0_4_7
    re.compile(r"(?:^|[\s\-(_])(\d+(?:_\d+)+)(?=$|[\s\-)])"),
    # v19, V263, release_3, only with a leading v or release
    re.compile(r"(?:^|[\s\-(_])(?:v|release[\s_]?)\s?(\d+)(?=$|[\s\-)_])", re.IGNORECASE),
]
# Name parts that do not belong to the game name
_NOISE = re.compile(r"\b(?:pc|public|hotfix\d*|release|v)\b", re.IGNORECASE)


@dataclass(frozen=True)
class FoundGame:
    folder: str  # name of the game folder directly below GAMES_DIR
    entry: str  # start file relative to the game folder, with /
    title: str
    version_hint: str | None
    html_files: tuple[str, ...] = field(default=())  # all candidates, relative, with /
    size: int = 0  # disk usage of the whole folder in bytes
    image: str | None = None  # automatically picked cover image, relative, with /
    images: tuple[str, ...] = field(default=())  # all images, relative, with /


def _walk(folder: Path) -> tuple[list[Path], list[Path], bool]:
    """Returns the HTML candidates and images in the folder and whether an .exe was found."""
    html: list[Path] = []
    images: list[Path] = []
    has_exe = False
    stack = [(folder, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            children = list(current.iterdir())
        except OSError:
            continue
        for child in children:
            name = child.name.lower()
            if name.startswith((".", "._")):
                continue
            if child.is_dir():
                if depth < MAX_DEPTH and name not in IGNORED_DIRS:
                    stack.append((child, depth + 1))
            elif name.endswith(".exe"):
                has_exe = True
            elif name.endswith(HTML_SUFFIXES) and child.stem.lower() not in IGNORED_STEMS:
                html.append(child)
            elif name.endswith(IMAGE_SUFFIXES):
                images.append(child)
    return html, images, has_exe


def list_html_files(folder: Path) -> list[str]:
    """All possible start files of a game folder, relative and sorted."""
    html, _, _ = _walk(folder)
    return _relative(folder, html)


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _keyword_rank(path: Path) -> int:
    """Position of the first keyword in the file name, otherwise len(IMAGE_KEYWORDS)."""
    stem = path.stem.lower()
    return next((i for i, word in enumerate(IMAGE_KEYWORDS) if word in stem), len(IMAGE_KEYWORDS))


def _rank_images(images: list[Path]) -> list[Path]:
    """Preferred images first: by keyword, then the largest."""
    return sorted(images, key=lambda p: (_keyword_rank(p), -_file_size(p), p.as_posix().lower()))


def pick_image(folder: Path, images: list[Path]) -> str | None:
    """Cover image by a fixed rule: an image with "cover", "poster", "banner", "title",
    "splash", "thumb" or "preview" in its name (in this order, the largest if there are
    several), otherwise the largest image in the folder."""
    if not images:
        return None
    return _rank_images(images)[0].relative_to(folder).as_posix()


def list_images(folder: Path) -> list[str]:
    """All images of a game folder, preferred first; the first is the automatic choice."""
    _, images, _ = _walk(folder)
    return [p.relative_to(folder).as_posix() for p in _rank_images(images)]


def _relative(folder: Path, paths: list[Path]) -> list[str]:
    return sorted((p.relative_to(folder).as_posix() for p in paths), key=str.lower)


def _depth(folder: Path, path: Path) -> int:
    return len(path.relative_to(folder).parts)


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _similar(a: str, b: str) -> bool:
    a, b = _key(a), _key(b)
    return bool(a) and bool(b) and (a in b or b in a)


def resolve_image(
    mode: str, current: str | None, auto: str | None, images: Iterable[str]
) -> tuple[str, str | None]:
    """New image mode and path after a scan.

    "auto" takes the automatically picked image, "file" keeps the user's choice as
    long as the file still exists (otherwise back to "auto"). An uploaded image
    ("upload") and "none" stay unchanged.
    """
    if mode == "auto" or (mode == "file" and current not in set(images)):
        return "auto", auto
    return mode, current


def pick_entry(folder: Path, candidates: list[Path]) -> Path | None:
    """Shallowest index.html, otherwise a file with a similar name, otherwise the largest."""
    if not candidates:
        return None
    index = [p for p in candidates if p.stem.lower() == "index"]
    if index:
        return min(index, key=lambda p: (_depth(folder, p), p.as_posix().lower()))

    folder_name = strip_version(folder.name)

    def similar(p: Path) -> bool:
        # Intermediate folders count too: Lark/Lantern_v0.34/Lantern.html
        names = [folder_name, *(strip_version(d) for d in p.relative_to(folder).parts[:-1])]
        return any(_similar(name, p.stem) for name in names)

    def size(p: Path) -> int:
        try:
            return p.stat().st_size
        except OSError:
            return 0

    matching = [p for p in candidates if similar(p)]
    if matching:
        return min(matching, key=lambda p: (_depth(folder, p), -size(p)))
    return max(candidates, key=size)


def folder_size(folder: Path) -> int:
    """Disk usage of all files below `folder` in bytes; symlinks do not count."""
    total = 0
    for root, _, files in os.walk(folder):
        for name in files:
            try:
                info = os.lstat(os.path.join(root, name))
            except OSError:
                continue
            if S_ISREG(info.st_mode):
                total += info.st_size
    return total


def version_hint(name: str) -> str | None:
    """Version hint from a folder name, for example "Quiet Valley 0_11" → "0.11"."""
    for pattern in _VERSION_PATTERNS:
        match = pattern.search(name)
        if match:
            return match.group(1).replace("_", ".")
    return None


def strip_version(name: str) -> str:
    """Folder name without version and suffixes such as "pc" or "hotfix1"."""
    cleaned = name
    for pattern in _VERSION_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = _NOISE.sub(" ", cleaned.replace("_", " "))
    cleaned = re.sub(r"\(\s*\)", " ", cleaned)
    return re.sub(r"[\s\-]+", " ", cleaned).strip(" -()")


def read_title(path: Path) -> str | None:
    try:
        with path.open("rb") as file:
            head = file.read(TITLE_READ_BYTES).decode("utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"<title[^>]*>(.*?)</title>", head, re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    title = re.sub(r"\s+", " ", unescape(match.group(1))).strip()
    return title or None


def guess_title(folder: Path, entry: Path) -> str:
    title = read_title(entry)
    if title:
        return title
    if entry.stem.lower() != "index":
        return entry.stem
    return strip_version(folder.name) or folder.name


def scan_folder(folder: Path) -> FoundGame | None:
    """Examines a game folder; None if it is not an HTML game."""
    candidates, images, has_exe = _walk(folder)
    if has_exe:
        return None
    entry = pick_entry(folder, candidates)
    if entry is None:
        return None
    hint = version_hint(folder.name)
    if hint is None:
        # The version is sometimes only in a subfolder: Lark/Lantern_v0.34/
        for part in reversed(entry.relative_to(folder).parts[:-1]):
            hint = version_hint(part)
            if hint:
                break
    return FoundGame(
        folder=folder.name,
        entry=entry.relative_to(folder).as_posix(),
        title=guess_title(folder, entry),
        version_hint=hint,
        html_files=tuple(_relative(folder, candidates)),
        size=folder_size(folder),
        image=pick_image(folder, images),
        images=tuple(_relative(folder, images)),
    )


def scan(games_dir: Path) -> list[FoundGame]:
    """All HTML games directly below games_dir, sorted by folder name."""
    if not games_dir.is_dir():
        return []
    games = []
    for folder in sorted(games_dir.iterdir(), key=lambda p: p.name.lower()):
        name = folder.name.lower()
        if not folder.is_dir() or name.startswith(".") or name in IGNORED_DIRS:
            continue
        game = scan_folder(folder)
        if game is not None:
            games.append(game)
    return games
