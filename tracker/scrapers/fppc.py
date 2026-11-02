"""Fair Political Practices Commission monthly hearings."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from tracker.http import Fetcher
from tracker.models import DocLink, Item, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import clean, is_document, needs_details, parse_long_date, text

LISTING = "https://www.fppc.ca.gov/about-fppc/commission-hearings/"
ITEM_START = re.compile(r"^(\d+)\.\s+(.*)$", re.S)


@register
class FPPC(Scraper):
    agency_id = "fppc"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        postings = []
        for url, label in self.parse_listing(fetcher.get(LISTING).content):
            p = Posting(
                # e.g. "2026/oct-2026-agenda"
                external_key="/".join(url.rstrip("/").lower().rsplit("/", 2)[-2:]),
                title=f"Commission Hearing - {label}",
                meeting_date=parse_long_date(label),
                url=url,
            )
            if needs_details(p.meeting_date):
                p.items, p.documents = self.parse_agenda(fetcher.get(url).content, url)
            else:
                p.partial = True
            postings.append(p)
        return postings

    def parse_listing(self, html: bytes) -> list[tuple[str, str]]:
        """(agenda URL, date label) for each hearing with a posted agenda."""
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main") or soup
        out: dict[str, str] = {}
        for a in main.find_all("a", href=True):
            if "/events/commission-hearings/" not in a["href"]:
                continue
            url = self.absolute(LISTING, a["href"])
            label = text(a)
            if parse_long_date(label):
                out[url] = label
            else:
                out.setdefault(url, label)  # "View current agenda": dated by its own link if listed
        return [(u, l) for u, l in out.items() if parse_long_date(l)]

    def parse_agenda(self, html: bytes, url: str) -> tuple[list[Item], list[DocLink]]:
        soup = BeautifulSoup(html, "html.parser")
        items: list[Item] = []
        docs: list[DocLink] = []
        seen: set[str] = set()
        for block in soup.select("main .rich-text"):
            for el in block.find_all(["h2", "h3", "p", "ul"], recursive=False):
                if el.name == "ul":
                    for a in el.find_all("a", href=True):
                        if is_document(a["href"]):
                            docs.append(DocLink(self.absolute(url, a["href"]), text(a) or None,
                                                item_index=len(items) - 1 if items else None))
                    continue
                m = ITEM_START.match(text(el))
                if not m:
                    continue
                number, rest = m[1], clean(m[2])
                if number in seen:
                    # The section heading came first; this paragraph is the item itself.
                    if items and items[-1].number == number and el.name == "p":
                        items[-1].description = rest
                    continue
                seen.add(number)
                if el.name == "p":
                    title, description = _split_first_sentence(rest)
                else:
                    title, description = rest, ""
                items.append(Item(title=title, number=number, description=description or None))
        return items, docs


def _split_first_sentence(s: str) -> tuple[str, str]:
    """Title is everything before "Staff:", or else the first sentence. A period
    after "No" ("FPPC Case No. 20/945") doesn't end a sentence."""
    if " Staff:" in s:
        title, _, rest = s.partition(" Staff:")
        return title.strip(), "Staff:" + rest
    m = re.match(r"(.+?(?<!\bNo)\.)\s+(.*)$", s, re.S)
    return (m[1], m[2]) if m else (s, "")
