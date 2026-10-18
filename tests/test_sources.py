"""Adapters of the individual sources with synthetic responses."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app import updates
from app.sources import f95, patreon
from tests.test_updates import FakeFetch

FIXTURES = Path(__file__).parent / "fixtures" / "updates"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def latest(url: str, fetch: FakeFetch) -> updates.Latest:
    return updates.check_url(url, fetch, adult_sources=True)


# F95Zone


@pytest.mark.parametrize(
    "url",
    [
        "https://f95zone.to/threads/driftwood-v0-78-public-exampledev.123456/",
        "https://f95zone.to/threads/123456/",
        "https://f95zone.to/threads/driftwood-v0-78-public-exampledev.123456/page-12#post-1",
    ],
)
def test_f95_thread_title(url: str) -> None:
    fetch = FakeFetch({"https://f95zone.to/threads/123456/": fixture("f95_thread.html")})
    result = latest(url, fetch)
    assert result.version == "v0.78 Public"
    assert result.date == "2026-09-20"
    assert updates.compare_versions(result.version, "v.0.78") == 0
    assert fetch.urls == ["https://f95zone.to/threads/123456/"]


def test_f95_prefixes_and_first_post_only() -> None:
    result = f95.parse_thread(fixture("f95_abandoned.html"))
    assert result.version == "v0.15"
    assert result.date == "2022-06-24"


@pytest.mark.parametrize(
    ("title", "version"),
    [
        ("Game [v0.8.2] [Dev]", "v0.8.2"),
        ("Game [Ch. 3 v2] [Dev]", "Ch. 3 v2"),
        ("Game [Final] [Dev]", "Final"),
        ("Game [0.11]", "0.11"),
    ],
)
def test_f95_title_variants(title: str, version: str) -> None:
    page = f'<h1 class="p-title-value">{title}</h1>'
    assert f95.parse_thread(page).version == version


def test_f95_errors() -> None:
    with pytest.raises(updates.CheckError, match="thread"):
        latest("https://f95zone.to/forums/games.2/", FakeFetch())
    with pytest.raises(updates.CheckError, match="login"):
        f95.parse_thread('<a href="/login/">Log in</a>')
    with pytest.raises(updates.CheckError, match="No version"):
        f95.parse_thread('<h1 class="p-title-value">Just a title</h1>')


# itch.io


def wharf(answers: dict[str, str]) -> FakeFetch:
    """Wharf answers per channel; channels not listed are "invalid channel"."""

    class Wharf(FakeFetch):
        def __call__(self, url, headers=None):
            self.urls.append(url)
            query = parse_qs(urlsplit(url).query)
            assert query["target"] == ["dev/my-game"]
            text = answers.get(query["channel_name"][0], '{"errors":["invalid channel"]}')
            return updates.Response(url, 200, text)

    return Wharf()


def test_itch_version_from_butler_channel() -> None:
    fetch = wharf({"web": '{"latest":"1.0.26a"}'})
    result = latest("https://Dev.itch.io/my-game?ref=x", fetch)
    assert result.version == "1.0.26a"
    assert [parse_qs(urlsplit(u).query)["channel_name"][0] for u in fetch.urls] == [
        "html5",
        "web",
    ]


def test_itch_without_channel_needs_manual_check() -> None:
    with pytest.raises(updates.ManualCheck, match="butler"):
        latest("https://dev.itch.io/my-game", wharf({}))


def test_itch_errors() -> None:
    fetch = wharf({"html5": '{"errors":["invalid target (bad user)"]}'})
    with pytest.raises(updates.CheckError, match="does not know the game"):
        latest("https://dev.itch.io/my-game", fetch)
    with pytest.raises(updates.CheckError, match="itch.io game page"):
        latest("https://itch.io/games/html5", FakeFetch())
    with pytest.raises(updates.CheckError, match="readable"):
        latest("https://dev.itch.io/my-game", wharf({"html5": "<html>Just a moment...</html>"}))


# TFGames and Patreon


@pytest.mark.parametrize(
    "url", ["https://f95zone.to/threads/123456/", "https://tfgames.site/?module=viewgame&id=1"]
)
def test_adult_sources_are_off_by_default(url: str) -> None:
    fetch = FakeFetch({"https://f95zone.to/threads/123456/": fixture("f95_thread.html")})
    with pytest.raises(updates.ManualCheck, match="ADULT_SOURCES"):
        updates.check_url(url, fetch)
    assert fetch.urls == []


def test_tfgames_is_manual_without_request() -> None:
    fetch = FakeFetch()
    with pytest.raises(updates.ManualCheck, match="bot protection"):
        latest("https://tfgames.site/?module=viewgame&id=2000", fetch)
    assert fetch.urls == []


class PatreonFetch(FakeFetch):
    def __call__(self, url, headers=None):
        self.urls.append(url)
        if url == "https://www.patreon.com/examplestudio":
            return updates.Response(url, 200, fixture("patreon_creator.html"))
        if url.startswith("https://www.patreon.com/api/posts?"):
            query = parse_qs(urlsplit(url).query)
            assert query["filter[campaign_id]"] == ["12345"]
            assert query["sort"] == ["-published_at"]
            return updates.Response(url, 200, self.posts)
        raise updates.CheckError("404")

    posts = fixture("patreon_posts.json")


@pytest.mark.parametrize(
    "url",
    [
        "https://www.patreon.com/examplestudio",
        "https://patreon.com/c/examplestudio/posts",
        "https://www.patreon.com/examplestudio/about",
    ],
)
def test_patreon_version_from_post_title(url: str) -> None:
    fetch = PatreonFetch()
    result = latest(url, fetch)
    assert result.version == "v0.6.1"
    assert result.date == "2026-06-15"
    assert len(fetch.urls) == 2


def test_patreon_campaign_id_in_link_skips_creator_page() -> None:
    fetch = PatreonFetch()
    latest("https://www.patreon.com/api/posts?filter[campaign_id]=12345", fetch)
    assert len(fetch.urls) == 1


def test_patreon_without_version_needs_manual_check() -> None:
    posts = '{"data":[{"attributes":{"title":"Summer 2026 update","published_at":"2026-09-02"}}]}'
    with pytest.raises(updates.ManualCheck):
        patreon.parse_posts(posts)
    with pytest.raises(updates.CheckError, match="creator page"):
        latest("https://www.patreon.com/posts/abc-123", FakeFetch())
