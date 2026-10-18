"""Safe ZIP import: check the archive, extract it into a staging folder, detect the game.

Every archive entry is checked before anything is written: no absolute paths, no
`..`, no symbolic links, no encrypted files, no `.exe`. The size is limited twice:
by the sizes declared in the archive and, while extracting, by the bytes actually
written, so that forged sizes (zip bombs) do not slip through.
"""

import re
import shutil
import stat
import zipfile
import zlib
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

from app import scanner

# Entries that never belong to the game and are skipped silently
JUNK_DIRS = {"__macosx"}
JUNK_FILES = {".ds_store", "thumbs.db", "desktop.ini"}
# No sensible HTML game has more files; protects against archives with millions of entries
MAX_FILES = 50_000
CHUNK_SIZE = 1024 * 1024
MAX_FOLDER_NAME = 120
_WINDOWS_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)$", re.IGNORECASE)


class ImportRejected(ValueError):
    """The archive is not imported; the message gives the reason to the user."""


@dataclass(frozen=True)
class ExtractedGame:
    path: Path  # game folder in the staging folder; its name is the cleaned folder name
    game: scanner.FoundGame


def format_size(size: int) -> str:
    if size >= 1024**3:
        return f"{size / 1024**3:.1f} GB"
    return f"{max(round(size / 1024**2), 1)} MB"


def member_path(name: str) -> PurePosixPath | None:
    """Safe relative path of an archive entry; None for entries that are skipped."""
    name = name.replace("\\", "/")
    if name.startswith("/") or re.match(r"^[a-zA-Z]:", name):
        raise ImportRejected(f"The archive contains an absolute path ({name}).")
    if "\x00" in name:
        raise ImportRejected("The archive contains an invalid file name.")
    parts = [part for part in name.split("/") if part not in ("", ".")]
    if ".." in parts:
        raise ImportRejected(f"The archive contains a path outside the game folder ({name}).")
    if not parts or any(part.lower() in JUNK_DIRS for part in parts):
        return None
    if parts[-1].startswith("._") or parts[-1].lower() in JUNK_FILES:
        return None
    return PurePosixPath(*parts)


def clean_folder_name(name: str) -> str:
    """Folder name without characters that cause trouble on Windows or Linux."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    name = name[:MAX_FOLDER_NAME].strip(" .")
    if _WINDOWS_RESERVED.match(name):
        name += "_"
    return name or "Game"


def archive_stem(filename: str) -> str:
    """File name without path and without .zip."""
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"\.zip$", "", name, flags=re.IGNORECASE)


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def _check_members(
    archive: zipfile.ZipFile, max_bytes: int
) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    members = []
    total = 0
    for info in archive.infolist():
        path = member_path(info.filename)
        if path is None or info.is_dir():
            continue
        if _is_symlink(info):
            raise ImportRejected(f"The archive contains a symbolic link ({info.filename}).")
        if info.flag_bits & 0x1:
            raise ImportRejected("The archive is password protected.")
        if path.suffix.lower() == ".exe":
            raise ImportRejected(
                f"The archive contains a Windows program ({path.name}). "
                "Only HTML games are imported."
            )
        members.append((info, path))
        total += info.file_size
    if not members:
        raise ImportRejected("The archive is empty.")
    if len(members) > MAX_FILES:
        raise ImportRejected(
            f"The archive contains {len(members)} files; the maximum is {MAX_FILES}."
        )
    if total > max_bytes:
        raise ImportRejected(_too_big(total, max_bytes))
    return members


def _too_big(size: int, max_bytes: int) -> str:
    return (
        f"The extracted content would be {format_size(size)}; "
        f"the maximum is {format_size(max_bytes)} (MAX_EXTRACTED_MB)."
    )


def _extract(archive: zipfile.ZipFile, target: Path, max_bytes: int) -> None:
    written = 0
    for info, path in _check_members(archive, max_bytes):
        destination = target.joinpath(*path.parts)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, destination.open("wb") as output:
                while chunk := source.read(CHUNK_SIZE):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ImportRejected(_too_big(written, max_bytes))
                    output.write(chunk)
        except (FileExistsError, NotADirectoryError, IsADirectoryError) as exc:
            raise ImportRejected(
                f"The archive contains conflicting paths ({info.filename})."
            ) from exc


def _unwrap(root: Path) -> tuple[Path, list[str]]:
    """Descends into wrapper folders as long as a folder contains exactly one subfolder."""
    wrappers = []
    while True:
        children = list(root.iterdir())
        if len(children) != 1 or not children[0].is_dir():
            return root, wrappers
        root = children[0]
        wrappers.append(root.name)


def extract_game(archive_path: Path, staging: Path, filename: str, max_bytes: int) -> ExtractedGame:
    """Extracts a ZIP into `staging` (which must not exist yet) and detects the game.

    On any error `staging` is removed again. Rejected archives raise
    `ImportRejected` with an understandable reason.
    """
    # With a dot, so that no cleaned folder name can collide with it
    raw = staging / ".raw"
    staging.mkdir(parents=True)
    raw.mkdir()
    try:
        try:
            with zipfile.ZipFile(archive_path) as archive:
                _extract(archive, raw, max_bytes)
        except zipfile.BadZipFile as exc:
            raise ImportRejected("The file is not a valid ZIP archive or is damaged.") from exc
        except (zlib.error, EOFError) as exc:
            raise ImportRejected("The ZIP archive is damaged.") from exc
        except NotImplementedError as exc:
            raise ImportRejected(
                "The ZIP archive uses an unsupported compression method. "
                'Please repack it with "Deflate".'
            ) from exc

        root, wrappers = _unwrap(raw)
        names = [*wrappers, archive_stem(filename)]
        # Prefer a folder name with a version, otherwise the outermost wrapper or the archive
        name = next((n for n in names if scanner.version_hint(n)), names[0])
        hint = next((h for n in names if (h := scanner.version_hint(n))), None)

        game_dir = staging / clean_folder_name(name)
        root.rename(game_dir)
        shutil.rmtree(raw, ignore_errors=True)

        game = scanner.scan_folder(game_dir)
        if game is None:
            raise ImportRejected("The archive contains no HTML file that could start a game.")
        if game.version_hint is None and hint:
            game = replace(game, version_hint=hint)
        return ExtractedGame(game_dir, game)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
