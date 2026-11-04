"""Paced checking of cde.ca.gov (State Board of Education, the Advisory
Commission on Charter Schools, and the California Practitioners Advisory Group).

cde.ca.gov's bot protection lets an occasional request through but challenges
several in a row. So each run reads at most a few pages (one by default), taken
from a small queue:

  1. an agency's meeting list that has never been read,
  2. an agenda page that has never been read (newly posted meetings),
  3. a meeting list not read in LISTING_REFRESH_HOURS,
  4. an upcoming meeting's agenda not re-read in AGENDA_RECHECK_HOURS (revisions),
  5. an item document not downloaded yet (see next_document),
  6. otherwise the meeting list read longest ago.

Agenda pages list every item's subject, which keyword matching uses right
away. Item documents are downloaded one per spare slot: upcoming meetings
first, each item's main document before attachments and slides. Once a
document's text is stored it's searched like any other agency's; a new
keyword hit on a recent or upcoming meeting flags it as updated.

Every request is logged in crawl_log with the time since the previous one and
whether it was challenged, and acceptance by spacing is reported each run, so
the pacing can be tuned with these environment variables (set as repository
variables in GitHub):

  CDE_MIN_GAP_MINUTES   minimum time between runs that contact CDE (default 100)
  CDE_PAGES_PER_RUN     pages read per run (default 1)
  CDE_PAGE_GAP_SECONDS  wait between pages within one run (default 90)

When a request is challenged, CDE is left alone for 2 hours, doubling with each
challenge in a row (up to 24 hours)."""

from __future__ import annotations

import datetime as dt
import logging
import os
import time
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from tracker.http import BlockedError, Fetcher, FetchError
from tracker.models import Posting
from tracker.scrapers.cde import ACCS, CPAG, SBE, parse_agenda_page

log = logging.getLogger(__name__)

HOST = "www.cde.ca.gov"
CDE_AGENCIES = {"sbe": SBE, "accs": ACCS, "cpag": CPAG}
STATE_KEY = "cde"
RECENT_MEETINGS = 2           # agenda pages tracked per agency
LISTING_REFRESH_HOURS = 8
AGENDA_RECHECK_HOURS = 20
BACKOFF_START = dt.timedelta(hours=2)
BACKOFF_MAX = dt.timedelta(hours=24)
DOCS_NOTE = "not downloaded yet: cde.ca.gov limits automated requests, so documents are fetched one at a time"
# Documents are downloaded for upcoming meetings and those within this many days.
DOC_WINDOW_DAYS = 60
# A late document's new keyword hit flags meetings this recent (or upcoming) as
# updated; older ones just become searchable, without an alert.
DOC_ALERT_DAYS = 14


def _setting(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


@dataclass
class Task:
    agency_id: str
    kind: str  # "listing" | "agenda" | "document"
    url: str
    meeting: list | None = None  # [key, url, label, iso date] for agendas
    doc: tuple | None = None     # (document id, posting id, label, meeting date, posting title)


def _parse_time(s: str | None) -> dt.datetime | None:
    return dt.datetime.fromisoformat(s) if s else None


def _older_than(stamp: str | None, now: dt.datetime, hours: float) -> bool:
    t = _parse_time(stamp)
    return t is None or now - t >= dt.timedelta(hours=hours)


def choose_task(state: dict, agency_ids: list[str], today: dt.date, now: dt.datetime,
                document: Task | None = None) -> Task:
    listings = state.setdefault("listings", {})
    agendas = state.setdefault("agendas", {})

    def listing(a):
        return Task(a, "listing", CDE_AGENCIES[a].listing_url)

    for a in agency_ids:
        if a not in listings:
            return listing(a)
    tracked = [(a, m) for a in agency_ids for m in listings[a]["meetings"][:RECENT_MEETINGS]]
    for a, m in tracked:
        if m[1] not in agendas:
            return Task(a, "agenda", m[1], m)
    stale = [a for a in agency_ids if _older_than(listings[a]["fetched_at"], now, LISTING_REFRESH_HOURS)]
    if stale:
        return listing(min(stale, key=lambda a: listings[a]["fetched_at"]))
    for a, m in tracked:
        upcoming = m[3] is None or dt.date.fromisoformat(m[3]) >= today - dt.timedelta(days=1)
        if upcoming and _older_than(agendas[m[1]]["fetched_at"], now, AGENDA_RECHECK_HOURS):
            return Task(a, "agenda", m[1], m)
    if document is not None:
        return document
    return listing(min(agency_ids, key=lambda a: listings[a]["fetched_at"]))


SEARCHABLE = r"\.(docx|pdf|pptx)$"


def next_document(conn, agency_ids: list[str], today: dt.date) -> Task | None:
    """The most useful CDE item document not downloaded yet."""
    row = conn.execute(
        """select d.id, d.url, d.label, p.agency_id, p.id, p.meeting_date, p.title
           from documents d join postings p on p.id = d.posting_id
           where p.agency_id = any(%(agencies)s)
             and d.sha256 is null
             and (d.fetch_error is null or d.fetch_error like 'not downloaded%%')
             and lower(split_part(d.url, '?', 1)) ~ %(searchable)s
             and (p.meeting_date is null or p.meeting_date >= %(today)s::date - %(window)s)
           order by (p.meeting_date is null or p.meeting_date >= %(today)s::date) desc,
                    (coalesce(d.label, '') ~* '(attachment|handout|slides|appendix|exhibit)'),
                    abs(p.meeting_date - %(today)s::date) nulls first, d.id
           limit 1""",
        {"agencies": agency_ids, "searchable": SEARCHABLE, "today": today, "window": DOC_WINDOW_DAYS},
    ).fetchone()
    if not row:
        return None
    doc_id, url, label, agency_id, posting_id, mdate, title = row
    return Task(agency_id, "document", url, doc=(doc_id, posting_id, label, mdate, title))


def load_state(conn) -> dict:
    row = conn.execute("select value from crawl_state where key = %s", (STATE_KEY,)).fetchone()
    return dict(row[0]) if row else {}


def save_state(conn, state: dict) -> None:
    conn.execute(
        """insert into crawl_state (key, value, updated_at) values (%s, %s, now())
           on conflict (key) do update set value = excluded.value, updated_at = now()""",
        (STATE_KEY, Jsonb(state)),
    )


def origin() -> str:
    """Where this run is happening: 'github', 'office', or 'local' (set by the workflow)."""
    return os.environ.get("TRACKER_ORIGIN", "").strip() or "local"


ORIGIN_NAMES = {"github": "GitHub's servers", "office": "the office computer", "local": "a local run"}


def _log_request(conn, url: str, now: dt.datetime, outcome: str, detail: str | None) -> int | None:
    # Spacing is measured from the previous request from anywhere: CDE sees
    # them all, and both runners share one pacing queue.
    prev = conn.execute(
        "select max(requested_at) from crawl_log where host = %s", (HOST,)).fetchone()[0]
    gap = int((now - prev).total_seconds()) if prev else None
    conn.execute(
        """insert into crawl_log (host, url, requested_at, gap_seconds, outcome, detail, origin)
           values (%s, %s, %s, %s, %s, %s, %s)""",
        (HOST, url, now, gap, outcome, detail, origin()),
    )
    return gap


def _mark_success(conn, agency_id: str, count: int | None = None) -> None:
    conn.execute(
        """update agencies set last_checked_at = now(), last_success_at = now(), last_error = null,
             consecutive_failures = 0,
             last_posting_count = coalesce(%s, last_posting_count)
           where id = %s""",
        (count, agency_id),
    )


def _mark_failure(conn, agency_id: str, error: str) -> int:
    return conn.execute(
        """update agencies set last_checked_at = now(), last_error = %s,
             consecutive_failures = consecutive_failures + 1
           where id = %s returning consecutive_failures""",
        (error[:2000], agency_id),
    ).fetchone()[0]


def run_cde(conn: psycopg.Connection, fetcher: Fetcher, agency_ids: list[str],
            today: dt.date | None = None, now=None, sleep=time.sleep) -> list:
    from tracker.pipeline import AgencyResult, store_posting

    clock = now or (lambda: dt.datetime.now(dt.timezone.utc))
    today = today or dt.date.today()
    results = {a: AgencyResult(a, ok=True, skipped=True, note="waiting its turn (CDE is read one page at a time)")
               for a in agency_ids}
    state = load_state(conn)
    t = clock()

    waiting_until = _parse_time(state.get("next_allowed_at"))
    if waiting_until and t < waiting_until:
        why = ("backing off after CDE challenged a request" if state.get("challenges")
               else "spacing requests to CDE")
        for r in results.values():
            r.note = f"{why}; next CDE request after {waiting_until:%b %d %H:%M} UTC"
        return list(results.values())

    pages = max(1, int(_setting("CDE_PAGES_PER_RUN", 1)))
    page_gap = _setting("CDE_PAGE_GAP_SECONDS", 90)
    min_gap = dt.timedelta(minutes=_setting("CDE_MIN_GAP_MINUTES", 100))

    for i in range(pages):
        if i:
            sleep(page_gap)
        t = clock()
        task = choose_task(state, agency_ids, today, t, next_document(conn, agency_ids, today))
        r = results[task.agency_id]
        try:
            resp = fetcher.get(task.url)
        except BlockedError as e:
            gap = _log_request(conn, task.url, t, "challenged", str(e)[:500])
            state["challenges"] = state.get("challenges", 0) + 1
            backoff = min(BACKOFF_START * 2 ** (state["challenges"] - 1), BACKOFF_MAX)
            state["next_allowed_at"] = (t + backoff).isoformat()
            failures = _mark_failure(conn, task.agency_id, f"CDE challenged the request; retrying after {backoff}")
            results[task.agency_id] = AgencyResult(
                task.agency_id, ok=False, error=f"challenged by CDE bot protection ({_gap_text(gap)}); backing off {backoff}",
                consecutive_failures=failures, backing_off=True)
            break
        except FetchError as e:
            _log_request(conn, task.url, t, "error", str(e)[:500])
            state["next_allowed_at"] = (t + min_gap).isoformat()
            # Don't pick the same broken page or file again on every run.
            if task.kind == "agenda":
                state["agendas"][task.url] = {"fetched_at": t.isoformat(), "error": str(e)[:300]}
            if task.kind == "document":
                conn.execute("update documents set fetch_error = %s, fetched_at = now() where id = %s",
                             (str(e)[:500], task.doc[0]))
                r.skipped = False
                r.note = f"couldn't download {task.doc[2] or 'a document'}: {e}"
            else:
                failures = _mark_failure(conn, task.agency_id, str(e))
                results[task.agency_id] = AgencyResult(task.agency_id, ok=False, error=str(e),
                                                        consecutive_failures=failures)
            save_state(conn, state)
            conn.commit()
            break

        gap = _log_request(conn, task.url, t, "ok", None)
        state["challenges"] = 0
        state["next_allowed_at"] = (t + min_gap).isoformat()
        stamp = t.isoformat()
        scraper = CDE_AGENCIES[task.agency_id]()
        r.skipped = False
        if task.kind == "document":
            r.note, flagged = _store_document(conn, task, resp, today)
            r.note += f" ({_gap_text(gap)})"
            if flagged:
                r.updated.append(task.doc[1])
            else:
                r.unchanged += 1
            _mark_success(conn, task.agency_id)
        elif task.kind == "listing":
            meetings = [[k, u, label, d.isoformat() if d else None]
                        for k, u, label, d in scraper.parse_listing(resp.content)]
            first = task.agency_id not in state["listings"]
            state["listings"][task.agency_id] = {"fetched_at": stamp, "meetings": meetings}
            if first:
                # Meetings already listed the first time are loaded as "already posted",
                # except upcoming ones.
                state.setdefault("initial", {})[task.agency_id] = [m[0] for m in meetings]
            _mark_success(conn, task.agency_id, len(meetings))
            r.note = f"read the meeting list ({len(meetings)} meetings; {_gap_text(gap)})"
        else:
            key, url, label, iso = task.meeting
            date = dt.date.fromisoformat(iso) if iso else None
            items, docs = parse_agenda_page(resp.content, url)
            posting = Posting(external_key=key, title=f"{scraper.title_prefix} - {label}",
                              meeting_date=date, url=url, items=items, documents=docs)
            initial = state.get("initial", {}).get(task.agency_id, [])
            baseline = key in initial and not (date and date >= today)
            outcome, pid = store_posting(conn, fetcher, task.agency_id, posting, today, baseline,
                                         fetch_docs=False, skipped_doc_note=DOCS_NOTE)
            state["agendas"][url] = {"fetched_at": stamp}
            _mark_success(conn, task.agency_id)
            if outcome == "new" and baseline:
                r.baseline += 1
            elif outcome == "new":
                r.new.append(pid)
            elif outcome == "updated":
                r.updated.append(pid)
            else:
                r.unchanged += 1
            r.note = f"read the {label} agenda ({len(items)} items; {_gap_text(gap)})"
        save_state(conn, state)
        conn.commit()

    save_state(conn, state)
    conn.commit()
    return list(results.values())


def _store_document(conn, task: Task, resp, today: dt.date) -> tuple[str, bool]:
    """Save a downloaded document's text and re-run keyword matching.
    Returns (note, whether the meeting was flagged as updated)."""
    from tracker.documents import document_from_response
    from tracker.pipeline import _match_keys, _strip_nul

    doc_id, posting_id, label, mdate, title = task.doc
    got = document_from_response(resp, task.url)
    conn.execute(
        """update documents set sha256 = %s, text = %s, fetched_at = now(), fetch_error = %s
           where id = %s""",
        (got.sha256, _strip_nul(got.text), got.error, doc_id),
    )
    before = _match_keys(conn, posting_id)
    conn.execute("select rematch(%s, null)", ([posting_id],))
    new_hits = _match_keys(conn, posting_id) - before
    recent = mdate is None or mdate >= today - dt.timedelta(days=DOC_ALERT_DAYS)
    flagged = bool(new_hits) and recent
    if flagged:
        conn.execute("update postings set last_changed_at = clock_timestamp() where id = %s", (posting_id,))
        conn.execute(
            """insert into posting_events (posting_id, kind, content_hash)
               select id, 'updated', content_hash from postings where id = %s""",
            (posting_id,),
        )
    what = f"downloaded {label or 'a document'} for {title}"
    if got.text is None:
        what += f" (no searchable text: {got.error})"
    elif new_hits:
        what += f"; {len(new_hits)} new keyword hit(s)"
    return what, flagged


def _gap_text(gap: int | None) -> str:
    """'90s after the previous CDE request, from GitHub's servers'."""
    where = ORIGIN_NAMES.get(origin(), origin())
    if gap is None:
        return f"the first logged CDE request, from {where}"
    return f"{_duration(gap)} after the previous CDE request, from {where}"


def _duration(gap: int) -> str:
    if gap < 120:
        return f"{gap}s"
    if gap < 7200:
        return f"{gap // 60} min"
    return f"{gap / 3600:.1f} h"


GAP_BUCKETS = [(0, 60, "under 1 min"), (60, 600, "1-10 min"), (600, 3600, "10-60 min"),
               (3600, 3 * 3600, "1-3 h"), (3 * 3600, 12 * 3600, "3-12 h"), (12 * 3600, None, "12 h+")]


def acceptance_by_gap(conn, days: int = 14) -> list[tuple[str, str, int, int]]:
    """(origin, spacing bucket, accepted, challenged) for recent CDE requests."""
    rows = conn.execute(
        """select coalesce(origin, 'office'), gap_seconds, outcome from crawl_log
           where host = %s and requested_at > now() - make_interval(days => %s)
             and outcome in ('ok', 'challenged') and gap_seconds is not null""",
        (HOST, days),
    ).fetchall()
    out = []
    for where in sorted({r[0] for r in rows}):
        for lo, hi, label in GAP_BUCKETS:
            sel = [o for w, g, o in rows if w == where and g >= lo and (hi is None or g < hi)]
            if sel:
                out.append((ORIGIN_NAMES.get(where, where), label, sel.count("ok"), sel.count("challenged")))
    return out
