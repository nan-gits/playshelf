"""Helpers for the adapters."""

import html
import re


def text_of(fragment: str) -> str:
    """Plain text of an HTML fragment: tags removed, entities decoded, whitespace collapsed."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()
