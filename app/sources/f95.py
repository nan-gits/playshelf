"""F95Zone: version from F95Zone's "latest updates" data, thread page as fallback.

Since October 2026 every thread page asks for a browser check (Cloudflare
challenge), which a request without a real browser cannot pass, and getting
around the bot protection is out of the question. The JSON behind the site's
"Latest Updates" page (`/sam/latest_alpha/latest_data.php`) still answers
without it. It can be searched by title and gives per thread its id, the title,
the version (free text such as "v0.79 Public") and the time of the last update
(`ts`, Unix time). The thread id from the update link picks the right entry,
so a search that finds other games as well does no harm.

The search is picky: some special characters (`!`, `~`, `%`) or a long title
make it find nothing or ignore the search, so several search terms are tried,
from the exact title to its first words. If none finds the thread, the thread
page is read as before; behind the browser check that ends in "check by hand".
No login is needed, so no credentials are used.

Adult game site: only checked with ADULT_SOURCES=true.
"""

import json
import logging
import re
from datetime import UTC, datetime
from urllib.parse import urlencode

from app import updates
from app.sources.common import text_of

log = logging.getLogger(__name__)

_THREAD = re.compile(r"/threads/(?:([^/]*)\.)?(\d+)(?:/|$)")
_H1 = re.compile(r'<h1[^>]*class="p-title-value"[^>]*>(.*?)</h1>', re.DOTALL)
_LABELS = re.compile(r"<a[^>]*labelLink.*?</a>|<span[^>]*label-append.*?</span>", re.DOTALL)
_BRACKETS = re.compile(r"\[([^\]]+)\]")
_UPDATED = re.compile(r"Thread Updated\s*</b>\s*:?\s*(\d{4}-\d{2}-\d{2})")

LATEST_URL = "https://f95zone.to/sam/latest_alpha/latest_data.php"
MAX_SEARCHES = 4  # requests to the search per check, a few seconds apart
# A version in a title or link name: "v0.78", "v.0.78", "0.11"
_VERSION_WORD = re.compile(r"\b(?:v\.?\s*\d|\d+(?:\.\d+)+)", re.I)


class F95Zone:
    name = "F95Zone"
    domains = ("f95zone.to", "f95zone.com")
    adult = True

    def latest(self, url: str, fetch: updates.Fetch, title: str | None = None) -> updates.Latest:
        match = _THREAD.search(url)
        if not match:
            raise updates.CheckError(
                "The link does not point to an F95Zone thread (expected …/threads/….<id>/)."
            )
        slug, thread_id = match.group(1), int(match.group(2))
        try:
            for term in search_terms(title, slug):
                query = urlencode({"cmd": "list", "cat": "games", "page": 1, "search": term})
                entry = find_thread(fetch(f"{LATEST_URL}?{query}").text, thread_id)
                if entry is not None:
                    return from_entry(entry)
        except updates.CheckError as exc:
            # The search is unofficial and may change; the thread page may still work
            log.warning("F95Zone search for thread %s failed: %s", thread_id, exc)
        try:
            page = fetch(f"https://f95zone.to/threads/{thread_id}/").text
        except updates.BotProtection as exc:
            raise updates.BotProtection(
                "F95Zone's search did not find this thread, and the thread page asks for a"
                " browser check (bot protection). Please check the page by hand."
            ) from exc
        return parse_thread(page)


def search_terms(title: str | None, slug: str | None) -> list[str]:
    """Search terms to try, best first: the title as it is, without special
    characters, the name from the link, and the first words."""
    names = []
    if title:
        names.append(_without_version(title))
    if slug:
        names.append(_without_version(slug.replace("-", " ")))
    terms: list[str] = []
    for name in names:
        plain = " ".join(re.sub(r"[^\w']+", " ", name).split())
        words = plain.split()
        terms += [name, plain, " ".join(words[:2]) if len(words) > 2 else ""]
    unique: list[str] = []
    for term in terms:
        if len(term) >= 3 and term.lower() not in (t.lower() for t in unique):
            unique.append(term)
    return unique[:MAX_SEARCHES]


def _without_version(name: str) -> str:
    """The name without bracketed parts and without a version at the end."""
    name = _BRACKETS.sub(" ", name)
    version = _VERSION_WORD.search(name)
    if version and version.start() > 0:
        name = name[: version.start()]
    return " ".join(name.split()).strip(" -–:")


def find_thread(text: str, thread_id: int) -> dict | None:
    """The entry for `thread_id` in an answer of the search, None if it is not there."""
    try:
        data = json.loads(text)
        entries = data["msg"]["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise updates.CheckError(
            "F95Zone's latest updates could not be read, possibly a changed format."
        ) from exc
    if data.get("status") != "ok" or not isinstance(entries, list):
        raise updates.CheckError("F95Zone's latest updates answered with an error.")
    for entry in entries:
        if isinstance(entry, dict) and str(entry.get("thread_id")) == str(thread_id):
            return entry
    return None


def from_entry(entry: dict) -> updates.Latest:
    version = str(entry.get("version") or "").strip() or None
    date = None
    if isinstance(entry.get("ts"), int | float) and entry["ts"] > 0:
        date = datetime.fromtimestamp(entry["ts"], UTC).isoformat(timespec="seconds")
    return updates.Latest(version=version, date=date)


def parse_thread(page: str) -> updates.Latest:
    h1 = _H1.search(page)
    if not h1:
        if "/login/" in page and "p-title-value" not in page:
            raise updates.CheckError("F95Zone requires a login for this thread.")
        raise updates.CheckError("Thread title not found, possibly a new page layout.")
    title = text_of(_LABELS.sub(" ", h1.group(1)))
    brackets = _BRACKETS.findall(title)
    version = None
    if brackets and (len(brackets) >= 2 or updates.version_key(brackets[0])):
        version = brackets[0].strip()
    # Only search the first post: later posts often contain older information
    posts = page.split('class="bbWrapper"')
    updated = _UPDATED.search(posts[1]) if len(posts) > 1 else None
    if not version and not updated:
        raise updates.CheckError(f'No version found in the thread title: "{title}".')
    return updates.Latest(version=version, date=updated.group(1) if updated else None)


updates.register(F95Zone())
