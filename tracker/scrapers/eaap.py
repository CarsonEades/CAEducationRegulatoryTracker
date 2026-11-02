"""Education Audit Appeals Panel meetings.

The agendas page lists each meeting date as a heading followed by its
documents (agenda, summary review and formal appeals reports, minutes). The
reports name the districts and charter schools with pending audit appeals,
so they're downloaded and searched like any agenda."""

from __future__ import annotations

import datetime as dt

from bs4 import BeautifulSoup

from tracker.http import Fetcher
from tracker.models import DocLink, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import LONG_DATE, is_document, parse_long_date, text

LISTING = "https://eaap.ca.gov/panel/agendas/"
# The page keeps several years of meetings; only track roughly the last year.
HISTORY_DAYS = 400


@register
class EAAP(Scraper):
    agency_id = "eaap"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        return self.parse(fetcher.get(LISTING).content)

    def parse(self, html: bytes, today: dt.date | None = None) -> list[Posting]:
        today = today or dt.date.today()
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main") or soup
        meetings: list[tuple[str, dt.date, list[DocLink]]] = []
        for el in main.find_all(["h2", "h3", "h4", "h5", "h6", "a"]):
            if el.name == "a":
                if meetings and el.get("href") and is_document(el["href"]):
                    meetings[-1][2].append(DocLink(self.absolute(LISTING, el["href"]), text(el) or None))
                continue
            label = text(el)
            m = LONG_DATE.fullmatch(label)
            if m and m[3]:
                meetings.append((label, parse_long_date(label), []))

        postings: dict[str, Posting] = {}
        for label, date, docs in meetings:
            if not docs or date < today - dt.timedelta(days=HISTORY_DAYS):
                continue
            agenda = next((d for d in docs if (d.label or "").lower() == "agenda"), docs[0])
            postings.setdefault(date.isoformat(), Posting(
                external_key=date.isoformat(),
                title=f"EAAP Meeting - {label}",
                meeting_date=date,
                url=agenda.url,
                documents=docs,
            ))
        return list(postings.values())
