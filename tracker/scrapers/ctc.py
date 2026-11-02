"""Commission on Teacher Credentialing meetings portal (meetings.ctc.ca.gov).

The listing page fills its grid from a JSON endpoint; each meeting's detail
page lists the agenda items and their documents."""

from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from tracker.http import Fetcher
from tracker.models import DocLink, Item, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import needs_details

BASE = "https://meetings.ctc.ca.gov"
# Most recent meetings to check each run (includes upcoming ones).
RECENT = 20


@register
class CTC(Scraper):
    agency_id = "ctc"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        resp = fetcher.post(
            f"{BASE}/GetDataSourceResult",
            data={"sort": "BeginOn-desc", "page": "1", "pageSize": str(RECENT),
                  "group": "", "filter": ""},
            headers={"X-Requested-With": "XMLHttpRequest"},
        )
        postings = []
        for m in resp.json()["Data"]:
            detail_url = f"{BASE}/Details/{m['Id']}"
            p = self.posting(m, detail_url, [], [])
            if needs_details(p.meeting_date):
                p.items, p.documents = self.parse_details(fetcher.get(detail_url).content)
            else:
                p.partial = True
            postings.append(p)
        return postings

    @staticmethod
    def posting(m: dict, url: str, items: list[Item], docs: list[DocLink]) -> Posting:
        begin = m.get("BeginOn")
        return Posting(
            external_key=str(m["Id"]),
            title=f"{m['Title']} ({m.get('TypeName') or 'Meeting'})",
            meeting_date=dt.date.fromisoformat(begin[:10]) if begin else None,
            url=url,
            items=items,
            documents=docs,
        )

    def parse_details(self, html: bytes) -> tuple[list[Item], list[DocLink]]:
        soup = BeautifulSoup(html, "html.parser")
        items: list[Item] = []
        docs: list[DocLink] = []
        for box in soup.find_all(id="AgendaItemTitleContainer"):
            title_el = box.find("b")
            if not title_el:
                continue
            title = re.sub(r"\s+", " ", title_el.get_text(" ", strip=True))
            m = re.match(r"Item\s+([\w.]+)\s*[-–]\s*(.+)", title)
            number, text = (m[1], m[2]) if m else (None, title)
            badge = box.find(class_="badge")
            items.append(Item(title=text, number=number,
                              description=badge.get_text(strip=True) if badge else None))
            for a in box.find_all("a", href=True):
                if a["href"].startswith("/Document/Download/"):
                    label = a.get_text(" ", strip=True)
                    if label == "Download Agenda Item":
                        label = f"Item {number or ''} agenda item".replace("  ", " ")
                    docs.append(DocLink(self.absolute(BASE, a["href"]), label,
                                        item_index=len(items) - 1))
        return items, docs
