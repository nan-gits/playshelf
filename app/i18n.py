"""Interface languages.

All texts of the interface live in `app/locales/<language>.json`, one flat table
per language with the same keys. Placeholders are written `{name}`. Keys ending
in `.one` / `.other` are plural forms; the browser picks one with
`Intl.PluralRules`.

The language comes from the signed-in account if it chose one, otherwise from the
`UI_LANGUAGE` setting. With `UI_LANGUAGE=auto` (the default) the browser's
`Accept-Language` header decides, falling back to English.
"""

import json
from functools import cache
from pathlib import Path
from typing import Any

from fastapi import Request

LANGUAGES = ("en", "de")
DEFAULT_LANGUAGE = "en"
AUTO = "auto"
LOCALES_DIR = Path(__file__).parent / "locales"


@cache
def messages(language: str) -> dict[str, str]:
    """The table of one language."""
    if language not in LANGUAGES:
        raise ValueError(f"Unknown language: {language}")
    return json.loads((LOCALES_DIR / f"{language}.json").read_text(encoding="utf-8"))


def translate(language: str, key: str, **params: Any) -> str:
    """Text for `key`; a missing key is an error, so tests find it."""
    return messages(language)[key].format(**params)


def from_accept_language(header: str | None) -> str | None:
    """The supported language the browser prefers most, or None.

    `de-AT,de;q=0.9,en;q=0.8` gives `de`; entries with `q=0` are refused ones.
    """
    choices: list[tuple[float, int, str]] = []
    for index, part in enumerate((header or "").split(",")):
        tag, _, params = part.strip().partition(";")
        language = tag.strip().split("-")[0].lower()
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        if language in LANGUAGES and quality > 0:
            choices.append((-quality, index, language))
    return min(choices)[2] if choices else None


def choose_language(setting: str, accept_language: str | None) -> str:
    if setting in LANGUAGES:
        return setting
    return from_accept_language(accept_language) or DEFAULT_LANGUAGE


def request_language(request: Request) -> str:
    """Language for this request, from the account, the setting or the browser."""
    user = getattr(request.state, "user", None)
    if user and user.get("language") in LANGUAGES:
        return user["language"]
    setting = request.app.state.settings.ui_language
    return choose_language(setting, request.headers.get("accept-language"))
