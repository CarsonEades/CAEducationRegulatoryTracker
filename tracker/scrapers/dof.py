"""Department of Finance: trailer bill language, the budget releases on ebudget.ca.gov,
and Finance Letters.

The trailer bill page (dof.ca.gov/esdapp/trailer-bill/) and ebudget.ca.gov are JavaScript
apps; both load their content from public JSON endpoints, which are read here directly
instead of the HTML (lessons: look for an API before scraping).
"""

from __future__ import annotations

import datetime as dt
import json
import re

from bs4 import BeautifulSoup

from tracker.http import Fetcher
from tracker.models import DocLink, Item, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import clean, is_document, parse_long_date, text

TRAILER_API = "https://trailerbill.dof.ca.gov/public"
EBUDGET = "https://ebudget.ca.gov"
BUDGET_DETAILS = "https://dof.ca.gov/budget/budget-details/"
STAGES = {"p": "Governor's Budget", "m": "May Revision", "e": "Enacted Budget"}


def load_json(body: bytes):
    """These APIs sometimes wrap their JSON in a list of string chunks:
    ["{\\"pits\\":...", "...}"]. Unwrap that."""
    data = json.loads(body)
    if isinstance(data, list) and data and all(isinstance(x, str) for x in data):
        return json.loads("".join(data))
    return data


def _us_date(s: str | None) -> dt.date | None:
    """'05/26/2026' or '2026-06-22T15:22:51.2410000'."""
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


@register
class TrailerBills(Scraper):
    """One publication per trailer bill proposal. The listed date is when DOF last
    updated it, so a revision shows as an update and its PDF is downloaded again."""
    agency_id = "dof_trailer_bills"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        info = load_json(fetcher.get(f"{TRAILER_API}/getAppInfo").content)
        trackers = {t["pit"]: t for t in info.get("trackers", [])}
        postings: list[Posting] = []
        # "h" is last year's chaptered trailer bills, a different kind of list.
        for pit in [p for p in info.get("pits", []) if p != "h"]:
            bills = load_json(fetcher.get(f"{TRAILER_API}/getTrailerBillsByPit/{pit}").content)
            postings += self.parse(bills, trackers.get(pit, {}).get("title") or STAGES.get(pit, pit))
        return postings

    def parse(self, bills: dict, tracker_title: str) -> list[Posting]:
        postings = []
        for category, entries in bills.items():
            for b in entries:
                pdf = f"{TRAILER_API}/pdf/{b['trailerBillId']}"
                postings.append(Posting(
                    external_key=f"tbl-{b['trailerBillId']}",
                    title=clean(b.get("title")) or f"Trailer bill {b['trailerBillId']}",
                    kind="publication",
                    meeting_date=_us_date(b.get("lastUpdDate")),
                    url=pdf,
                    items=[Item(title=clean(b.get("categoryName") or category),
                                description=tracker_title)],
                    documents=[DocLink(pdf, "Trailer bill language")],
                ))
        return postings


@register
class BudgetReleases(Scraper):
    """The Governor's Budget, May Revision and Enacted Budget for the latest budget year,
    each with its full Budget Summary PDF."""
    agency_id = "dof_budget"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        links = load_json(fetcher.get(f"{EBUDGET}/api/home/getLink").content)
        if not links:
            return []
        latest = links[0]
        postings = []
        for pit in ("p", "m", "e"):
            summary = latest.get(f"summary_{pit}")
            if not summary:
                continue  # not released yet
            app_type = summary.strip("/").split("/")[0]  # "budget" or "publication"
            try:
                info = load_json(fetcher.get(
                    f"{EBUDGET}/api/{app_type}/{pit}/{latest['budgetYear']}/appInfo").content)
            except Exception as e:  # one stage failing shouldn't hide the others
                self.warnings.append(f"{STAGES[pit]} {latest['budgetYear']}: {e}")
                continue
            postings.append(self.posting(latest["budgetYear"], pit, summary, info))
        return postings

    @staticmethod
    def posting(year: str, pit: str, summary_path: str, info: dict) -> Posting:
        folder = "" if pit == "p" else f"{info.get('publication')}/"
        pdf = f"{EBUDGET}/{year}/pdf/{folder}BudgetSummary/FullBudgetSummary.pdf"
        return Posting(
            external_key=f"{year}-{pit}",
            title=f"{STAGES[pit]} {year}",
            kind="publication",
            meeting_date=parse_long_date(info.get("publicationDate") or ""),
            url=f"{EBUDGET}{summary_path}",
            items=[Item(title=info.get("publicationTitle") or STAGES[pit],
                        description=info.get("publicationDate"))],
            documents=[DocLink(pdf, "Full Budget Summary (PDF)")],
        )


@register
class FinanceLetters(Scraper):
    """Finance Letters (April 1 and May adjustments to the Governor's Budget), linked from
    the Budget Details page. One publication per set, with a PDF per subject area."""
    agency_id = "dof_finance_letters"

    def fetch(self, fetcher: Fetcher) -> list[Posting]:
        postings = []
        for label, url in self.letter_pages(fetcher.get(BUDGET_DETAILS).content):
            postings.append(self.parse_letters(fetcher.get(url).content, url, label))
        return postings

    def letter_pages(self, html: bytes) -> list[tuple[str, str]]:
        soup = BeautifulSoup(html, "html.parser")
        root = soup.find("main") or soup
        pages = []
        for a in root.find_all("a", href=True):
            label = text(a)
            if "historical-budget-publications/" in a["href"] and re.search(
                    r"adjustments|finance letter", label, re.I):
                pages.append((label, self.absolute(BUDGET_DETAILS, a["href"])))
        return list(dict.fromkeys(pages))

    def parse_letters(self, html: bytes, url: str, label: str) -> Posting:
        soup = BeautifulSoup(html, "html.parser")
        root = soup.find("main") or soup
        title = text(root.find("h1")) or label
        items, docs = [], []
        for a in root.find_all("a", href=True):
            if not is_document(a["href"]) and "/media/docs/" not in a["href"]:
                continue
            area = text(a)
            items.append(Item(title=area))
            docs.append(DocLink(self.absolute(url, a["href"]), area, item_index=len(items) - 1))
        return Posting(
            external_key=url.rstrip("/").rsplit("/", 1)[-1],
            title=title,
            kind="publication",
            meeting_date=None,  # the page gives no date; listed by when the tracker found it
            url=url,
            items=items,
            documents=docs,
        )
