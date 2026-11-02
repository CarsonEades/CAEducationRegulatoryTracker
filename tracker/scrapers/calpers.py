"""CalPERS Board of Administration and committee meetings. The board meetings
page links the current month's committee agenda pages (e.g. pension-202609);
each of those is one posting."""

from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup, Tag

from tracker.http import Fetcher
from tracker.models import DocLink, Item, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import clean, needs_details, parse_long_date, text

BASE = "https://www.calpers.ca.gov"
LISTING = f"{BASE}/about/board/board-meetings"
RECENT_MONTHS = 2
MEETING_LINK = re.compile(r"/about/board/board-meetings/([a-z-]+-\d{6})$")


def _month_end(yyyymm: str) -> dt.date:
    """Last day of the month in a page name like 'pension-202609'."""
    y, m = int(yyyymm[:4]), int(yyyymm[4:])
    return dt.date(y + m // 12, m % 12 + 1, 1) - dt.timedelta(days=1)


@register
class CalPERS(Scraper):
    agency_id = "calpers"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        postings = []
        for url in self.parse_listing(fetcher.get(LISTING).content):
            key = MEETING_LINK.search(url)[1]
            month_end = _month_end(key[-6:])
            if not needs_details(month_end):
                postings.append(Posting(external_key=key, title=key, url=url, partial=True))
                continue
            postings.append(self.parse_meeting(fetcher.get(url).content, url))
        return postings

    def parse_listing(self, html: bytes) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        urls: list[str] = []
        for a in (soup.find("main") or soup).find_all("a", href=True):
            href = self.absolute(BASE, a["href"])
            if MEETING_LINK.search(href) and href not in urls:
                urls.append(href)
        # The page also links years of past meetings; only follow the two latest months.
        months = sorted({u[-6:] for u in urls}, reverse=True)[:RECENT_MONTHS]
        return [u for u in urls if u[-6:] in months]

    def parse_meeting(self, html: bytes, url: str) -> Posting:
        soup = BeautifulSoup(html, "html.parser")
        title = text(soup.find("h1")) or url.rsplit("/", 1)[-1]
        blocks = [b for b in soup.select(".block__content") if b.find("ol")]
        content = blocks[0] if blocks else (soup.find("main") or soup)
        items: list[Item] = []
        docs: list[DocLink] = []
        session = None
        # Numbering continues across the lists of the same session (the open
        # session is split around the closed one), matching CalPERS' item numbers.
        counts: dict[str | None, int] = {}
        for el in content.find_all(["h3", "ol"]):
            if el.name == "h3":
                session = text(el)
            elif el.find_parent("ol") is None:
                counts[session] = self._walk(el, "", session, items, docs, url,
                                             start=counts.get(session, 0))
        return Posting(
            external_key=MEETING_LINK.search(url)[1],
            title=title,
            meeting_date=parse_long_date(title),
            url=url,
            items=items,
            documents=docs,
        )

    def _walk(self, ol: Tag, parent_number: str, session: str | None,
              items: list[Item], docs: list[DocLink], url: str, start: int = 0) -> int:
        """Add the list's items; returns the last top-level number used."""
        i = start - 1
        for i, li in enumerate(ol.find_all("li", recursive=False), start=start):
            number = f"{parent_number}{chr(ord('a') + i)}" if parent_number else str(i + 1)
            own = clean(" ".join(
                t if isinstance(t, str) else t.get_text(" ")
                for t in li.children if not (isinstance(t, Tag) and t.name in ("ol", "ul"))
            ))
            own = re.sub(r"\s*\(PDF\)", "", own).strip()
            items.append(Item(title=own, number=number, description=session))
            index = len(items) - 1
            for a in li.find_all("a", href=True):
                # Attachments sit in a nested bulleted list; sub-items in a nested numbered one.
                owner = a.find_parent("ol")
                if owner is ol and "/documents/" in a["href"]:
                    docs.append(DocLink(self.absolute(BASE, a["href"]),
                                        re.sub(r"\s*\(PDF\)", "", text(a)) or None, item_index=index))
            for sub in li.find_all("ol", recursive=False):
                self._walk(sub, number, session, items, docs, url)
        return i + 1
