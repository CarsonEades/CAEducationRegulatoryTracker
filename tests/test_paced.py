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


def test_waits_for_minimum_gap(conn, cde_fetcher, monkeypatch):
    monkeypatch.setenv("CDE_MIN_GAP_MINUTES", "100")
    clock = Clock()
    run(conn, cde_fetcher, clock)
    clock.advance(minutes=30)
    results = run(conn, cde_fetcher, clock)
    assert len(cde_fetcher.requests) == 1
    assert all(r.skipped and "spacing requests" in r.note for r in results.values())


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


def test_pages_per_run_setting(conn, cde_fetcher, monkeypatch):
    monkeypatch.setenv("CDE_PAGES_PER_RUN", "3")
    waits = []
    paced.run_cde(conn, cde_fetcher, ["sbe", "accs"], today=dt.date(2026, 9, 1),
                  now=Clock(), sleep=waits.append)
    assert cde_fetcher.requests == [SBE_LIST, ACCS_LIST, SBE_SEP]
    assert waits == [90, 90]


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
    conn.execute("drop table crawl_log, crawl_state")
    conn.commit()
    conn.execute("drop table if exists schema_migrations")
    conn.commit()
    assert migrate(conn) == ["20261031000001_paced_crawling.sql", "20261102000001_crawl_log_origin.sql"]
    names = {r[0] for r in conn.execute("select name from schema_migrations")}
    assert names == {"20261030000001_init.sql", "20261031000001_paced_crawling.sql",
                     "20261102000001_crawl_log_origin.sql"}
    assert migrate(conn) == []
    assert conn.execute("select to_regclass('public.crawl_log') is not null").fetchone()[0]
