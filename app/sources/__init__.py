"""Adapters of the online update check, one per source (see `app.updates`).

Each module registers its adapter on import; new sources are added here.
"""

from app.sources import f95, itch, patreon, tfgames  # noqa: F401  (register the adapters)
