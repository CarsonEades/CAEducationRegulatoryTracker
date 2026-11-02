"""California Department of Education pages: State Board of Education (SBE),
Advisory Commission on Charter Schools (ACCS), and the California
Practitioners Advisory Group (CPAG).

cde.ca.gov limits automated requests (see HOST_BUDGETS in tracker/http.py),
so each run only reads the listing and the most recent agenda pages. Agenda
pages list every item's subject, which is what keyword matching relies on
first; the item documents are downloaded a few per run."""

from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from tracker.http import Fetcher
from tracker.models import DocLink, Item, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import clean, is_document, long_date, parse_long_date

BASE = "https://www.cde.ca.gov"
RECENT = 2  # agenda pages read per run

ITEM_HEADER = re.compile(r"^Item\s+([A-Z]{0,2}-?\d+[A-Za-z]?)\b(?!.*Attachment)(.*)$")
STOP = re.compile(r"^(Type\s+of\s+Action|Revision\s+Notes|Item\s+\S+|Return to Top|Public Comment|\*+$|\()", re.I)


def parse_agenda_page(html: bytes, url: str) -> tuple[list[Item], list[DocLink]]:
    """Items ("Item 02 ... Subject: ...") and their documents from a CDE agenda page."""
    soup = BeautifulSoup(html, "html.parser")
    content = soup.find(id="content") or soup.body or soup
    for t in content(["script", "style", "nav"]):
        t.decompose()
    lines = [clean(ln) for ln in content.get_text("\n").split("\n")]
    lines = [ln for ln in lines if ln]

    items: dict[str, Item] = {}
    current: str | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        m = ITEM_HEADER.match(line)
        if m:
            current = m[1]
            items.setdefault(current, Item(title=clean(f"Item {m[1]}{m[2]}"), number=current))
        elif line.lower().startswith("subject:") and current:
            parts = [line.split(":", 1)[1]]
            j = i + 1
            while j < len(lines) and not STOP.match(lines[j]):
                parts.append(lines[j])
                j += 1
            subject = re.sub(r"\s+([.,;:])", r"\1", clean(" ".join(parts)))
            if subject and items[current].title.startswith("Item "):
                items[current].title = subject
            i = j
            continue
        i += 1

    order = list(items)
    docs: list[DocLink] = []
    for a in content.find_all("a", href=True):
        if not is_document(a["href"]):
            continue
        label = clean(a.get_text(" "))
        m = re.match(r"Item\s+([A-Z]{0,2}-?\d+[A-Za-z]?)", label)
        index = order.index(m[1]) if m and m[1] in items else None
        docs.append(DocLink(Scraper.absolute(url, a["href"]), label or None, item_index=index))
    return list(items.values()), docs


class _CDEAgency(Scraper):
    listing_url: str
    title_prefix: str

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        postings = []
        for key, url, label, date in self.parse_listing(fetcher.get(self.listing_url).content)[:RECENT]:
            items, docs = parse_agenda_page(fetcher.get(url).content, url)
            postings.append(Posting(
                external_key=key,
                title=f"{self.title_prefix} - {label}",
                meeting_date=date,
                url=url,
                items=items,
                documents=docs,
            ))
        return postings

    def parse_listing(self, html: bytes) -> list[tuple[str, str, str, dt.date | None]]:
        """(key, agenda URL, date label, date), newest first."""
        raise NotImplementedError


@register
class SBE(_CDEAgency):
    agency_id = "sbe"
    listing_url = f"{BASE}/be/ag/ag/index.asp"
    title_prefix = "State Board of Education Meeting"

    def parse_listing(self, html):
        soup = BeautifulSoup(html, "html.parser")
        content = soup.find(id="content") or soup
        found = {}
        for a in content.find_all("a", href=True):
            m = re.search(r"main(\d{4})(\d{2})\.asp$", a["href"])
            label = clean(a.get_text(" "))
            if m and parse_long_date(label):
                yyyymm = m[1] + m[2]
                url = Scraper.absolute(self.listing_url, f"yr{m[1][2:]}/agenda{yyyymm}.asp")
                # The year lists come first; later sidebar links use vaguer labels.
                found.setdefault(yyyymm, (yyyymm, url, label, parse_long_date(label)))
        return [found[k] for k in sorted(found, reverse=True)]


@register
class ACCS(_CDEAgency):
    agency_id = "accs"
    listing_url = f"{BASE}/be/cc/cs/accsagendas.asp"
    title_prefix = "Advisory Commission on Charter Schools Meeting"

    def parse_listing(self, html):
        soup = BeautifulSoup(html, "html.parser")
        content = soup.find(id="content") or soup
        found = {}
        for a in content.find_all("a", href=True):
            m = re.search(r"(accsnotice\d{6})\.asp$", a["href"])
            text = clean(a.get_text(" "))
            date = parse_long_date(text)
            if m and date:
                label = long_date(date) + (" (Canceled)" if "cancel" in text.lower() else "")
                found[m[1]] = (m[1], Scraper.absolute(self.listing_url, a["href"]), label, date)
        return sorted(found.values(), key=lambda r: r[3], reverse=True)


@register
class CPAG(_CDEAgency):
    agency_id = "cpag"
    # One list page per year. (Early in January, before CDE creates the new
    # year's page, this fails until it exists.)
    listing_url = f"{BASE}/be/cc/cp/cpag{dt.date.today().year}agendas.asp"
    title_prefix = "California Practitioners Advisory Group Meeting"

    def parse_listing(self, html):
        soup = BeautifulSoup(html, "html.parser")
        content = soup.find(id="content") or soup
        found = {}
        for a in content.find_all("a", href=True):
            m = re.search(r"(cpag\d{4}agenda\w+)\.asp$", a["href"])
            text = clean(a.get_text(" "))
            date = parse_long_date(text)
            if m and date:
                found[m[1]] = (m[1], Scraper.absolute(self.listing_url, a["href"]), long_date(date), date)
        return sorted(found.values(), key=lambda r: r[3], reverse=True)
