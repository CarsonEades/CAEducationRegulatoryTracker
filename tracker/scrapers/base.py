"""Base class for per-agency scrapers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from tracker.http import Fetcher
from tracker.models import Posting


class Scraper(ABC):
    #: Matches tracker.agencies.Agency.id
    agency_id: str

    def __init__(self) -> None:
        # Problems worth showing on the health page that don't fail the check, e.g. an
        # agenda PDF where no items could be found. Filled in during fetch().
        self.warnings: list[str] = []

    @abstractmethod
    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        """Return the postings currently listed on the agency's site."""

    # Helpers shared by the HTML scrapers.

    @staticmethod
    def soup(fetcher: Fetcher, url: str) -> BeautifulSoup:
        resp = fetcher.get(url)
        return BeautifulSoup(resp.content, "html.parser")

    @staticmethod
    def absolute(base: str, href: str) -> str:
        return urljoin(base, href.strip())
