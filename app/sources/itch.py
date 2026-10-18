"""itch.io: version from the public Wharf API.

The game pages themselves sit behind bot protection (Cloudflare) and cannot be
read with a simple request. Reachable without login is only
`https://itch.io/api/1/x/wharf/latest?target=<user>/<game>&channel_name=<channel>`.
It returns the version the developer gave when uploading with the "butler" tool.
Developers choose the channel name freely; the common names are tried. If the
developer uploads through the website there is no channel, and only a manual
check is left.

Feasibility (checked 2026-10-17): the game page, `data.json` and the devlog RSS
answer 403 "Just a moment…"; the Wharf API answers with JSON.
"""

import json
import re
from urllib.parse import urlencode

from app import updates

CHANNELS = ("html5", "web", "html", "browser", "webgl")

_GAME_URL = re.compile(r"^https?://([a-z0-9_-]+)\.itch\.io/([a-z0-9_-]+)", re.IGNORECASE)


class ItchIo:
    name = "itch.io"
    domains = ("itch.io",)
    adult = False

    def latest(self, url: str, fetch: updates.Fetch, title: str | None = None) -> updates.Latest:
        match = _GAME_URL.match(url)
        if not match:
            raise updates.CheckError(
                "The link does not point to an itch.io game page"
                " (expected https://<name>.itch.io/<game>)."
            )
        target = f"{match.group(1).lower()}/{match.group(2).lower()}"
        for channel in CHANNELS:
            query = urlencode({"target": target, "channel_name": channel})
            answer = _json(fetch(f"https://itch.io/api/1/x/wharf/latest?{query}").text)
            if answer.get("latest"):
                return updates.Latest(version=str(answer["latest"]))
            errors = " ".join(answer.get("errors") or [])
            if "invalid target" in errors:
                raise updates.CheckError(f'itch.io does not know the game "{target}".')
            if "invalid channel" not in errors:
                raise updates.CheckError(f"Unexpected answer from itch.io: {errors or answer}")
        raise updates.ManualCheck(
            "The game is not published on itch.io with butler, so no version can be"
            " retrieved. Please check the page by hand."
        )


def _json(text: str) -> dict:
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise updates.CheckError("itch.io did not return a readable answer.") from exc
    return data if isinstance(data, dict) else {}


updates.register(ItchIo())
