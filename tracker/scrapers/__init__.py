"""Registry of per-agency scrapers. An agency without an entry here is
tracked in the dashboard but marked "scraper not built yet"."""

from __future__ import annotations

from tracker.scrapers.base import Scraper

SCRAPERS: dict[str, type[Scraper]] = {}


def register(cls: type[Scraper]) -> type[Scraper]:
    SCRAPERS[cls.agency_id] = cls
    return cls


def get_scraper(agency_id: str) -> Scraper | None:
    cls = SCRAPERS.get(agency_id)
    return cls() if cls else None


# Agency scraper modules register themselves on import. Add new ones here, e.g.:
from tracker.scrapers import calpers, calstrs, ccee, cde, csfa, ctc, eaap, fcc, fppc, oag, opsc  # noqa: E402,F401
from tracker.scrapers import dof, lao, legislature  # noqa: E402,F401
