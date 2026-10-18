"""Patreon: version from the titles of the newest posts.

Feasibility (checked 2026-10-17): the creator page can be read without login and
contains the campaign ID. The API `/api/posts?filter[campaign_id]=…` returns the
title and date of the newest posts without login. Content and downloads stay
reserved for supporters.

A version is only there if the developer writes it into the post title (for
example "v0.6 Public Release"). The first title with a version among the newest
posts is used. Without one, the check stays manual, because a new post alone
does not mean there is an update.
"""

import json
import re
from urllib.parse import unquote, urlencode, urlsplit

from app import updates

POSTS = 10  # number of newest posts searched

_CAMPAIGN = re.compile(r'"campaign":\{"data":\{"id":"(\d+)","type":"campaign"')
_CAMPAIGN_LINK = re.compile(r"/api/campaigns/(\d+)")
# With a leading "v" (v0.6, Version 2) or with a dot (0.6.1); plain numbers like 2026 do not count
_TITLE_VERSION = re.compile(
    r"\bv(?:er(?:sion)?)?\.?\s*\d+(?:[._]\d+)*[a-z]?\b|\b\d+(?:\.\d+)+[a-z]?\b", re.IGNORECASE
)
_RESERVED = {"posts", "about", "membership", "collections", "shop", "home", "join"}


class Patreon:
    name = "Patreon"
    domains = ("patreon.com",)
    adult = False

    def latest(self, url: str, fetch: updates.Fetch, title: str | None = None) -> updates.Latest:
        campaign = self.campaign_id(url, fetch)
        query = urlencode(
            {
                "filter[campaign_id]": campaign,
                "sort": "-published_at",
                "fields[post]": "title,published_at,url",
                "page[count]": POSTS,
            }
        )
        answer = fetch(f"https://www.patreon.com/api/posts?{query}").text
        return parse_posts(answer)

    def campaign_id(self, url: str, fetch: updates.Fetch) -> str:
        parts = urlsplit(url)
        given = re.search(r"campaign_id\]?=(\d+)", unquote(parts.query))
        if given:
            return given.group(1)
        path = [p for p in parts.path.split("/") if p]
        if path[:1] == ["c"]:
            path = path[1:]
        if not path or path[0].lower() in _RESERVED:
            raise updates.CheckError(
                "The link does not point to a Patreon creator page (expected patreon.com/<name>)."
            )
        page = fetch(f"https://www.patreon.com/{path[0]}").text
        match = _CAMPAIGN.search(page) or _CAMPAIGN_LINK.search(page)
        if not match:
            raise updates.CheckError("No campaign was found on the Patreon page.")
        return match.group(1)


def parse_posts(answer: str) -> updates.Latest:
    try:
        posts = json.loads(answer).get("data") or []
    except (ValueError, AttributeError) as exc:
        raise updates.CheckError("Patreon did not return a readable answer.") from exc
    for post in posts:
        attributes = post.get("attributes") or {}
        match = _TITLE_VERSION.search(attributes.get("title") or "")
        if match:
            return updates.Latest(
                version=match.group().strip(),
                date=(attributes.get("published_at") or "")[:10] or None,
            )
    raise updates.ManualCheck(
        "None of the newest Patreon posts has a version in its title. Please check by hand."
    )


updates.register(Patreon())
