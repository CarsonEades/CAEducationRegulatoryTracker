"""Assembly and Senate budget committees (abgt.assembly.ca.gov, sbud.senate.ca.gov).

Both sites run the same CMS. A committee's agenda page lists hearings as a bold date
line ("May 19, 2026 Hearing", "Wednesday, August 12th at 9:30 a.m. - State Capitol,
room 112") followed by links to files (/media/N, which redirect to the PDF): the agenda,
handouts, LAO reports. Agendas are PDFs, so the pipeline reads their items from the
downloaded text (DocLink.agenda).

Quirks handled here:
- Assembly pages group hearings under "2026 Agendas" headings and drop the year from
  older entries; older sections name the hearing in the link itself ("May 20 Agenda: ...").
- Senate pages often leave out the year ("Wednesday, May 20"); it's inferred as the date
  closest to today.
- The Senate full committee page puts the "Next Hearing" agenda link *before* the date
  line, so links that come before any date in a section go with the next date that isn't
  cancelled.
- Each committee's "home" page also lists reports (floor reports, highlights of the
  Governor's budget); those are publications (`LegislatureReports`).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from tracker.agencies import ABGT, ASSEMBLY_SUBCOMMITTEES, BY_ID, SBUD, SENATE_SUBCOMMITTEES
from tracker.http import Fetcher
from tracker.models import DocLink, Posting
from tracker.scrapers import register
from tracker.scrapers.base import Scraper
from tracker.scrapers.common import MONTHS, clean, is_document, long_date, needs_details, parse_long_date, text

# Hearings older than this aren't listed at all: the pages go back several sessions, and
# each old hearing would cost document downloads on a source's first run.
HISTORY_DAYS = 400

WEEKDAY = r"(?:(?:mon|tues|wednes|thurs|fri|satur|sun)day,?\s+)?"
DATE_LINE = re.compile(
    rf"^{WEEKDAY}({MONTHS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s*,\s*|\s+)?(\d{{4}})?", re.I)
# "September 2025 Hearing": a month without a day. Its links can't be placed on a date.
MONTH_ONLY = re.compile(rf"^{WEEKDAY}({MONTHS})\.?\s+(\d{{4}})\b", re.I)
YEAR_HEADING = re.compile(r"^(\d{4})\s+Agendas?\b", re.I)
# Older Assembly entries name the hearing in the link: "May 20 Agenda: May Revision".
LINK_DATE = re.compile(rf"^({MONTHS})\.?\s+(\d{{1,2}})\b.*\bagenda\b", re.I)
TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b\.?", re.I)
PLACE_WORDS = re.compile(r"\b(room|rm\.?|street|capitol|suite|building)\b", re.I)
# What follows "Agenda" in a link label is the hearing's subject: "Hearing Agenda:  May
# Revision", "May 19 - Sub. 4 Agenda: May Revision", "Hearing Agenda Part 1:  PreK-12".
AGENDA_SUBJECT = re.compile(r"\bagenda\b(?:\s+part\s+\w+\b)?\s*(?:\[pdf\])?\s*[:\-–—]?\s*(.*)$", re.I)


@dataclass
class Hearing:
    date: dt.date | None
    heading: str
    start_time: dt.time | None = None
    location: str | None = None
    cancelled: bool = False
    subject: str | None = None
    links: list[tuple[str, str]] = field(default_factory=list)  # (label, url)


def _year_for(month: str, day: int, year: int | None, today: dt.date) -> dt.date | None:
    s = f"{month} {day}" + (f", {year}" if year else "")
    return parse_long_date(s, today)


def parse_time(s: str) -> dt.time | None:
    m = TIME.search(s)
    if not m:
        return None
    hour = int(m[1]) % 12 + (12 if m[3].lower() == "p" else 0)
    try:
        return dt.time(hour, int(m[2] or 0))
    except ValueError:
        return None


def parse_location(s: str) -> str | None:
    """'Thursday, May 28th - Upon Adjournment of Session - 1021 O Street, Rm 2100'
    -> '1021 O Street, Rm 2100'."""
    for part in re.split(r"\s+[-–—]\s+", s)[1:]:
        if PLACE_WORDS.search(part):
            return clean(part)
    return None


def _date_from_line(line: str, section_year: int | None, today: dt.date) -> dt.date | None:
    m = DATE_LINE.match(line)
    if not m:
        return None
    year = int(m[3]) if m[3] else section_year
    return _year_for(m[1], int(m[2]), year, today)


def _blocks(root: Tag) -> list[Tag]:
    """Headings, paragraphs and list items in page order, outermost only."""
    names = ("h1", "h2", "h3", "h4", "h5", "p", "li")
    return [el for el in root.find_all(names) if not el.find_parent(names)]


def content_root(soup: BeautifulSoup) -> Tag:
    return (soup.select_one("article .text-long") or soup.select_one("article")
            or soup.find("main") or soup)


def parse_hearings(html: bytes, base_url: str, today: dt.date) -> list[Hearing]:
    soup = BeautifulSoup(html, "html.parser")
    root = content_root(soup)
    hearings: list[Hearing] = []
    current: Hearing | None = None
    pending: list[tuple[str, str]] = []  # links seen before this section's first date
    section_year: int | None = None

    def start(h: Hearing) -> Hearing:
        nonlocal pending
        hearings.append(h)
        if pending and not h.cancelled:
            h.links.extend(pending)
            pending = []
        return h

    for block in _blocks(root):
        line = text(block)
        if not line:
            continue
        links = [(text(a), Scraper.absolute(base_url, a["href"])) for a in block.find_all("a", href=True)
                 if "/media/" in a["href"] or is_document(a["href"])]
        if block.name.startswith("h"):
            m = YEAR_HEADING.match(line)
            if m:
                section_year = int(m[1])
            current, pending = None, []
            if not links:
                continue
        if not links:
            date = _date_from_line(line, section_year, today)
            if date:
                rest = re.sub(r"\s+hearing$", "", line, flags=re.I)
                current = start(Hearing(date=date, heading=rest, start_time=parse_time(line),
                                        location=parse_location(line),
                                        cancelled="cancel" in line.lower()))
            elif MONTH_ONLY.match(line):
                current = None  # no day given; its links can't be dated
                pending = []
            elif current and line.lower().startswith("subject:"):
                current.subject = clean(line.split(":", 1)[1])
            continue
        for label, url in links:
            m = LINK_DATE.match(label)
            if m:
                date = _year_for(m[1], int(m[2]), section_year, today)
                if date and (current is None or current.date != date):
                    current = start(Hearing(date=date, heading=label))
            if current is not None:
                current.links.append((label, url))
            else:
                pending.append((label, url))
    return hearings


def subject_from(label: str) -> str | None:
    """'Hearing Agenda:  May Revision' -> 'May Revision'; 'Hearing Agenda' -> None."""
    m = AGENDA_SUBJECT.search(label)
    rest = clean(m[1]) if m else ""
    rest = re.sub(r"\s*\[pdf\]$", "", rest, flags=re.I).strip(" -–—:")
    return rest or None


def to_postings(hearings: list[Hearing], listing_url: str, today: dt.date) -> list[Posting]:
    postings: list[Posting] = []
    keys: dict[str, int] = {}
    for h in hearings:
        if h.date is None or h.date < today - dt.timedelta(days=HISTORY_DAYS):
            continue
        agenda_links = [(label, url) for label, url in h.links if "agenda" in label.lower()]
        subjects = list(dict.fromkeys(s for s in (subject_from(lbl) for lbl, _ in agenda_links) if s))
        subject = h.subject or " / ".join(subjects) or None
        title = f"{long_date(h.date)} hearing" + (f": {subject}" if subject else "")
        if h.cancelled:
            title += " (cancelled)"
        key = h.date.isoformat()
        keys[key] = keys.get(key, 0) + 1
        if keys[key] > 1:
            key += f"-{keys[key]}"  # two hearings listed on one day
        seen: set[str] = set()
        docs = []
        for label, url in h.links:
            if url not in seen:
                seen.add(url)
                docs.append(DocLink(url, label or None, agenda="agenda" in label.lower()))
        postings.append(Posting(
            external_key=key,
            title=title,
            meeting_date=h.date,
            url=agenda_links[0][1] if agenda_links else listing_url,
            documents=docs,
            partial=not needs_details(h.date, today),
            start_time=h.start_time,
            location=h.location,
        ))
    return postings


class CommitteeHearings(Scraper):
    """One committee's agenda page."""
    base: str

    def fetch(self, fetcher: Fetcher, today: dt.date | None = None) -> list[Posting]:
        today = today or dt.date.today()
        url = BY_ID[self.agency_id].url
        return self.parse(fetcher.get(url).content, url, today)

    def parse(self, html: bytes, url: str, today: dt.date) -> list[Posting]:
        hearings = parse_hearings(html, url, today)
        undated = [h for h in hearings if not h.links and not h.cancelled and h.date
                   and h.date >= today]
        if undated:
            self.warnings.append(
                f"{len(undated)} upcoming hearing(s) without an agenda yet")
        return to_postings(hearings, url, today)


def _committee(agency_id: str, base: str) -> type[CommitteeHearings]:
    cls = type(f"Hearings_{agency_id}", (CommitteeHearings,), {"agency_id": agency_id, "base": base})
    return register(cls)


ASSEMBLY = [_committee("abgt_full", ABGT)] + [_committee(f"abgt_sub{n}", ABGT)
                                              for n, _, _ in ASSEMBLY_SUBCOMMITTEES]
SENATE = [_committee("sbud_full", SBUD)] + [_committee(f"sbud_sub{n}", SBUD)
                                            for n, _, _ in SENATE_SUBCOMMITTEES]


class LegislatureReports(Scraper):
    """Reports linked from a budget committee's home page: floor reports, highlights of the
    Governor's budget and May Revision, budget plans. One publication per file."""

    def fetch(self, fetcher: Fetcher, today: dt.date | None = None) -> list[Posting]:
        url = BY_ID[self.agency_id].url
        return self.parse(fetcher.get(url).content, url, today or dt.date.today())

    def parse(self, html: bytes, url: str, today: dt.date) -> list[Posting]:
        soup = BeautifulSoup(html, "html.parser")
        root = content_root(soup)
        postings: dict[str, Posting] = {}
        for a in root.find_all("a", href=True):
            if "/media/" not in a["href"]:
                continue
            label = re.sub(r"\s*\[pdf\]$", "", text(a).lstrip("-– ").strip(), flags=re.I)
            href = self.absolute(url, a["href"])
            if not label or href in postings:
                continue
            m = re.search(r"/media/(\d+)", href)
            title = label
            # Links listed under a heading item, e.g. "2026-27 Assembly Democratic Budget Plan
            # (links below)" > "Subcommittee No. 1 on Health": name the parent too.
            item = a.find_parent("li")
            parent = item.find_parent("li") if item else None
            if parent is not None:
                own = clean(" ".join(parent.find_all(string=True, recursive=False)))
                own = re.sub(r"\s*\(links? below\)", "", own, flags=re.I).rstrip(":")
                if own and own.lower() not in label.lower():
                    title = f"{own}: {label}"
            has_date = re.search(r"\b\d{4}\b", label) and re.search(MONTHS, label, re.I)
            date = parse_long_date(label, today) if has_date else None
            postings[href] = Posting(
                external_key=f"media-{m[1]}" if m else href,
                title=title,
                kind="publication",
                meeting_date=date,
                url=href,
                documents=[DocLink(href, label)],
            )
        return list(postings.values())


@register
class AssemblyReports(LegislatureReports):
    agency_id = "abgt_reports"


@register
class SenateReports(LegislatureReports):
    agency_id = "sbud_reports"
