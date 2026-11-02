"""Small parsing helpers shared by the agency scrapers."""

from __future__ import annotations

import datetime as dt
import re

from bs4 import Tag

MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december"
          "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
LONG_DATE = re.compile(rf"\b({MONTHS})\.?\s+(\d{{1,2}})(?!\d)(?:\s*[-–]\s*\d{{1,2}})?(?:,?\s+(\d{{4}}))?",
                       re.IGNORECASE)


def clean(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def text(el: Tag | None) -> str:
    return clean(el.get_text(" ", strip=True)) if el else ""


def parse_long_date(s: str, today: dt.date | None = None) -> dt.date | None:
    """'October 28, 2026', 'Nov. 5-6, 2026', or 'November 3' (year inferred as
    the one that puts the date closest to today)."""
    m = LONG_DATE.search(s)
    if not m:
        return None
    month_name = m[1].lower()[:3]
    month = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
             "dec"].index(month_name) + 1
    day = int(m[2])
    if m[3]:
        years = [int(m[3])]
    else:
        today = today or dt.date.today()
        years = [today.year - 1, today.year, today.year + 1]
    candidates = []
    for y in years:
        try:
            candidates.append(dt.date(y, month, day))
        except ValueError:
            pass
    if not candidates:
        return None
    today = today or dt.date.today()
    return min(candidates, key=lambda d: abs((d - today).days))


# Detail pages (meeting agendas, staff reports) are re-read only for meetings
# that are upcoming or happened within this many days. Listing pages are read
# every run, so new meetings are still caught right away.
DETAIL_WINDOW_DAYS = 30


def needs_details(date: dt.date | None, today: dt.date | None = None) -> bool:
    today = today or dt.date.today()
    return date is None or date >= today - dt.timedelta(days=DETAIL_WINDOW_DAYS)


def long_date(d: dt.date) -> str:
    """'October 6, 2026'. (strftime has no portable no-padding day code; Windows lacks it.)"""
    return f"{d:%B} {d.day}, {d.year}"


def short_date(d: dt.date) -> str:
    """'Nov 3'."""
    return f"{d:%b} {d.day}"


def is_document(href: str) -> bool:
    path = href.lower().split("?", 1)[0]
    return path.endswith((".pdf", ".doc", ".docx", ".pptx", ".xlsx"))
