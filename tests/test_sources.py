"""Adapters of the individual sources with synthetic responses."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from app import updates
from app.db import DB_FILENAME, Database
from app.sources import f95, patreon
from tests.test_updates import FakeFetch

FIXTURES = Path(__file__).parent / "fixtures" / "updates"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def latest(url: str, fetch: FakeFetch) -> updates.Latest:
    return updates.check_url(url, fetch, adult_sources=True)


# F95Zone

THREAD_PAGE = "https://f95zone.to/threads/123456/"


def search_url(term: str) -> str:
    return f"{f95.LATEST_URL}?" + urlencode(
        {"cmd": "list", "cat": "games", "page": 1, "search": term}
    )


def latest_data(*entries: dict, count: int | None = None) -> str:
    """An answer of F95Zone's latest updates in the format seen on 2026-10-18."""
    return json.dumps(
        {
            "status": "ok",
            "msg": {
                "data": list(entries),
                "pagination": {"page": 1, "total": 1},
                "count": len(entries) if count is None else count,
            },
        }
    )


def entry(thread_id: int, title: str, version: str, ts: int = 1792268880) -> dict:
    return {
        "thread_id": thread_id,
        "title": title,
        "creator": "dev",
        "version": version,
        "prefixes": [4],
        "date": "24 hrs",
        "ts": ts,
    }


CHALLENGE = updates.BotProtection("f95zone.to asks for a browser check (bot protection)")


def test_f95_version_from_latest_updates() -> None:
    fetch = FakeFetch(
        {search_url("Disruption"): latest_data(entry(178604, "Disruption", "v0.79 Public"))}
    )
    result = updates.check_url(
        "https://f95zone.to/threads/disruption.178604/", fetch, True, "Disruption"
    )
    assert result.version == "v0.79 Public"
    assert result.date == "2026-10-17T20:28:00+00:00"
    assert fetch.urls == [search_url("Disruption")]


def test_f95_picks_the_thread_by_id() -> None:
    # The search finds similar games too; only the thread from the link counts
    answer = latest_data(
        entry(1, "Stepmother Effect Remake", "v9"), entry(145943, "Stepmother Effect", "v2.7.2a")
    )
    fetch = FakeFetch({search_url("Stepmother Effect"): answer})
    result = updates.check_url(
        "https://f95zone.to/threads/stepmother-effect.145943/", fetch, True, "Stepmother Effect"
    )
    assert result.version == "v2.7.2a"


def test_f95_special_characters_try_a_plain_title() -> None:
    # "Go!Go!PizzaBoy!" finds nothing, "Go Go PizzaBoy" finds the game
    fetch = FakeFetch(
        {
            search_url("Go!Go!PizzaBoy!"): latest_data(),
            search_url("Go Go PizzaBoy"): latest_data(entry(198840, "Go!Go!PizzaBoy!", "v0.3")),
        }
    )
    result = updates.check_url(
        "https://f95zone.to/threads/go-go-pizzaboy.198840/", fetch, True, "Go!Go!PizzaBoy!"
    )
    assert result.version == "v0.3"
    assert fetch.urls == [search_url("Go!Go!PizzaBoy!"), search_url("Go Go PizzaBoy")]


def test_f95_ignored_search_falls_back_to_first_words() -> None:
    # A long title with ~ makes F95Zone ignore the search and list all games
    title = "Loretta Maiden ~The Inferior Soldier with 0% Compatibility~"
    everything = latest_data(entry(1, "Other", "v1"), count=27796)
    fetch = FakeFetch(
        {
            search_url(title): everything,
            search_url("Loretta Maiden The Inferior Soldier with 0 Compatibility"): everything,
            search_url("Loretta Maiden"): latest_data(entry(319280, title, "v1.2")),
        }
    )
    url = "https://f95zone.to/threads/loretta-maiden-the-inferior-soldier.319280/"
    assert updates.check_url(url, fetch, True, title).version == "v1.2"


@pytest.mark.parametrize(
    ("title", "slug", "terms"),
    [
        ("Disruption", "disruption", ["Disruption"]),
        (
            "Other Side: Mop & Mayhem",
            None,
            ["Other Side: Mop & Mayhem", "Other Side Mop Mayhem", "Other Side"],
        ),
        ("Driftwood [v0.78] [Dev]", "driftwood-v0-78-public-exampledev", ["Driftwood"]),
        ("Driftwood v0.78", None, ["Driftwood"]),
        ("The Final Exam", None, ["The Final Exam", "The Final"]),
        ("Driftwood 0.11", None, ["Driftwood"]),
        (None, "driftwood-v0-78-public-exampledev", ["driftwood"]),
        (None, None, []),
    ],
)
def test_f95_search_terms(title: str | None, slug: str | None, terms: list[str]) -> None:
    assert f95.search_terms(title, slug) == terms


def test_f95_search_terms_are_limited() -> None:
    terms = f95.search_terms("Other Side: Mop & Mayhem", "other-side-mop-and-mayhem")
    assert len(terms) == f95.MAX_SEARCHES


@pytest.mark.parametrize(
    "url",
    [
        "https://f95zone.to/threads/driftwood-v0-78-public-exampledev.123456/",
        "https://f95zone.to/threads/123456/",
        "https://f95zone.to/threads/driftwood-v0-78-public-exampledev.123456/page-12#post-1",
    ],
)
def test_f95_thread_page_when_the_search_does_not_find_it(url: str) -> None:
    fetch = FakeFetch(
        {search_url("Driftwood"): latest_data(), search_url("driftwood"): latest_data()}
        | {THREAD_PAGE: fixture("f95_thread.html")}
    )
    result = updates.check_url(url, fetch, True, "Driftwood")
    assert result.version == "v0.78 Public"
    assert result.date == "2026-09-20"
    assert updates.compare_versions(result.version, "v.0.78") == 0
    assert fetch.urls[-1] == THREAD_PAGE


def test_f95_thread_page_when_the_search_is_broken() -> None:
    fetch = FakeFetch(
        {search_url("Driftwood"): "<html>changed</html>", THREAD_PAGE: fixture("f95_thread.html")}
    )
    assert updates.check_url(THREAD_PAGE, fetch, True, "Driftwood").version == "v0.78 Public"
    assert fetch.urls == [search_url("Driftwood"), THREAD_PAGE]


def test_f95_bot_protection_means_check_by_hand(tmp_path: Path) -> None:
    fetch = FakeFetch({search_url("Driftwood"): latest_data(), THREAD_PAGE: CHALLENGE})
    with pytest.raises(updates.ManualCheck, match="did not find this thread"):
        updates.check_url(THREAD_PAGE, fetch, True, "Driftwood")

    db = Database(tmp_path / DB_FILENAME)
    game = db.create_game("Driftwood", "index.html", "Driftwood")
    game = db.update_game(game["id"], {"version": "v0.77", "update_url": THREAD_PAGE})
    result = updates.check_game(db, game, fetch, adult_sources=True)
    assert result["status"] == updates.STATUS_MANUAL
    assert "by hand" in result["error"]


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
