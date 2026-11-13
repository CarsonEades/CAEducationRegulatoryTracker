"""Scrape agencies, store what changed, and run keyword matching."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field

import psycopg

from tracker.agenda import split_agenda
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
# Request log entries older than this are deleted at the start of each run.
CRAWL_LOG_DAYS = 120


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
            """insert into agencies (id, name, short_name, url, scraper_ready, program, group_name,
                 may_be_empty)
               values (%s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (id) do update set name = excluded.name,
                 short_name = excluded.short_name, url = excluded.url,
                 scraper_ready = excluded.scraper_ready, program = excluded.program,
                 group_name = excluded.group_name, may_be_empty = excluded.may_be_empty""",
            (a.id, a.name, a.short_name, a.url, a.id in SCRAPERS, a.program, a.group,
             a.may_be_empty),
        )


def run(conn: psycopg.Connection, fetcher: Fetcher, agency_ids: list[str] | None = None,
        today: dt.date | None = None) -> list[AgencyResult]:
    """Scrape the given agencies (default: all) and commit after each one."""
    today = today or dt.date.today()
    sync_agencies(conn)
    conn.execute("delete from crawl_log where requested_at < now() - make_interval(days => %s)",
                 (CRAWL_LOG_DAYS,))
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
        results.append(run_agency(conn, fetcher, agency_id, scraper.fetch, today,
                                  warnings=getattr(scraper, "warnings", None)))
    return results


def run_agency(conn: psycopg.Connection, fetcher: Fetcher, agency_id: str,
               scrape, today: dt.date, warnings: list[str] | None = None) -> AgencyResult:
    """Scrape one agency and store its postings. `warnings` is a list the scraper appends
    to while it runs (Scraper.warnings); they're shown on the health page."""
    last_success, last_count = conn.execute(
        "select last_success_at, last_posting_count from agencies where id = %s",
        (agency_id,),
    ).fetchone()
    log_start = len(getattr(fetcher, "log", []))
    warnings = warnings if warnings is not None else []
    try:
        postings = scrape(fetcher)
        if not postings and last_count:
            agency = BY_ID.get(agency_id)
            if agency and agency.may_be_empty:
                warnings.append(f"no postings listed (previously {last_count}); normal between "
                                "sessions, otherwise check the page")
            else:
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
            outcome, posting_id = store_posting(conn, fetcher, agency_id, p, today, baseline,
                                                warnings=warnings)
            if outcome == "new" and baseline:
                result.baseline += 1
            elif outcome == "new":
                result.new.append(posting_id)
            elif outcome == "updated":
                result.updated.append(posting_id)
            else:
                result.unchanged += 1
        note = "; ".join(dict.fromkeys(warnings))[:2000] or None
        result.note = note
        conn.execute(
            """update agencies set last_checked_at = now(), last_success_at = now(),
                 last_error = null, consecutive_failures = 0, last_posting_count = %s,
                 last_note = %s
               where id = %s""",
            (len(seen_keys), note, agency_id),
        )
        log_requests(conn, fetcher, agency_id, log_start)
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
        log_requests(conn, fetcher, agency_id, log_start)
        conn.commit()
        return AgencyResult(agency_id, ok=False, error=str(e), consecutive_failures=failures)


def log_requests(conn: psycopg.Connection, fetcher: Fetcher, agency_id: str, start: int) -> None:
    """Write this agency's requests (fetcher.log[start:]) to crawl_log, with the gap since
    the previous request to the same host from any run."""
    records = getattr(fetcher, "log", [])[start:]
    last: dict[str, dt.datetime | None] = {}
    for r in records:
        if r.host not in last:
            last[r.host] = conn.execute(
                "select max(requested_at) from crawl_log where host = %s and requested_at < %s",
                (r.host, r.requested_at)).fetchone()[0]
        prev = last[r.host]
        gap = int((r.requested_at - prev).total_seconds()) if prev else None
        conn.execute(
            """insert into crawl_log (host, url, requested_at, gap_seconds, outcome, detail,
                 origin, agency_id, request_type, status)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (r.host, r.url[:2000], r.requested_at, gap, r.outcome, r.detail,
             os.environ.get("TRACKER_ORIGIN", "local"), agency_id, r.request_type, r.status),
        )
        last[r.host] = r.requested_at


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
    # Added for budget hearings. Only hashed when set, so postings stored before these
    # fields existed keep their hash and aren't flagged as changed.
    if p.start_time is not None:
        payload["time"] = p.start_time.isoformat()
    if p.location:
        payload["location"] = p.location
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def store_posting(conn: psycopg.Connection, fetcher: Fetcher, agency_id: str, p: Posting,
                  today: dt.date, baseline: bool, fetch_docs: bool = True,
                  skipped_doc_note: str | None = None,
                  warnings: list[str] | None = None) -> tuple[str, int]:
    """Insert or update one posting. Returns ('new' | 'updated' | 'unchanged', id)."""
    _strip_nul_posting(p)
    row = conn.execute(
        """select id, content_hash, is_baseline,
                  exists (select 1 from items i where i.posting_id = postings.id),
                  exists (select 1 from documents d where d.posting_id = postings.id
                          and d.sha256 is null and d.fetch_error like '%%limit for%%')
           from postings where agency_id = %s and external_key = %s""",
        (agency_id, p.external_key),
    ).fetchone()
    posting_id, old_hash, was_baseline, had_items, deferred_docs = \
        row if row else (None, None, False, False, False)
    # A listed posting whose details weren't re-read is left as stored, unless some of its
    # documents are still waiting for a download that a per-site limit deferred.
    if p.partial and posting_id is not None and not deferred_docs:
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
    # Re-download documents for new and recent meetings, and for a publication whose listing
    # changed (e.g. DOF posting a revised trailer bill under the same address).
    refresh = (posting_id is None or is_active(p, today)
               or (p.kind == "publication" and content_hash(p) != old_hash))
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

    # Budget hearing agendas are PDFs: their items come from the agenda's text (downloaded
    # above, or the stored copy), so a revised agenda shows which items changed.
    agendas = [d for d in p.documents if d.agenda]
    late_items = False  # items read from an agenda whose text arrived for the first time
    listing_hash = content_hash(p)  # what the scraper listed, before any items from agendas
    if agendas and not p.items:
        parts = [d for d in dict((d.url, d) for d in agendas).values()]
        for n, d in enumerate(parts, 1):
            text = docs[d.url].text
            if not text:
                continue
            found = split_agenda(text)
            if not found and warnings is not None:
                warnings.append(f"no agenda items recognized in {d.label or d.url} "
                                f"({p.meeting_date or p.title}); its text is still searched")
            for item in found:
                if len(parts) > 1:  # e.g. Part A and Part B, whose issue numbers overlap
                    item.number = f"Part {n} · {item.number}" if item.number else f"Part {n}"
                p.items.append(item)
            if found and stored.get(d.url, (None, None))[0] is None:
                late_items = True

    # The hash covers what the agenda lists (including which documents it links),
    # not document contents, so a document that downloads in a later run (or is
    # deferred by a per-site request limit) isn't mistaken for a change.
    new_hash = content_hash(p)
    # Items that only appear because an agenda finally downloaded (e.g. it was deferred by a
    # per-site document limit) aren't a change to the agenda; store them quietly.
    quiet_items = (posting_id is not None and late_items and not had_items
                   and listing_hash == old_hash and not revised)
    changed = posting_id is None or (new_hash != old_hash and not quiet_items) or revised

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
                 content_hash, is_baseline, start_time, location)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (agency_id, p.kind, p.external_key, p.title, p.meeting_date, p.url,
             new_hash, baseline, p.start_time, p.location),
        ).fetchone()
        outcome = "new"
    elif changed:
        conn.execute(
            """update postings set kind = %s, title = %s, meeting_date = %s, url = %s,
                 content_hash = %s, last_changed_at = clock_timestamp(), last_seen_at = now(),
                 start_time = %s, location = %s
               where id = %s""",
            (p.kind, p.title, p.meeting_date, p.url, new_hash, p.start_time, p.location,
             posting_id),
        )
        outcome = "updated"
    else:
        outcome = "unchanged"  # only newly downloaded text; decided after matching below

    if quiet_items:
        conn.execute("update postings set content_hash = %s, last_seen_at = now() where id = %s",
                      (new_hash, posting_id))
    if changed or quiet_items:
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
    new_hit = outcome == "unchanged" and bool(_match_keys(conn, posting_id) - before)
    if new_hit and (not was_baseline or is_active(p, today)):
        # A late-arriving document produced a new keyword hit: worth flagging. (Not for
        # older postings loaded quietly on a source's first run, whose documents can arrive
        # over several runs.)
        conn.execute(
            "update postings set last_changed_at = clock_timestamp(), last_seen_at = now() where id = %s",
            (posting_id,),
        )
        outcome = "updated"
    elif quiet_items or new_hit:
        # Not a change, but keep the latest history snapshot complete (items and matches), so
        # a later revision is compared with what the agenda really said.
        conn.execute(
            """insert into posting_versions (posting_id, changed_at, snapshot)
               select id, last_changed_at, posting_snapshot(id) from postings where id = %s
               on conflict (posting_id, changed_at) do update set snapshot = excluded.snapshot""",
            (posting_id,),
        )

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
