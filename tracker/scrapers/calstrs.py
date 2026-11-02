"""CalSTRS Teachers' Retirement Board meetings. Each meeting page has one
accordion per committee, listing its items and documents."""

from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from tracker.http import Fetcher
from tracker.models import DocLink, Item, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import is_document, long_date, needs_details, parse_long_date, short_date, text

BASE = "https://www.calstrs.com"
LISTING = f"{BASE}/board-meetings"
DAY_HEADING = re.compile(r"[A-Z][a-z]+ \d{1,2}, \d{4}")
MEETING_LINK = re.compile(r"^/?(\d{4}-\d{2}-\d{2})-board-meeting")


@register
class CalSTRS(Scraper):
    agency_id = "calstrs"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        postings = []
        for url, date in self.parse_listing(fetcher.get(LISTING).content):
            if not needs_details(date):
                postings.append(Posting(external_key=url.rsplit("/", 1)[-1],
                                        title=f"Teachers' Retirement Board Meeting - {long_date(date)}",
                                        meeting_date=date, url=url, partial=True))
                continue
            title, items, docs = self.parse_meeting(fetcher.get(url).content, url)
            postings.append(Posting(
                external_key=url.rsplit("/", 1)[-1],
                title=f"{title} - {long_date(date)}",
                meeting_date=date,
                url=url,
                items=items,
                documents=docs,
            ))
        return postings

    def parse_listing(self, html: bytes):
        soup = BeautifulSoup(html, "html.parser")
        seen = {}
        for a in (soup.find("main") or soup).find_all("a", href=True):
            path = a["href"].replace(BASE, "")
            m = MEETING_LINK.match(path)
            if m:
                seen.setdefault(self.absolute(BASE, path), dt.date.fromisoformat(m[1]))
        return list(seen.items())

    def parse_meeting(self, html: bytes, url: str) -> tuple[str, list[Item], list[DocLink]]:
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main") or soup
        h1 = soup.find("h1")
        title = text(h1) if h1 else "Teachers' Retirement Board Meeting"
        items: list[Item] = []
        docs: list[DocLink] = []
        when = None
        for el in main.find_all(True):
            # A heading that is just a date ("November 3, 2026") starts each day's committees.
            if el.name != "div" and not el.find("div") and DAY_HEADING.fullmatch(text(el)):
                when = parse_long_date(text(el))
                continue
            if "accordion-wrapper" not in (el.get("class") or []):
                continue
            wrapper = el
            header = wrapper.select_one(".accordion-header")
            content = wrapper.select_one(".accordion-content")
            if not header or not content:
                continue
            committee = text(header)
            for el in content.find_all(["h4", "p", "a"]):
                if el.name == "h4":
                    m = re.match(r"Item\s+([\w.]+)\s*[-–]\s*(.+)", text(el))
                    number, item_title = (m[1], m[2]) if m else (None, text(el))
                    prefix = committee + (f" ({short_date(when)})" if when else "")
                    items.append(Item(title=item_title, number=number, description=prefix))
                elif el.name == "p" and items and el.find_previous("h4") is not None:
                    line = text(el)
                    if line.startswith("Session:"):
                        items[-1].description = f"{items[-1].description} · {line}"
                elif el.name == "a" and el.get("href") and is_document(el["href"]):
                    docs.append(DocLink(self.absolute(BASE, el["href"]), text(el) or None,
                                        item_index=len(items) - 1 if items else None))
        return title, items, docs
