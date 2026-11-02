"""California School Finance Authority board meetings (State Treasurer's site)."""

from __future__ import annotations

import re

from bs4 import BeautifulSoup, NavigableString, Tag

from tracker.http import Fetcher
from tracker.models import DocLink, Item, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import LONG_DATE, clean, is_document, needs_details, parse_long_date, text

LISTING = "https://www.treasurer.ca.gov/csfa/meeting"


@register
class CSFA(Scraper):
    agency_id = "csfa"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        postings = []
        for m in self.parse_listing(fetcher.get(LISTING).content):
            items: list[Item] = []
            docs = [DocLink(u, label) for label, u in m["links"] if is_document(u)]
            staff = next((u for label, u in m["links"] if "staff report" in label.lower()), None)
            partial = not needs_details(m["date"])
            if staff and not partial:
                items, staff_docs = self.parse_staff_page(fetcher.get(staff).content, staff)
                docs += staff_docs
            title = f"CSFA Board Meeting - {m['label']}" + (" (Cancelled)" if m["cancelled"] else "")
            postings.append(Posting(
                external_key=m["date"].isoformat(),
                title=title,
                meeting_date=m["date"],
                url=staff or next((u for _, u in m["links"]), LISTING),
                items=items,
                documents=docs,
                partial=partial,
            ))
        return postings

    def parse_listing(self, html: bytes) -> list[dict]:
        """Meeting dates in page order, each with the links that follow it."""
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main") or soup
        meetings: list[dict] = []
        for node in main.descendants:
            if isinstance(node, NavigableString):
                if node.parent.name == "a":
                    continue
                s = clean(str(node))
                m = LONG_DATE.fullmatch(s) if s else None
                if m and m[3]:
                    meetings.append({"label": s, "date": parse_long_date(s), "links": [],
                                     "cancelled": False})
                elif meetings and re.fullmatch(r"cancell?ed", s, re.I):
                    meetings[-1]["cancelled"] = True
            elif isinstance(node, Tag) and node.name == "a" and node.get("href") and meetings:
                href = node["href"]
                if href.startswith("mailto:"):
                    continue
                meetings[-1]["links"].append((text(node), self.absolute(LISTING, href)))
        return meetings

    def parse_staff_page(self, html: bytes, url: str) -> tuple[list[Item], list[DocLink]]:
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main") or soup
        items: list[Item] = []
        docs: list[DocLink] = []
        for li in main.find_all("li"):
            first = li.find("a", href=True)
            if not first or li.find_parent("li"):
                continue  # nested lists hold the item's other documents
            direct = li.find("a", href=True, recursive=False)
            m = re.match(r"Agenda Item\s+(\w+)", text(direct), re.I) if direct else None
            if not m and items:
                pass  # the page sometimes splits one item over two entries; keep its documents
            else:
                # Item text is the list item's own text, minus its links and nested lists.
                own = clean(" ".join(li.find_all(string=True, recursive=False))).lstrip("-–— ")
                items.append(Item(title=own or text(first), number=m[1] if m else None))
            for a in li.find_all("a", href=True):
                if is_document(a["href"]):
                    docs.append(DocLink(self.absolute(url, a["href"]), text(a) or None,
                                        item_index=len(items) - 1))
        return items, docs
