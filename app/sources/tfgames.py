"""TFGames: only a hint to check by hand.

Feasibility (checked 2026-10-17): every page of tfgames.site, including the home
page, answers 403 with a Cloudflare challenge ("Just a moment…") that requires
JavaScript and cookies, regardless of the user agent. A request without a real
browser is therefore not possible, and getting around the bot protection is out
of the question. The adapter does not fetch anything.

Adult game site: only offered with ADULT_SOURCES=true.
"""

from app import updates


class TFGames:
    name = "TFGames"
    domains = ("tfgames.site",)
    adult = True

    def latest(self, url: str, fetch: updates.Fetch, title: str | None = None) -> updates.Latest:
        raise updates.ManualCheck(
            "TFGames does not allow automatic requests (bot protection). Please check the page"
            " by hand."
        )


updates.register(TFGames())
