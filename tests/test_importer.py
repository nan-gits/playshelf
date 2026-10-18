import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from app import importer
from app.importer import (
    ImportRejected,
    archive_stem,
    archive_suffix,
    clean_folder_name,
    extract_game,
    format_size,
    member_path,
)

MB = 1024 * 1024
# RAR and 7z archives made with rar 7.00 and 7-Zip 23.01 (tests cannot create them)
ARCHIVES = Path(__file__).parent / "fixtures" / "archives"
needs_libarchive = pytest.mark.skipif(
    importer.libarchive is None, reason="libarchive is not installed"
)


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
    assert archive_stem("Game 0.5.tar.gz") == "Game 0.5"
    assert archive_stem("Game-0.5.7z") == "Game-0.5"
    assert archive_stem("Game 0.5.Rar") == "Game 0.5"
    assert archive_stem("notes.txt") == "notes.txt"
    assert format_size(5 * MB) == "5 MB"
    assert format_size(3 * 1024 * MB // 2) == "1.5 GB"


# RAR, 7z and tar through libarchive


def make_tar(path: Path, files: dict[str, str | bytes], mode: str = "w:gz") -> Path:
    with tarfile.open(path, mode) as archive:
        for name, content in files.items():
            data = content.encode() if isinstance(content, str) else content
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


def extract_tar(tmp_path: Path, files: dict[str, str | bytes], **kw):
    archive = make_tar(tmp_path / "upload.tar.gz", files)
    return extract_game(archive, tmp_path / "staging", "Game.tar.gz", kw.get("max_bytes", MB))


def tar_rejected(tmp_path: Path, files: dict[str, str | bytes], **kw) -> str:
    with pytest.raises(ImportRejected) as info:
        extract_tar(tmp_path, files, **kw)
    assert not (tmp_path / "staging").exists(), "staging folder must be cleaned up"
    return str(info.value)


def test_suffixes() -> None:
    assert archive_suffix("Game.ZIP") == ".zip"
    assert archive_suffix("notes.txt") is None
    if importer.libarchive is not None:
        assert archive_suffix("Game 1.0.tar.gz") == ".tar.gz"
        assert archive_suffix("Game.7z") == ".7z"


@needs_libarchive
@pytest.mark.parametrize("name", ["game.rar", "game.7z"])
def test_rar_and_7z(tmp_path: Path, name: str) -> None:
    result = extract_game(ARCHIVES / name, tmp_path / "staging", f"Quiet Valley.{name[5:]}", MB)
    assert result.path == tmp_path / "staging" / "Quiet Valley 0.3"
    assert (result.path / "js" / "game.js").read_text() == "let x = 1;\n"
    assert result.game.entry == "index.html"
    assert result.game.title == "Quiet Valley"
    assert result.game.version_hint == "0.3"


@needs_libarchive
@pytest.mark.parametrize("name", ["encrypted.rar", "encrypted.7z"])
def test_encrypted_rar_and_7z_are_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(ImportRejected, match="password protected"):
        extract_game(ARCHIVES / name, tmp_path / "staging", name, MB)
    assert not (tmp_path / "staging").exists()


@needs_libarchive
def test_rar_named_zip_is_rejected_as_zip(tmp_path: Path) -> None:
    with pytest.raises(ImportRejected, match="not a valid ZIP"):
        extract_game(ARCHIVES / "game.rar", tmp_path / "staging", "Game.zip", MB)


@needs_libarchive
def test_zip_named_rar_is_read_as_zip(tmp_path: Path) -> None:
    archive = make_zip(tmp_path / "upload", {"index.html": "<title>Z</title>"})
    assert extract_game(archive, tmp_path / "staging", "Z.rar", MB).game.title == "Z"


@needs_libarchive
@pytest.mark.parametrize("mode", ["w", "w:gz", "w:bz2", "w:xz"])
def test_tar(tmp_path: Path, mode: str) -> None:
    archive = make_tar(tmp_path / "upload", {"T 2.0/index.html": "<title>T</title>"}, mode)
    result = extract_game(archive, tmp_path / "staging", "T.tar", MB)
    assert (result.path.name, result.game.version_hint) == ("T 2.0", "2.0")


@needs_libarchive
def test_tar_wrapper_and_junk(tmp_path: Path) -> None:
    result = extract_tar(
        tmp_path,
        {"./Outer/G 1.0/g.html": "<title>G</title>", "__MACOSX/x": "junk", "Outer/._y": "junk"},
    )
    assert result.path.name == "G 1.0"
    assert result.game.entry == "g.html"


@needs_libarchive
@pytest.mark.parametrize(
    ("name", "message"),
    [("../evil.html", "outside the game folder"), ("/etc/evil.html", "absolute path")],
)
def test_tar_unsafe_paths_are_rejected(tmp_path: Path, name: str, message: str) -> None:
    assert message in tar_rejected(tmp_path, {"index.html": "x", name: "x"})
    assert not (tmp_path / "evil.html").exists()


@needs_libarchive
@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_tar_links_are_rejected(tmp_path: Path, kind: bytes) -> None:
    archive = tmp_path / "upload.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo("index.html")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
        link = tarfile.TarInfo("link")
        link.type = kind
        link.linkname = "/etc/passwd" if kind == tarfile.SYMTYPE else "index.html"
        tar.addfile(link)
    with pytest.raises(ImportRejected, match="link"):
        extract_game(archive, tmp_path / "staging", "x.tar", MB)
    assert not (tmp_path / "staging").exists()


@needs_libarchive
def test_tar_special_files_are_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "upload.tar"
    with tarfile.open(archive, "w") as tar:
        fifo = tarfile.TarInfo("pipe")
        fifo.type = tarfile.FIFOTYPE
        tar.addfile(fifo)
    with pytest.raises(ImportRejected, match="special file"):
        extract_game(archive, tmp_path / "staging", "x.tar", MB)


@needs_libarchive
def test_tar_exe_too_many_and_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "Windows program" in tar_rejected(tmp_path, {"G/index.html": "x", "G/G.exe": "MZ"})
    assert "empty" in tar_rejected(tmp_path, {"__MACOSX/x": "junk"})
    monkeypatch.setattr("app.importer.MAX_FILES", 3)
    assert "maximum is 3" in tar_rejected(tmp_path, {f"{i}.html": "x" for i in range(4)})


@needs_libarchive
def test_tar_size_is_limited(tmp_path: Path) -> None:
    message = tar_rejected(tmp_path, {"index.html": "x", "big.bin": b"\0" * (2 * MB)})
    assert "MAX_EXTRACTED_MB" in message


@needs_libarchive
def test_tar_size_is_counted_while_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if the declared sizes lie, the bytes written are counted."""
    real = importer.libarchive.file_reader

    class Lying:
        def __init__(self, entry):
            self._entry = entry

        def __getattr__(self, name):
            return 0 if name == "size" else getattr(self._entry, name)

    def lying_reader(*args, **kwargs):
        class Reader:
            def __enter__(self):
                self._cm = real(*args, **kwargs)
                return (Lying(entry) for entry in self._cm.__enter__())

            def __exit__(self, *exc):
                return self._cm.__exit__(*exc)

        return Reader()

    monkeypatch.setattr(importer.libarchive, "file_reader", lying_reader)
    message = tar_rejected(tmp_path, {"index.html": "x", "big.bin": b"\0" * (2 * MB)})
    assert "MAX_EXTRACTED_MB" in message


@needs_libarchive
def test_tar_conflicting_paths_are_rejected(tmp_path: Path) -> None:
    assert "conflicting" in tar_rejected(tmp_path, {"a": "file", "a/index.html": "x"})


@needs_libarchive
def test_damaged_archive_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "upload.7z"
    archive.write_bytes((ARCHIVES / "game.7z").read_bytes()[:100])
    with pytest.raises(ImportRejected, match="could not be read"):
        extract_game(archive, tmp_path / "staging", "x.7z", MB)
    assert not (tmp_path / "staging").exists()


def test_split_rar_is_rejected(tmp_path: Path) -> None:
    archive = tmp_path / "upload"
    archive.write_bytes(b"Rar!")
    with pytest.raises(ImportRejected, match="several parts"):
        extract_game(archive, tmp_path / "staging", "Game.part1.rar", MB)


def test_without_libarchive_only_zip_works(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.importer.libarchive", None)
    assert importer.supported_suffixes() == [".zip"]
    with pytest.raises(ImportRejected, match="libarchive"):
        extract_game(ARCHIVES / "game.rar", tmp_path / "staging", "Game.rar", MB)
    archive = make_zip(tmp_path / "upload.zip", {"index.html": "x"})
    assert extract_game(archive, tmp_path / "staging", "x.zip", MB).game.entry == "index.html"
