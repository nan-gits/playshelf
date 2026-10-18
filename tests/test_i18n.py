import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import i18n
from app.config import Settings
from app.main import TEMPLATE, create_app

APP_JS = Path(i18n.__file__).parent / "static" / "app.js"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def test_tables_have_the_same_keys() -> None:
    english, german = i18n.messages("en"), i18n.messages("de")
    assert sorted(english) == sorted(german)


@pytest.mark.parametrize("language", i18n.LANGUAGES)
def test_texts_are_not_empty(language: str) -> None:
    for key, text in i18n.messages(language).items():
        assert isinstance(text, str) and text.strip(), key


def test_tables_use_the_same_placeholders() -> None:
    english, german = i18n.messages("en"), i18n.messages("de")
    for key, text in english.items():
        if key.endswith(".one"):
            # "started once" needs no number; the other form must have it
            assert placeholders(text) <= placeholders(english[key[:-4] + ".other"]), key
            continue
        assert placeholders(text) == placeholders(german[key]), key


def test_plural_keys_come_in_pairs() -> None:
    keys = set(i18n.messages("en"))
    for key in keys:
        if key.endswith(".one"):
            assert key[:-4] + ".other" in keys, key


def test_english_table_has_no_german() -> None:
    for key, text in i18n.messages("en").items():
        assert not re.search("[äöüÄÖÜß„]", text), key


def test_template_keys_exist() -> None:
    keys = set(re.findall(r"\{\{\s*([\w.]+)\s*\}\}", TEMPLATE.read_text(encoding="utf-8")))
    assert keys - {"lang", "messages", "version", "project_url"} <= set(i18n.messages("en"))


def test_keys_used_in_app_js_exist() -> None:
    table = i18n.messages("en")
    used = set(re.findall(r"""\bt\(\s*["']([\w.]+)["']""", APP_JS.read_text(encoding="utf-8")))
    assert used
    missing = [key for key in used if key not in table and f"{key}.other" not in table]
    assert not missing
    # Keys built from stored codes
    for code in ("playing", "completed", "dropped", "waiting"):
        assert f"status.{code}" in table
    for action in ("new", "update", "restored", "edited"):
        assert f"history.action.{action}" in table
    for match in ("folder", "title", "entry"):
        assert f"import.match.{match}" in table
    for theme in ("auto", "light", "dark"):
        assert f"theme.{theme}" in table


def test_status_codes_have_labels() -> None:
    from app.db import STATUSES

    for code in STATUSES:
        assert f"status.{code}" in i18n.messages("en")


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("", None),
        ("de", "de"),
        ("de-AT,de;q=0.9,en;q=0.8", "de"),
        ("en-US,en;q=0.9,de;q=0.8", "en"),
        ("fr-FR,fr;q=0.9,de;q=0.5", "de"),
        ("fr,en;q=0.3,de;q=0.7", "de"),
        ("de;q=0,en", "en"),
        ("fr", None),
        ("*", None),
        ("de;q=abc", None),
    ],
)
def test_from_accept_language(header: str | None, expected: str | None) -> None:
    assert i18n.from_accept_language(header) == expected


def test_choose_language() -> None:
    assert i18n.choose_language("de", "en-US") == "de"
    assert i18n.choose_language("en", "de-DE") == "en"
    assert i18n.choose_language("auto", "de-DE") == "de"
    assert i18n.choose_language("auto", "fr") == "en"
    assert i18n.choose_language("auto", None) == "en"


@pytest.mark.parametrize(("value", "expected"), [("", "auto"), ("DE", "de"), (" en ", "en")])
def test_language_setting(value: str, expected: str) -> None:
    assert Settings(_env_file=None, ui_language=value).ui_language == expected


def test_language_setting_ignores_gnu_language(monkeypatch: pytest.MonkeyPatch) -> None:
    # Debian and Ubuntu set LANGUAGE=en_US:en; it must not break the start.
    monkeypatch.setenv("LANGUAGE", "en_US:en")
    monkeypatch.setenv("UI_LANGUAGE", "de")
    assert Settings(_env_file=None).ui_language == "de"


def test_language_setting_rejects_unknown() -> None:
    with pytest.raises(ValueError):
        Settings(_env_file=None, ui_language="fr")


def embedded(html: str) -> dict:
    match = re.search(r'<script id="i18n" type="application/json">(.*?)</script>', html, re.S)
    assert match
    return json.loads(match.group(1))


def test_dashboard_follows_the_browser(client: TestClient) -> None:
    english = client.get("/").text
    assert '<html lang="en">' in english
    assert "Rescan folder" in english
    assert embedded(english)["language"] == "en"

    response = client.get("/", headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
    assert response.headers["vary"] == "Accept-Language"
    german = response.text
    assert '<html lang="de">' in german
    assert "Ordner neu einlesen" in german
    data = embedded(german)
    assert data["language"] == "de"
    assert data["messages"] == i18n.messages("de")


def test_language_setting_wins_over_browser(settings: Settings) -> None:
    settings.ui_language = "de"
    with TestClient(create_app(settings)) as client:
        html = client.get("/", headers={"Accept-Language": "en-US"}).text
    assert '<html lang="de">' in html
    assert "Spiel bearbeiten" in html
