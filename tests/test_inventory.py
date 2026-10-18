"""Regression test with a made-up library that mimics typical downloaded folders.

Only the HTML and .exe files are recreated. The folder layouts cover the cases the
scanner has to handle: version in the folder name in many spellings, start file in
a subfolder, `__MACOSX` leftovers, changelog/credits/head pages and .exe games.
"""

from pathlib import Path

from app.scanner import scan

FILES = [
    "Courage/The Book of Courage.html",
    "PB/changelog.html",
    "PB/index.html",
    "beginning of xyz/index.html",
    "Blooming Meadow/Blooming Meadow.html",
    "Moonbeam/Moonbeam.html",
    "SH 0.2.1/Silver Harbor.html",
    "BlankPage/index.html",
    "Lumen_v1.3/Index.html",
    "StarHarbor0.6.1/Star Harbor.html",
    "Driftwood/Driftwood.html",
    "New Mornings/New Mornings.html",
    "Quiet Valley 0_11/Quiet Valley.html",
    "Paper Lanterns 1.4.4/Paper Lanterns.html",
    "voyage-v0.1.7-northpath-hotfix1/voyage.html",
    "Ridgetown/RIDGETOWN 11.1.exe",
    "WTRF/Water-for-the-Fields.html",
    "OAK v 0.191.0/index.html",
    "My Journey (Public)/My Journey.html",
    "Paths of Stone/Paths of Stone.html",
    "Lark/Lantern_v0.34/Lantern.html",
    "archive1115/archive1115/head.html",
    "archive1115/archive1115/archive1115.html",
    "Fractured-0.23.2-pc/lib/py3-windows-x86_64/python.exe",
    "Fractured-0.23.2-pc/Fractured.exe",
    "glassbridge/glass_bridge_release_3/glass_bridge.html",
    "Desk story v1.1/Desk story/credits.html",
    "Desk story v1.1/Desk story/Game.exe",
    "Desk story v1.1/Desk story/index.html",
    "moon_garden_0_4_7/index.html",
    "Harbor Town/Harbor Town USA/Harbor Town USA_ Hometown Stories.html",
    "RK-1.0.26-pc/lib/py3-windows-x86_64/python.exe",
    "RK-1.0.26-pc/RK.exe",
    "RK2-1.1.15-pc/RK2.exe",
    "ClockworkGarden_V263/Clockwork Garden.html",
    "The orchard/TheOrchard.html",
    "The orchard/__MACOSX/._TheOrchard.html",
    "The Keeper's Lantern v19/v19/The Keeper's Lantern v19/index.html",
    "The Keeper's Lantern v19/__MACOSX/v19/The Keeper's Lantern v19/._index.html",
    "QXKV0143/QXKV0143.html",
    "QWERT 1.9.1/QWERT.html",
    "QWERT 1.9.1/__MACOSX/._QWERT.html",
]

# Folder -> (start file, version hint)
EXPECTED = {
    "Courage": ("The Book of Courage.html", None),
    "PB": ("index.html", None),
    "beginning of xyz": ("index.html", None),
    "Blooming Meadow": ("Blooming Meadow.html", None),
    "Moonbeam": ("Moonbeam.html", None),
    "SH 0.2.1": ("Silver Harbor.html", "0.2.1"),
    "BlankPage": ("index.html", None),
    "Lumen_v1.3": ("Index.html", "1.3"),
    "StarHarbor0.6.1": ("Star Harbor.html", "0.6.1"),
    "Driftwood": ("Driftwood.html", None),
    "New Mornings": ("New Mornings.html", None),
    "Quiet Valley 0_11": ("Quiet Valley.html", "0.11"),
    "Paper Lanterns 1.4.4": ("Paper Lanterns.html", "1.4.4"),
    "voyage-v0.1.7-northpath-hotfix1": ("voyage.html", "0.1.7"),
    "WTRF": ("Water-for-the-Fields.html", None),
    "OAK v 0.191.0": ("index.html", "0.191.0"),
    "My Journey (Public)": ("My Journey.html", None),
    "Paths of Stone": ("Paths of Stone.html", None),
    "Lark": ("Lantern_v0.34/Lantern.html", "0.34"),
    "archive1115": ("archive1115/archive1115.html", None),
    "glassbridge": ("glass_bridge_release_3/glass_bridge.html", "3"),
    "moon_garden_0_4_7": ("index.html", "0.4.7"),
    "Harbor Town": ("Harbor Town USA/Harbor Town USA_ Hometown Stories.html", None),
    "ClockworkGarden_V263": ("Clockwork Garden.html", "263"),
    "The orchard": ("TheOrchard.html", None),
    "The Keeper's Lantern v19": ("v19/The Keeper's Lantern v19/index.html", "19"),
    "QXKV0143": ("QXKV0143.html", None),
    "QWERT 1.9.1": ("QWERT.html", "1.9.1"),
}


def test_sample_library(tmp_path: Path) -> None:
    for rel in FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    found = {g.folder: (g.entry, g.version_hint) for g in scan(tmp_path)}
    assert found == EXPECTED
    # Ren'Py and other .exe games do not show up
    for skipped in ("Ridgetown", "Fractured-0.23.2-pc", "RK-1.0.26-pc", "RK2-1.1.15-pc"):
        assert skipped not in found
    assert "Desk story v1.1" not in found
