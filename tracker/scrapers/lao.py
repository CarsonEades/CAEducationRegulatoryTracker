"""Legislative Analyst's Office: the budget index page (lao.ca.gov/Budget) and the
education policy area's publication list. One publication per LAO product; its HTML
page is downloaded so the full text is searched."""

from __future__ import annotations

import datetime as dt
import re

from bs4 import BeautifulSoup

from tracker.agencies import BY_ID
from tracker.http import Fetcher
from tracker.models import DocLink, Item, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import clean, parse_long_date, text

BASE = "https://www.lao.ca.gov"
PUBLICATION = re.compile(r"/Publications/(?:Detail|Report)/(\d+)", re.I)
SHORT_DATE = re.compile(r"\((\d{1,2})/(\d{1,2})/(\d{2,4})\)")
# Only publications from about the last year are listed; the pages go back decades.
HISTORY_DAYS = 365


def _short_date(s: str) -> dt.date | None:
    m = SHORT_DATE.search(s)
    if not m:
        return None
    year = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
    try:
        return dt.date(year, int(m[1]), int(m[2]))
    except ValueError:
        return None


def _recent(date: dt.date | None, today: dt.date) -> bool:
    return date is not None and date >= today - dt.timedelta(days=HISTORY_DAYS)


@register
class LAOBudget(Scraper):
    """The current budget year's index: publications listed under subject headings,
    each with a short date: "Overview of the Governor's Budget (1/12/26)"."""
    agency_id = "lao_budget"

    def fetch(self, fetcher: Fetcher, today: dt.date | None = None) -> list[Posting]:
        return self.parse(fetcher.get(BY_ID[self.agency_id].url).content, today or dt.date.today())

    def parse(self, html: bytes, today: dt.date) -> list[Posting]:
        soup = BeautifulSoup(html, "html.parser")
        root = soup.find("main") or soup
        postings: dict[str, Posting] = {}
        for li in root.find_all("li"):
            a = li.find("a", href=True)
            m = PUBLICATION.search(a["href"]) if a else None
            if not m or m[1] in postings:
                continue
            own = clean(" ".join(li.find_all(string=True, recursive=False)) + " "
                        + " ".join(text(e) for e in li.find_all("em", recursive=False)))
            date = _short_date(own)
            if not _recent(date, today):
                continue
            heading = li.find_previous(["h2", "h3", "h4"])
            url = self.absolute(BASE, a["href"])
            postings[m[1]] = Posting(
                external_key=m[1],
                title=text(a),
                kind="publication",
                meeting_date=date,
                url=url,
                items=[Item(title=text(heading))] if heading and text(heading) else [],
                documents=[DocLink(url, "LAO publication")],
            )
        return list(postings.values())


@register
class LAOEducation(Scraper):
    """The education policy area's publication list: type, title, formats, date and a
    one-sentence description per publication."""
    agency_id = "lao_education"

    def fetch(self, fetcher: Fetcher, today: dt.date | None = None) -> list[Posting]:
        return self.parse(fetcher.get(BY_ID[self.agency_id].url).content, today or dt.date.today())

    def parse(self, html: bytes, today: dt.date) -> list[Posting]:
        soup = BeautifulSoup(html, "html.parser")
        postings: dict[str, Posting] = {}
        for body in soup.select("div.media-body"):
            links = [a for a in body.find_all("a", href=True) if PUBLICATION.search(a["href"])]
            title = text(body.find(["h4", "h3"]))
            if not links or not title:
                continue
            pub_id = PUBLICATION.search(links[0]["href"])[1]
            p = body.find("p")
            summary = text(p)
            date = parse_long_date(summary.split(" - ", 1)[0], today) if summary else None
            if pub_id in postings or not _recent(date, today):
                continue
            description = clean(summary.split(" - ", 1)[1]) if " - " in summary else None
            kind = text(body.find("small"))
            # Prefer the HTML version for searching; PDFs are linked too.
            html_link = next((a for a in links if text(a).upper() == "HTML"), links[0])
            url = self.absolute(BASE, html_link["href"])
            postings[pub_id] = Posting(
                external_key=pub_id,
                title=title,
                kind="publication",
                meeting_date=date,
                url=url,
                items=[Item(title=description or title, number=kind or None)],
                documents=[DocLink(url, f"LAO {kind.lower() if kind else 'publication'}")],
            )
        return list(postings.values())
