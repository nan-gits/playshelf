import stat
import zipfile
from pathlib import Path

import pytest

from app.importer import (
    ImportRejected,
    archive_stem,
    clean_folder_name,
    extract_game,
    format_size,
    member_path,
)

MB = 1024 * 1024


def make_zip(path: Path, files: dict[str, str | bytes], **kwargs) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, **kwargs) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def extract(tmp_path: Path, files: dict[str, str | bytes], filename: str = "Game.zip", **kw):
    archive = make_zip(tmp_path / "upload.zip", files)
    return extract_game(archive, tmp_path / "staging", filename, kw.get("max_bytes", 10 * MB))


def rejected(tmp_path: Path, files: dict[str, str | bytes], **kw) -> str:
    with pytest.raises(ImportRejected) as info:
        extract(tmp_path, files, **kw)
    assert not (tmp_path / "staging").exists(), "staging folder must be cleaned up"
    return str(info.value)


def test_flat_archive_uses_archive_name(tmp_path: Path) -> None:
    result = extract(
        tmp_path,
        {"index.html": "<title>My Game</title>", "img/a.png": b"\x89PNG"},
        filename="My Game v0.5.zip",
    )
    assert result.path == tmp_path / "staging" / "My Game v0.5"
    assert (result.path / "img" / "a.png").read_bytes() == b"\x89PNG"
    assert result.game.entry == "index.html"
    assert result.game.title == "My Game"
    assert result.game.version_hint == "0.5"


def test_wrapper_folders_and_macosx_are_removed(tmp_path: Path) -> None:
    result = extract(
        tmp_path,
        {
            "Outer/Paper Lanterns 1.5.0/Paper Lanterns.html": "<title>PL</title>",
            "Outer/Paper Lanterns 1.5.0/media/x.mp3": b"ID3",
            "__MACOSX/Outer/._Paper Lanterns.html": b"junk",
            "Outer/.DS_Store": b"junk",
            "Outer/Paper Lanterns 1.5.0/._x": b"junk",
        },
        filename="download.zip",
    )
    assert result.path.name == "Paper Lanterns 1.5.0"
    assert result.game.entry == "Paper Lanterns.html"
    assert result.game.version_hint == "1.5.0"
    assert sorted(p.name for p in (tmp_path / "staging").iterdir()) == ["Paper Lanterns 1.5.0"]
    assert not list(result.path.rglob("._*"))


def test_version_hint_from_archive_name_when_folder_has_none(tmp_path: Path) -> None:
    result = extract(tmp_path, {"Game/index.html": "x"}, filename="Game-v12.zip")
    assert result.path.name == "Game-v12"
    assert result.game.version_hint == "12"


def test_backslash_paths_from_windows_tools(tmp_path: Path) -> None:
    result = extract(tmp_path, {"Game\\index.html": "x", "Game\\js\\a.js": "y"})
    assert result.path.name == "Game"
    assert (result.path / "js" / "a.js").is_file()


@pytest.mark.parametrize(
    "name",
    ["../evil.html", "Game/../../evil.html", "/etc/evil.html", "C:/evil.html", "..\\evil.html"],
)
def test_zip_slip_is_rejected(tmp_path: Path, name: str) -> None:
    message = rejected(tmp_path, {"index.html": "x", name: "x"})
    assert "path" in message
    assert not (tmp_path / "evil.html").exists()


def test_symlink_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.html", "x")
        link = zipfile.ZipInfo("link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
    with pytest.raises(ImportRejected, match="symbolic link"):
        extract_game(archive, tmp_path / "staging", "x.zip", MB)
    assert not (tmp_path / "staging").exists()


def test_exe_is_rejected(tmp_path: Path) -> None:
    message = rejected(tmp_path, {"Game/Game.exe": b"MZ", "Game/index.html": "x"})
    assert "Game.exe" in message
    assert "Only HTML games" in message


def test_archive_without_html_is_rejected(tmp_path: Path) -> None:
    message = rejected(tmp_path, {"Game/readme.txt": "x", "Game/changelog.html": "x"})
    assert "no HTML file" in message


def test_empty_archive_is_rejected(tmp_path: Path) -> None:
    assert "empty" in rejected(tmp_path, {"__MACOSX/._x": "x"})


def test_broken_file_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "upload.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(ImportRejected, match="not a valid ZIP"):
        extract_game(archive, tmp_path / "staging", "x.zip", MB)
    assert not (tmp_path / "staging").exists()


def test_corrupted_content_is_rejected(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "upload.zip", {"index.html": "x" * 10_000})
    data = bytearray(archive.read_bytes())
    start = data.index(b"index.html") + len("index.html")
    data[start + 10 : start + 30] = b"\x00" * 20  # destroy compressed data
    archive.write_bytes(bytes(data))
    with pytest.raises(ImportRejected, match="damaged"):
        extract_game(archive, tmp_path / "staging", "x.zip", MB)


def test_encrypted_member_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "upload.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("index.html")
        zf.writestr(info, "x")
    data = bytearray(archive.read_bytes())
    # Set the encryption bit in the local and the central header
    data[6] |= 1
    central = data.index(b"PK\x01\x02")
    data[central + 8] |= 1
    archive.write_bytes(bytes(data))
    with pytest.raises(ImportRejected, match="password protected"):
        extract_game(archive, tmp_path / "staging", "x.zip", MB)


def test_declared_size_over_limit_is_rejected(tmp_path: Path) -> None:
    message = rejected(tmp_path, {"index.html": "x", "big.bin": b"\0" * (2 * MB)}, max_bytes=MB)
    assert "the maximum is 1 MB" in message


def test_zip_bomb_with_forged_size_is_stopped(tmp_path: Path) -> None:
    """If the archive declares too small a size, counting while extracting catches it."""
    archive = make_zip(tmp_path / "upload.zip", {"index.html": "x", "bomb.bin": b"\0" * (3 * MB)})
    data = bytearray(archive.read_bytes())
    central = data.index(b"PK\x01\x02", data.index(b"bomb.bin"))
    # Forge the uncompressed size in the central directory to 1 byte
    data[central + 24 : central + 28] = (1).to_bytes(4, "little")
    archive.write_bytes(bytes(data))
    with pytest.raises(ImportRejected):
        extract_game(archive, tmp_path / "staging", "x.zip", MB)
    assert not (tmp_path / "staging").exists()


def test_too_many_files_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.importer.MAX_FILES", 3)
    assert "maximum is 3" in rejected(tmp_path, {f"{i}.html": "x" for i in range(4)})


def test_conflicting_paths_are_rejected(tmp_path: Path) -> None:
    assert "conflicting" in rejected(tmp_path, {"a": "file", "a/index.html": "x"})


def test_staging_must_not_exist(tmp_path: Path) -> None:
    (tmp_path / "staging").mkdir()
    archive = make_zip(tmp_path / "upload.zip", {"index.html": "x"})
    with pytest.raises(FileExistsError):
        extract_game(archive, tmp_path / "staging", "x.zip", MB)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Paper Lanterns 1.5", "Paper Lanterns 1.5"),
        ('Bad: name? <x> | "y" *', "Bad name x y"),
        ("  .hidden. ", "hidden"),
        ("..", "Game"),
        ("", "Game"),
        ("con", "con_"),
        ("x" * 200, "x" * 120),
    ],
)
def test_clean_folder_name(name: str, expected: str) -> None:
    assert clean_folder_name(name) == expected


def test_member_path_skips_junk() -> None:
    assert member_path("__MACOSX/a/b") is None
    assert member_path("a/._b") is None
    assert member_path("a/Thumbs.db") is None
    assert str(member_path("./a//b")) == "a/b"


def test_archive_stem_and_size() -> None:
    assert archive_stem("C:\\Downloads\\Game 0.5.ZIP") == "Game 0.5"
    assert format_size(5 * MB) == "5 MB"
    assert format_size(3 * 1024 * MB // 2) == "1.5 GB"
