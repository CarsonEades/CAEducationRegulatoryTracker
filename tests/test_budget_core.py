"""Pipeline and database behaviour added for budget tracking: programs, empty listings,
scraper warnings, request logging, notes and the hearing calendar feed."""

import datetime as dt
import hashlib
import json

from tests.test_pipeline import TODAY, add_user, mark_succeeded_before, nov_meeting, run_once
from tracker import pipeline
from tracker.http import RequestRecord, request_type
from tracker.models import Item, Posting


def old_content_hash(p: Posting) -> str:
    """content_hash as it was before hearing times and locations were added."""
    payload = {
        "title": p.title,
        "date": p.meeting_date.isoformat() if p.meeting_date else None,
        "items": [[i.number, i.title, i.description] for i in p.items],
        "docs": sorted({d.url for d in p.documents}),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def hearing(**overrides) -> Posting:
    p = Posting(external_key="2027-03-04", title="Subcommittee No. 3 hearing: K-12 Education",
                meeting_date=dt.date(2027, 3, 4), url="https://abgt.assembly.ca.gov/media/9001",
                items=[Item("Charter School Facility Grant Program", number="6100 · Issue 1")],
                start_time=dt.time(9, 30), location="State Capitol, Room 447")
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def test_hash_unchanged_for_postings_without_new_fields():
    p = nov_meeting()
    assert pipeline.content_hash(p) == old_content_hash(p)
    assert pipeline.content_hash(hearing()) != pipeline.content_hash(hearing(start_time=None))
    assert pipeline.content_hash(hearing()) != pipeline.content_hash(hearing(location="Room 1200"))


def test_hearing_time_and_location_stored_and_change_flags_update(conn, fetcher):
    mark_succeeded_before(conn, "abgt_sub3")
    pid = run_once(conn, fetcher, [hearing()], agency="abgt_sub3").new[0]
    assert conn.execute("select start_time, location from postings where id = %s",
                        (pid,)).fetchone() == (dt.time(9, 30), "State Capitol, Room 447")
    r = run_once(conn, fetcher, [hearing(start_time=dt.time(13, 30))], agency="abgt_sub3")
    assert r.updated == [pid]


def test_empty_listing_is_a_warning_for_committee_pages(conn, fetcher):
    conn.execute("""update agencies set last_success_at = now(), last_posting_count = 12
                    where id in ('abgt_sub3', 'sbe')""")
    r = run_once(conn, fetcher, [], agency="abgt_sub3")
    assert r.ok and "no postings listed" in r.note
    row = conn.execute("""select consecutive_failures, last_note, last_posting_count
                          from agencies where id = 'abgt_sub3'""").fetchone()
    assert row[0] == 0 and "between sessions" in row[1] and row[2] == 0
    assert not run_once(conn, fetcher, [], agency="sbe").ok  # education agencies still fail


def test_scraper_warnings_saved_and_cleared(conn, fetcher):
    mark_succeeded_before(conn, "sbud_sub1")
    warnings: list[str] = []

    def scrape(f):
        warnings.append("agenda for May 6 had no items")
        return [hearing()]

    r = pipeline.run_agency(conn, fetcher, "sbud_sub1", scrape, TODAY, warnings=warnings)
    assert r.note == "agenda for May 6 had no items"
    assert conn.execute("select last_note from agencies where id = 'sbud_sub1'").fetchone()[0] \
        == "agenda for May 6 had no items"
    run_once(conn, fetcher, [hearing()], agency="sbud_sub1")
    assert conn.execute("select last_note from agencies where id = 'sbud_sub1'").fetchone()[0] is None


def test_requests_are_logged_per_agency(conn, fetcher):
    mark_succeeded_before(conn, "lao_budget")
    fetcher.log = []
    t0 = dt.datetime(2026, 11, 1, 12, tzinfo=dt.timezone.utc)

    def scrape(f):
        f.log.append(RequestRecord("https://www.lao.ca.gov/Budget", "www.lao.ca.gov", t0,
                                   "page", "ok", 200))
        f.log.append(RequestRecord("https://www.lao.ca.gov/x.pdf", "www.lao.ca.gov",
                                   t0 + dt.timedelta(seconds=4), "document", "error", 403,
                                   "HTTP 403 for https://www.lao.ca.gov/x.pdf"))
        return [hearing(kind="publication", start_time=None, location=None)]

    pipeline.run_agency(conn, fetcher, "lao_budget", scrape, TODAY)
    rows = conn.execute("""select request_type, outcome, status, gap_seconds, agency_id
                           from crawl_log order by requested_at""").fetchall()
    assert rows == [("page", "ok", 200, None, "lao_budget"),
                    ("document", "error", 403, 4, "lao_budget")]


def test_request_type():
    assert request_type("https://abgt.assembly.ca.gov/media/8102", "application/pdf") == "document"
    assert request_type("https://abgt.assembly.ca.gov/media/8102") == "document"
    assert request_type("https://ebudget.ca.gov/api/home/getLink", "application/json") == "api"
    assert request_type("https://trailerbill.dof.ca.gov/public/pdf/1408") == "document"
    assert request_type("https://trailerbill.dof.ca.gov/public/getAppInfo") == "api"
    assert request_type("https://www.lao.ca.gov/Budget", "text/html; charset=utf-8") == "page"


def test_budget_keywords_only_match_budget_postings(conn, fetcher):
    mark_succeeded_before(conn, "abgt_sub3")
    mark_succeeded_before(conn, "sbe")
    budget_id = run_once(conn, fetcher, [hearing(title="Proposition 98 overview")],
                         agency="abgt_sub3").new[0]
    edu_id = run_once(conn, fetcher, [nov_meeting(title="Proposition 98 overview")]).new[0]
    terms = lambda pid: {r[0] for r in conn.execute(  # noqa: E731
        "select k.term from matches m join keywords k on k.id = m.keyword_id where m.posting_id = %s",
        (pid,))}
    assert "Proposition 98" in terms(budget_id)
    assert "Proposition 98" not in terms(edu_id)
    assert "charter" in terms(budget_id)  # program-wide keywords apply to both
    row = conn.execute("select program, group_name from posting_status where id = %s",
                       (budget_id,)).fetchone()
    assert row == ("budget", "Assembly Budget")


def test_notes_and_calendar_flags_in_status_view(conn, fetcher):
    mark_succeeded_before(conn, "abgt_sub3")
    pid = run_once(conn, fetcher, [hearing()], agency="abgt_sub3").new[0]
    uid = add_user(conn)
    conn.execute("insert into posting_notes (posting_id, user_id, body) values (%s, %s, 'Issue 1 is ours')",
                 (pid, uid))
    conn.execute("insert into calendar_hearings (posting_id, added_by) values (%s, %s)", (pid, uid))
    assert conn.execute("select note_count, on_calendar, status from posting_status where id = %s",
                        (pid,)).fetchone() == (1, True, "new")  # a note isn't a review


def test_calendar_feed(conn, fetcher):
    mark_succeeded_before(conn, "abgt_sub3")
    today = dt.date.today()
    timed = run_once(conn, fetcher, [hearing(meeting_date=today + dt.timedelta(days=10))],
                     agency="abgt_sub3").new[0]
    allday = run_once(conn, fetcher, [hearing(external_key="b", start_time=None,
                                              meeting_date=today + dt.timedelta(days=20),
                                              title="Hearing; with, punctuation")],
                      agency="abgt_sub3").new[0]
    run_once(conn, fetcher, [hearing(external_key="c", meeting_date=today + dt.timedelta(days=5))],
             agency="abgt_sub3")  # not on the calendar
    uid = add_user(conn)
    for pid in (timed, allday):
        conn.execute("insert into calendar_hearings (posting_id) values (%s)", (pid,))
    token = conn.execute("insert into calendar_feeds (user_id) values (%s) returning token",
                         (uid,)).fetchone()[0]
    assert len(token) == 64

    ics = conn.execute("select calendar_ics(%s, 'https://example.org/tracker/')",
                       (token,)).fetchone()[0]
    assert ics.startswith("BEGIN:VCALENDAR\r\n") and ics.endswith("END:VCALENDAR\r\n")
    assert all(len(line.encode()) <= 75 for line in ics.split("\r\n"))
    unfolded = ics.replace("\r\n ", "")
    assert unfolded.count("BEGIN:VEVENT") == 2
    assert f"UID:posting-{timed}@ca-budget-tracker" in unfolded
    start = (dt.datetime.combine(today + dt.timedelta(days=10), dt.time(9, 30))
             .replace(tzinfo=__import__("zoneinfo").ZoneInfo("America/Los_Angeles"))
             .astimezone(dt.timezone.utc))
    assert f"DTSTART:{start:%Y%m%dT%H%M%SZ}" in unfolded
    assert f"DTSTART;VALUE=DATE:{today + dt.timedelta(days=20):%Y%m%d}" in unfolded
    assert "SUMMARY:Hearing\\; with\\, punctuation" in unfolded
    assert "LOCATION:State Capitol\\, Room 447" in unfolded
    assert "6100 · Issue 1 Charter School Facility Grant Program" in unfolded
    assert f"Tracker: https://example.org/tracker/#/posting/{timed}" in unfolded

    assert conn.execute("select calendar_ics('nope')").fetchone()[0] is None
