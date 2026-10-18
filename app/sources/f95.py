"""F95Zone: version from the thread title.

Thread pages can be read without logging in. The title has the form
`Game name [v0.8.2] [Developer]`, preceded by prefixes such as "HTML" or
"Abandoned" as separate elements. The "Thread Updated" date is in the first post.
No login is needed, so no credentials are used.

Adult game site: only checked with ADULT_SOURCES=true.
"""

import re

from app import updates
from app.sources.common import text_of

_THREAD = re.compile(r"/threads/(?:[^/]*\.)?(\d+)(?:/|$)")
_H1 = re.compile(r'<h1[^>]*class="p-title-value"[^>]*>(.*?)</h1>', re.DOTALL)
_LABELS = re.compile(r"<a[^>]*labelLink.*?</a>|<span[^>]*label-append.*?</span>", re.DOTALL)
_BRACKETS = re.compile(r"\[([^\]]+)\]")
_UPDATED = re.compile(r"Thread Updated\s*</b>\s*:?\s*(\d{4}-\d{2}-\d{2})")


class F95Zone:
    name = "F95Zone"
    domains = ("f95zone.to", "f95zone.com")
    adult = True

    def latest(self, url: str, fetch: updates.Fetch) -> updates.Latest:
        match = _THREAD.search(url)
        if not match:
            raise updates.CheckError(
                "The link does not point to an F95Zone thread (expected …/threads/….<id>/)."
            )
        page = fetch(f"https://f95zone.to/threads/{match.group(1)}/").text
        return parse_thread(page)


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
