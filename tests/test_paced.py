import datetime as dt
from pathlib import Path

import pytest

from tests.conftest import FakeFetcher
from tracker import paced
from tracker.http import BlockedError

FIX = Path(__file__).parent / "fixtures"
SBE_LIST = "https://www.cde.ca.gov/be/ag/ag/index.asp"
SBE_SEP = "https://www.cde.ca.gov/be/ag/ag/yr26/agenda202609.asp"
SBE_JUL = "https://www.cde.ca.gov/be/ag/ag/yr26/agenda202607.asp"
ACCS_LIST = "https://www.cde.ca.gov/be/cc/cs/accsagendas.asp"
ACCS_OCT = "https://www.cde.ca.gov/be/cc/cs/accsnotice100626.asp"


class Clock:
    def __init__(self):
        self.t = dt.datetime(2026, 9, 1, 15, 0, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += dt.timedelta(**kw)


@pytest.fixture
def cde_fetcher():
    page = lambda p: ((FIX / p).read_bytes(), "text/html")
    return FakeFetcher({SBE_LIST: page("sbe/index.html"), SBE_SEP: page("sbe/agenda202609.html"),
                        SBE_JUL: page("sbe/agenda202609.html"),
                        ACCS_LIST: page("accs/accsagendas.html"), ACCS_OCT: page("accs/accsnotice100626.html")})


def run(conn, fetcher, clock, agencies=("sbe", "accs"), today=dt.date(2026, 9, 1)):
    results = paced.run_cde(conn, fetcher, list(agencies), today=today, now=clock, sleep=lambda s: None)
    return {r.agency_id: r for r in results}


def test_one_page_per_run_in_queue_order(conn, cde_fetcher):
    clock = Clock()
    order = []
    for _ in range(5):
        run(conn, cde_fetcher, clock)
        order.append(cde_fetcher.requests[-1])
        clock.advance(hours=2)
    # Lists first, then the newly listed agendas; one request per run.
    assert order == [SBE_LIST, ACCS_LIST, SBE_SEP, SBE_JUL, ACCS_OCT]
    assert len(cde_fetcher.requests) == 5
    rows = conn.execute("select outcome, gap_seconds from crawl_log order by id").fetchall()
    assert [o for o, _ in rows] == ["ok"] * 5
    assert [g for _, g in rows] == [None, 7200, 7200, 7200, 7200]


def test_agenda_items_stored_without_downloading_documents(conn, cde_fetcher):
    clock = Clock()
    for _ in range(3):
        results = run(conn, cde_fetcher, clock)
        clock.advance(hours=2)
    # The September 9 meeting is upcoming relative to "today", so it's new, not baseline.
    assert len(results["sbe"].new) == 1
    pid = results["sbe"].new[0]
    assert conn.execute("select count(*) from items where posting_id = %s", (pid,)).fetchone()[0] == 29
    docs = conn.execute("select count(*), count(text), min(fetch_error) from documents where posting_id = %s",
                        (pid,)).fetchone()
    assert docs[0] == 42 and docs[1] == 0 and docs[2].startswith("not downloaded")
    assert not any(u.endswith((".docx", ".pdf")) for u in cde_fetcher.requests)
    # Item subjects are matched against keywords.
    hits = conn.execute("select count(*) from matches where posting_id = %s and source = 'item'",
                        (pid,)).fetchone()[0]
    assert hits >= 1


def test_spare_slots_download_documents_nearest_meeting_main_docs_first(conn, cde_fetcher, monkeypatch):
    monkeypatch.setattr(paced, "LISTING_REFRESH_HOURS", {"sbe": 1000, "accs": 1000})
    item01 = "https://www.cde.ca.gov/be/ag/ag/yr26/documents/sep26item01.docx"
    cde_fetcher.pages[item01] = ("The board will hear a charter petition appeal.", "text/plain")
    clock = Clock()
    for _ in range(6):  # 2 lists, 4 agendas (the June ACCS page is missing: recorded, not retried)
        run(conn, cde_fetcher, clock)
        clock.advance(hours=2)
    assert cde_fetcher.requests[-1].endswith("accsnotice060226.asp")
    results = run(conn, cde_fetcher, clock)
    # Nearest upcoming meeting (Sep 9 SBE), its first main item document.
    assert cde_fetcher.requests[-1] == item01
    r = results["sbe"]
    assert r.note.startswith("downloaded Item 01 (DOCX) for State Board of Education Meeting - September 9, 2026")
    assert len(r.updated) == 1  # new keyword hit on an upcoming meeting
    text, err = conn.execute("select text, fetch_error from documents where url = %s and posting_id = %s",
                             (item01, r.updated[0])).fetchone()
    assert "charter petition" in text and err is None
    clock.advance(hours=2)
    run(conn, cde_fetcher, clock)  # next one isn't served: error recorded, moves on afterwards
    failed = cde_fetcher.requests[-1]
    clock.advance(hours=2)
    run(conn, cde_fetcher, clock)
    assert cde_fetcher.requests[-1] != failed
    assert not any("attachment" in u.lower() or u.endswith("a1.docx") for u in cde_fetcher.requests[-3:])


def test_extra_pages_stop_when_nothing_is_due(conn, cde_fetcher, monkeypatch):
    monkeypatch.setattr(paced, "LISTING_REFRESH_HOURS", {"sbe": 1000, "accs": 1000})
    monkeypatch.setattr(paced, "next_document", lambda *a: None)  # no documents left
    clock = Clock()
    for _ in range(6):  # lists and agendas
        run(conn, cde_fetcher, clock)
        clock.advance(hours=2)
    before = len(cde_fetcher.requests)
    monkeypatch.setenv("CDE_PAGES_PER_RUN", "10")
    waits = []
    paced.run_cde(conn, cde_fetcher, ["sbe", "accs"], today=dt.date(2026, 9, 1), now=clock, sleep=waits.append)
    # One routine list re-read, then nothing else worth a request.
    assert len(cde_fetcher.requests) - before == 1 and waits == []


def test_request_cap_stops_run_without_losing_documents(conn, cde_fetcher, monkeypatch):
    from tracker.http import BudgetExceeded

    monkeypatch.setattr(paced, "LISTING_REFRESH_HOURS", {"sbe": 1000, "accs": 1000})
    clock = Clock()
    for _ in range(6):  # lists and agendas
        run(conn, cde_fetcher, clock)
        clock.advance(hours=2)
    real_get = cde_fetcher.get
    cde_fetcher.get = lambda url: (_ for _ in ()).throw(BudgetExceeded("request limit reached"))
    run(conn, cde_fetcher, clock)
    cde_fetcher.get = real_get
    # Nothing was marked as failed: the documents are still queued for the next run.
    assert conn.execute("""select count(*) from documents where fetch_error like 'request limit%'""").fetchone()[0] == 0
    assert paced.next_document(conn, ["sbe", "accs"], dt.date(2026, 9, 1)) is not None


def test_cde_request_cap_follows_pages_setting(monkeypatch):
    from tracker import http

    monkeypatch.setenv("CDE_PAGES_PER_RUN", "40")
    assert http._cde_budget() == 40
    monkeypatch.setenv("CDE_PAGES_PER_RUN", "5000")
    assert http._cde_budget() == http.MAX_CDE_PAGES
    monkeypatch.setenv("CDE_PAGES_PER_RUN", "")
    assert http._cde_budget() == 1


def test_waits_for_minimum_gap(conn, cde_fetcher, monkeypatch):
    monkeypatch.setenv("CDE_MIN_GAP_MINUTES", "100")
    clock = Clock()
    run(conn, cde_fetcher, clock)
    clock.advance(minutes=30)
    results = run(conn, cde_fetcher, clock)
    assert len(cde_fetcher.requests) == 1
    assert all(r.skipped and "spacing requests" in r.note for r in results.values())


def test_changing_min_gap_takes_effect_immediately(conn, cde_fetcher, monkeypatch):
    monkeypatch.setenv("CDE_MIN_GAP_MINUTES", "100")
    clock = Clock()
    run(conn, cde_fetcher, clock)
    clock.advance(minutes=60)
    assert all(r.skipped for r in run(conn, cde_fetcher, clock).values())
    monkeypatch.setenv("CDE_MIN_GAP_MINUTES", "30")
    run(conn, cde_fetcher, clock)
    assert len(cde_fetcher.requests) == 2  # the lower setting applied right away


def test_lower_min_gap_does_not_cut_backoff_short(conn, cde_fetcher, monkeypatch):
    clock = Clock()

    def blocked(url):
        raise BlockedError("redirected to a bot-protection challenge")

    real_get = cde_fetcher.get
    cde_fetcher.get = blocked
    run(conn, cde_fetcher, clock)
    cde_fetcher.get = real_get
    monkeypatch.setenv("CDE_MIN_GAP_MINUTES", "1")
    clock.advance(minutes=30)
    r = run(conn, cde_fetcher, clock)["sbe"]
    assert r.skipped and r.note.startswith("backing off") and cde_fetcher.requests == []


def test_challenge_backs_off_and_doubles(conn, cde_fetcher):
    clock = Clock()

    def blocked(url):
        cde_fetcher.requests.append(url)
        raise BlockedError("redirected to a bot-protection challenge")

    cde_fetcher.get = blocked
    r = run(conn, cde_fetcher, clock)["sbe"]
    assert not r.ok and r.backing_off and "backing off 2:00:00" in r.error
    clock.advance(hours=1)
    assert run(conn, cde_fetcher, clock)["sbe"].skipped  # still backing off
    clock.advance(hours=1, minutes=1)
    r = run(conn, cde_fetcher, clock)["sbe"]
    assert "backing off 4:00:00" in r.error
    assert conn.execute("select consecutive_failures from agencies where id = 'sbe'").fetchone()[0] == 2
    assert [o for o, in conn.execute("select outcome from crawl_log order by id")] == ["challenged"] * 2


def test_one_web_page_per_run_then_documents_only(conn, cde_fetcher, monkeypatch):
    monkeypatch.setenv("CDE_PAGES_PER_RUN", "3")
    waits = []
    paced.run_cde(conn, cde_fetcher, ["sbe", "accs"], today=dt.date(2026, 9, 1),
                  now=Clock(), sleep=waits.append)
    # The ACCS list and SBE agendas are due too, but they're web pages: one per run.
    assert cde_fetcher.requests == [SBE_LIST] and waits == []
    item01 = "https://www.cde.ca.gov/be/ag/ag/yr26/documents/sep26item01.docx"
    cde_fetcher.pages[item01] = ("Item 1 text", "text/plain")
    [r] = paced.run_cde(conn, cde_fetcher, ["sbe"], today=dt.date(2026, 9, 1),
                        now=lambda: dt.datetime(2026, 9, 2, tzinfo=dt.timezone.utc), sleep=waits.append)
    # The next run reads one agenda, then spends its other pages on documents, each reported.
    pages = cde_fetcher.requests[1:]
    assert pages[:2] == [SBE_SEP, item01] and len(pages) == 3
    assert pages[2].endswith((".docx", ".pdf", ".pptx"))
    assert waits == [90, 90]
    assert r.note.startswith("read the September 9, 2026 agenda")
    assert "; then downloaded Item 01" in r.note and "; then couldn't download" in r.note


def test_challenge_report_keeps_earlier_requests(conn, cde_fetcher, monkeypatch):
    monkeypatch.setenv("CDE_PAGES_PER_RUN", "2")
    paced.run_cde(conn, cde_fetcher, ["sbe"], today=dt.date(2026, 9, 1), now=Clock(), sleep=lambda s: None)

    def blocked(url):
        raise BlockedError("redirected to a bot-protection challenge")

    real_get = cde_fetcher.get
    cde_fetcher.get = lambda url: real_get(url) if url == SBE_SEP else blocked(url)
    [r] = paced.run_cde(conn, cde_fetcher, ["sbe"], today=dt.date(2026, 9, 1),
                        now=lambda: dt.datetime(2026, 9, 2, tzinfo=dt.timezone.utc), sleep=lambda s: None)
    assert r.backing_off and r.error.startswith("challenged by CDE bot protection on the document")
    assert "(earlier this run: read the September 9, 2026 agenda" in r.error


def test_acceptance_by_gap_split_by_origin(conn):
    now = dt.datetime.now(dt.timezone.utc)
    for gap, outcome, where in [(30, "challenged", "office"), (8, "challenged", "office"),
                                (7200, "ok", "office"), (7300, "ok", "github"),
                                (8000, "challenged", "github"), (21000, "ok", None)]:
        conn.execute("""insert into crawl_log (host, url, requested_at, gap_seconds, outcome, origin)
                        values ('www.cde.ca.gov', 'u', %s, %s, %s, %s)""", (now, gap, outcome, where))
    # Rows logged before origins were recorded all came from the office computer.
    assert paced.acceptance_by_gap(conn) == [
        ("GitHub's servers", "1-3 h", 1, 1),
        ("the office computer", "under 1 min", 0, 2),
        ("the office computer", "1-3 h", 1, 0),
        ("the office computer", "3-12 h", 1, 0),
    ]


def test_origin_is_logged_and_shown(conn, cde_fetcher, monkeypatch):
    monkeypatch.setenv("TRACKER_ORIGIN", "github")
    r = run(conn, cde_fetcher, Clock())["sbe"]
    assert "from GitHub's servers" in r.note
    assert conn.execute("select origin from crawl_log").fetchone()[0] == "github"


def test_migrations_apply_on_top_of_hand_applied_schema(conn):
    from tracker.migrate import migrate

    # Production state: the first migration was run by hand in the SQL editor,
    # nothing recorded, later ones not applied yet.
    conn.execute("drop table crawl_log, crawl_state, posting_versions")
    conn.execute("drop function record_posting_version, posting_snapshot cascade")
    # Undo the budget migration too (its enum value can't be removed; it's added with
    # "if not exists").
    conn.execute("drop view posting_status")
    conn.execute("drop table posting_notes, calendar_hearings, calendar_feeds")
    conn.execute("drop function calendar_ics, ics_fold, ics_escape, search_all")
    conn.execute("alter table agencies drop column program, drop column group_name, "
                 "drop column may_be_empty, drop column last_note")
    conn.execute("alter table postings drop column start_time, drop column location")
    conn.execute("delete from keywords where program is not null")
    conn.execute("alter table keywords drop column program cascade")  # and its trigger
    conn.execute("alter table user_prefs drop column programs")
    conn.commit()
    conn.execute("drop table if exists schema_migrations")
    conn.commit()
    later = ["20261031000001_paced_crawling.sql", "20261102000001_crawl_log_origin.sql",
             "20261105000001_posting_versions.sql", "20261113000001_publication_kind.sql",
             "20261113000002_budget.sql"]
    assert migrate(conn) == later
    names = {r[0] for r in conn.execute("select name from schema_migrations")}
    assert names == {"20261030000001_init.sql", *later}
    assert migrate(conn) == []
    assert conn.execute("select to_regclass('public.crawl_log') is not null").fetchone()[0]
