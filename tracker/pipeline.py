"""Scrape agencies, store what changed, and run keyword matching."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field

import psycopg

from tracker.agencies import AGENCIES, BY_ID
from tracker.documents import FetchedDocument, fetch_document
from tracker.http import Fetcher
from tracker.models import Posting
from tracker.scrapers import get_scraper

log = logging.getLogger(__name__)

# Keep re-downloading documents for meetings up to this many days in the past,
# so revised agendas and late attachments are noticed.
ACTIVE_WINDOW_DAYS = 14
MAX_DOCS_PER_POSTING = 200
# Already-downloaded documents are re-checked at most this often. New links are
# always fetched right away; this just avoids re-downloading every PDF of a
# large meeting (CalSTRS posts 100+) on each run.
DOC_RECHECK_HOURS = 12
# Optionally check an agency at most every N hours (whether or not the last
# check succeeded), e.g. if its site starts refusing frequent checks. Set with
# the CHECK_EVERY_HOURS environment variable / repository variable, as
# "agency=hours" pairs: "eaap=6" or "eaap=6,ccee=4". Empty = every run.
def _check_every_hours() -> dict[str, float]:
    out = {}
    for pair in os.environ.get("CHECK_EVERY_HOURS", "").split(","):
        agency, _, hours = pair.partition("=")
        try:
            if agency.strip() and float(hours) > 0:
                out[agency.strip()] = float(hours)
        except ValueError:
            log.warning("ignoring CHECK_EVERY_HOURS entry %r", pair)
    return out


CHECK_EVERY_HOURS = _check_every_hours()
# This many failed runs in a row flags an agency in the dashboard and emails an admin.
FAILURE_ALERT_THRESHOLD = 3


class EmptyResult(Exception):
    pass


@dataclass
class AgencyResult:
    agency_id: str
    ok: bool
    skipped: bool = False
    error: str | None = None
    new: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    unchanged: int = 0
    baseline: int = 0  # first run: past meetings loaded as already posted
    consecutive_failures: int = 0
    note: str | None = None  # shown in the run summary (e.g. paced crawling status)
    # Failed, but in an expected, self-handling way (CDE challenged a paced
    # request and checks are backing off). Doesn't fail the GitHub job.
    backing_off: bool = False


def sync_agencies(conn: psycopg.Connection) -> None:
    from tracker.scrapers import SCRAPERS

    for a in AGENCIES:
        conn.execute(
            """insert into agencies (id, name, short_name, url, scraper_ready)
               values (%s, %s, %s, %s, %s)
               on conflict (id) do update set name = excluded.name,
                 short_name = excluded.short_name, url = excluded.url,
                 scraper_ready = excluded.scraper_ready""",
            (a.id, a.name, a.short_name, a.url, a.id in SCRAPERS),
        )


def run(conn: psycopg.Connection, fetcher: Fetcher, agency_ids: list[str] | None = None,
        today: dt.date | None = None) -> list[AgencyResult]:
    """Scrape the given agencies (default: all) and commit after each one."""
    today = today or dt.date.today()
    sync_agencies(conn)
    conn.commit()
    results = []
    selected = agency_ids or [a.id for a in AGENCIES]
    for agency_id in selected:
        if agency_id not in BY_ID:
            raise ValueError(f"unknown agency {agency_id!r}")
    from tracker import paced

    paced_ids = [a for a in selected if a in paced.CDE_AGENCIES]
    if paced_ids:
        results.extend(paced.run_cde(conn, fetcher, paced_ids, today=today))
    for agency_id in selected:
        if agency_id in paced.CDE_AGENCIES:
            continue
        scraper = get_scraper(agency_id)
        if scraper is None:
            results.append(AgencyResult(agency_id, ok=False, skipped=True,
                                        error="scraper not built yet"))
            continue
        hours = CHECK_EVERY_HOURS.get(agency_id)
        if hours and conn.execute(
                """select last_checked_at > now() - make_interval(hours => %s)
                   from agencies where id = %s""", (hours, agency_id)).fetchone()[0]:
            results.append(AgencyResult(agency_id, ok=True, skipped=True,
                                        note=f"checked at most every {hours:g} hours (CHECK_EVERY_HOURS)"))
            continue
        results.append(run_agency(conn, fetcher, agency_id, scraper.fetch, today))
    return results


def run_agency(conn: psycopg.Connection, fetcher: Fetcher, agency_id: str,
               scrape, today: dt.date) -> AgencyResult:
    last_success, last_count = conn.execute(
        "select last_success_at, last_posting_count from agencies where id = %s",
        (agency_id,),
    ).fetchone()
    try:
        postings = scrape(fetcher)
        if not postings and last_count:
            raise EmptyResult(
                f"found no postings (previously {last_count}); the page layout may have changed")
        result = AgencyResult(agency_id, ok=True)
        first_run = last_success is None
        seen_keys = set()
        for p in postings:
            if p.external_key in seen_keys:
                continue  # some pages list the same meeting twice
            seen_keys.add(p.external_key)
            baseline = first_run and not (p.meeting_date and p.meeting_date >= today)
            outcome, posting_id = store_posting(conn, fetcher, agency_id, p, today, baseline)
            if outcome == "new" and baseline:
                result.baseline += 1
            elif outcome == "new":
                result.new.append(posting_id)
            elif outcome == "updated":
                result.updated.append(posting_id)
            else:
                result.unchanged += 1
        conn.execute(
            """update agencies set last_checked_at = now(), last_success_at = now(),
                 last_error = null, consecutive_failures = 0, last_posting_count = %s
               where id = %s""",
            (len(seen_keys), agency_id),
        )
        conn.commit()
        log.info("%s: %d new, %d updated, %d unchanged, %d already posted", agency_id,
                 len(result.new), len(result.updated), result.unchanged, result.baseline)
        return result
    except Exception as e:
        conn.rollback()
        log.exception("%s failed", agency_id)
        (failures,) = conn.execute(
            """update agencies set last_checked_at = now(), last_error = %s,
                 consecutive_failures = consecutive_failures + 1
               where id = %s returning consecutive_failures""",
            (f"{type(e).__name__}: {e}"[:2000], agency_id),
        ).fetchone()
        conn.commit()
        return AgencyResult(agency_id, ok=False, error=str(e), consecutive_failures=failures)


def is_active(p: Posting, today: dt.date) -> bool:
    if p.kind != "meeting":
        return False  # opinions and initiatives are documents that don't get revised in place
    return p.meeting_date is None or p.meeting_date >= today - dt.timedelta(days=ACTIVE_WINDOW_DAYS)


def _strip_nul(s: str | None) -> str | None:
    # Postgres text can't hold NUL characters; some PDFs' extracted text contains them.
    return s.replace("\x00", "") if s else s


def _strip_nul_posting(p: Posting) -> None:
    p.title = _strip_nul(p.title)
    for i in p.items:
        i.number, i.title, i.description = (_strip_nul(i.number), _strip_nul(i.title),
                                            _strip_nul(i.description))
    for d in p.documents:
        d.label = _strip_nul(d.label)


def content_hash(p: Posting) -> str:
    payload = {
        "title": p.title,
        "date": p.meeting_date.isoformat() if p.meeting_date else None,
        "items": [[i.number, i.title, i.description] for i in p.items],
        "docs": sorted({d.url for d in p.documents}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def store_posting(conn: psycopg.Connection, fetcher: Fetcher, agency_id: str, p: Posting,
                  today: dt.date, baseline: bool, fetch_docs: bool = True,
                  skipped_doc_note: str | None = None) -> tuple[str, int]:
    """Insert or update one posting. Returns ('new' | 'updated' | 'unchanged', id)."""
    _strip_nul_posting(p)
    row = conn.execute(
        "select id, content_hash from postings where agency_id = %s and external_key = %s",
        (agency_id, p.external_key),
    ).fetchone()
    posting_id, old_hash = row if row else (None, None)
    if p.partial and posting_id is not None:
        conn.execute("update postings set last_seen_at = now() where id = %s", (posting_id,))
        return "unchanged", posting_id

    stored: dict[str, tuple[str | None, str | None]] = {}
    recent: set[str] = set()  # checked within DOC_RECHECK_HOURS
    too_large: set[str] = set()
    if posting_id is not None:
        for url, sha, text, fresh, error in conn.execute(
                """select url, sha256, text,
                          fetched_at > now() - make_interval(hours => %s), fetch_error
                   from documents where posting_id = %s""",
                (DOC_RECHECK_HOURS, posting_id)):
            stored[url] = (sha, text)
            if fresh and sha is not None:
                recent.add(url)
            if error and "larger than" in error:
                too_large.add(url)  # don't download a huge file again every run

    p.documents = p.documents[:MAX_DOCS_PER_POSTING]
    refresh = posting_id is None or is_active(p, today)
    docs: dict[str, FetchedDocument] = {}
    fetched: set[str] = set()  # attempted this run
    revised = False      # a document we already had has new contents
    first_text = False   # a document's contents arrived for the first time
    for d in p.documents:
        if d.url in docs:
            continue
        prev_sha, prev_text = stored.get(d.url, (None, None))
        if d.url in too_large:
            docs[d.url] = FetchedDocument(None, None, "file too large to download for searching")
        elif not fetch_docs:
            docs[d.url] = FetchedDocument(prev_sha, prev_text, None if prev_sha else skipped_doc_note)
        elif prev_sha is None or (refresh and d.url not in recent):
            got = fetch_document(fetcher, d.url)
            got.text = _strip_nul(got.text)
            fetched.add(d.url)
            if got.sha256 is None and prev_sha is not None:
                got = FetchedDocument(prev_sha, prev_text, got.error)  # keep last good copy
            elif got.sha256 == prev_sha and got.text is None:
                got = FetchedDocument(prev_sha, prev_text, got.error)
            revised |= prev_sha is not None and got.sha256 != prev_sha
            first_text |= prev_sha is None and got.sha256 is not None
            docs[d.url] = got
        else:
            docs[d.url] = FetchedDocument(prev_sha, prev_text)

    # The hash covers what the agenda lists (including which documents it links),
    # not document contents, so a document that downloads in a later run (or is
    # deferred by a per-site request limit) isn't mistaken for a change.
    new_hash = content_hash(p)
    changed = posting_id is None or new_hash != old_hash or revised

    if not changed and not first_text:
        conn.execute(
            "update postings set last_seen_at = now(), url = %s where id = %s",
            (p.url, posting_id),
        )
        for url in fetched:
            got = docs[url]
            conn.execute(
                """update documents set fetch_error = %s,
                     fetched_at = case when %s::text is null then now() else fetched_at end
                   where posting_id = %s and url = %s""",
                (got.error, got.error, posting_id, url),
            )
        return "unchanged", posting_id

    if posting_id is None:
        (posting_id,) = conn.execute(
            """insert into postings (agency_id, kind, external_key, title, meeting_date, url,
                 content_hash, is_baseline)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (agency_id, p.kind, p.external_key, p.title, p.meeting_date, p.url,
             new_hash, baseline),
        ).fetchone()
        outcome = "new"
    elif changed:
        conn.execute(
            """update postings set kind = %s, title = %s, meeting_date = %s, url = %s,
                 content_hash = %s, last_changed_at = clock_timestamp(), last_seen_at = now()
               where id = %s""",
            (p.kind, p.title, p.meeting_date, p.url, new_hash, posting_id),
        )
        outcome = "updated"
    else:
        outcome = "unchanged"  # only newly downloaded text; decided after matching below

    if changed:
        conn.execute("delete from items where posting_id = %s", (posting_id,))
        for pos, item in enumerate(p.items):
            conn.execute(
                """insert into items (posting_id, position, number, title, description)
                   values (%s, %s, %s, %s, %s)""",
                (posting_id, pos, item.number, item.title, item.description),
            )
        conn.execute(
            "delete from documents where posting_id = %s and not (url = any(%s))",
            (posting_id, list(docs)),
        )

    labels = {d.url: d for d in reversed(p.documents)}  # first occurrence wins
    for url, got in docs.items():
        if not changed and url not in fetched:
            continue
        link = labels[url]
        conn.execute(
            """insert into documents (posting_id, item_position, url, label, sha256, text,
                 fetched_at, fetch_error)
               values (%s, %s, %s, %s, %s, %s, case when %s then now() end, %s)
               on conflict (posting_id, url) do update set item_position = excluded.item_position,
                 label = excluded.label, sha256 = excluded.sha256, text = excluded.text,
                 fetched_at = coalesce(excluded.fetched_at, documents.fetched_at),
                 fetch_error = excluded.fetch_error""",
            (posting_id, link.item_index, url, link.label, got.sha256, got.text,
             url in fetched, got.error),
        )

    before = _match_keys(conn, posting_id)
    conn.execute("select rematch(%s, null)", ([posting_id],))
    if outcome == "unchanged" and _match_keys(conn, posting_id) - before:
        # A late-arriving document produced a new keyword hit: worth flagging.
        conn.execute(
            "update postings set last_changed_at = clock_timestamp(), last_seen_at = now() where id = %s",
            (posting_id,),
        )
        outcome = "updated"

    if outcome != "unchanged" and not baseline:
        conn.execute(
            "insert into posting_events (posting_id, kind, content_hash) values (%s, %s, %s)",
            (posting_id, outcome, new_hash),
        )
    return outcome, posting_id


def _match_keys(conn: psycopg.Connection, posting_id: int) -> set[tuple]:
    return set(conn.execute(
        """select m.keyword_id, m.source, coalesce(i.position, -1), coalesce(d.url, '')
           from matches m left join items i on i.id = m.item_id
           left join documents d on d.id = m.document_id
           where m.posting_id = %s""",
        (posting_id,),
    ).fetchall())
