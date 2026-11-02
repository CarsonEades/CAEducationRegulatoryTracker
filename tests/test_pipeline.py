import datetime as dt

from tracker import pipeline
from tracker.models import DocLink, Item, Posting

TODAY = dt.date(2026, 10, 30)
AGENDA_URL = "https://www.cde.ca.gov/be/ag/ag/nov2026.asp"
REPORT_URL = "https://www.cde.ca.gov/be/ag/ag/item05.pdf"


def nov_meeting(**overrides):
    p = Posting(
        external_key="2026-11-05",
        title="November 5-6, 2026 State Board Meeting",
        meeting_date=dt.date(2026, 11, 5),
        url=AGENDA_URL,
        items=[
            Item("Approval of minutes", number="1"),
            Item("Appeal of a petition denial", number="5",
                 description="Hearing on the Acme Academy appeal."),
        ],
        documents=[DocLink(REPORT_URL, "Item 5 staff report", item_index=1)],
    )
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def run_once(conn, fetcher, postings, agency="sbe", today=TODAY):
    return pipeline.run_agency(conn, fetcher, agency, lambda f: postings, today)


def status(conn, posting_id):
    return conn.execute("select status from posting_status where id = %s",
                        (posting_id,)).fetchone()[0]


def add_user(conn, email="a@example.org"):
    return conn.execute("insert into auth.users (email) values (%s) returning id",
                        (email,)).fetchone()[0]


def mark_succeeded_before(conn, agency="sbe"):
    conn.execute("update agencies set last_success_at = now() - interval '1 day' where id = %s",
                 (agency,))


def test_new_posting_is_stored_matched_and_flagged(conn, fetcher):
    mark_succeeded_before(conn)
    fetcher.pages[REPORT_URL] = ("<html><body><main>The charter school appeal under SB 740."
                                 "</main></body></html>", "text/html")
    r = run_once(conn, fetcher, [nov_meeting()])
    assert r.ok and len(r.new) == 1
    pid = r.new[0]
    assert status(conn, pid) == "new"
    rows = conn.execute(
        """select k.term, m.source from matches m join keywords k on k.id = m.keyword_id
           where m.posting_id = %s""", (pid,)).fetchall()
    assert set(rows) == {("SB 740", "document"), ("charter", "document")}
    assert conn.execute("select count(*) from posting_events where posting_id = %s",
                        (pid,)).fetchone()[0] == 1


def test_unchanged_rerun_creates_no_event(conn, fetcher):
    mark_succeeded_before(conn)
    fetcher.pages[REPORT_URL] = ("report v1", "text/plain")
    run_once(conn, fetcher, [nov_meeting()])
    r = run_once(conn, fetcher, [nov_meeting()])
    assert r.unchanged == 1 and not r.new and not r.updated
    assert conn.execute("select count(*) from posting_events").fetchone()[0] == 1


def test_review_then_revised_document_shows_updated(conn, fetcher, monkeypatch):
    monkeypatch.setattr(pipeline, "DOC_RECHECK_HOURS", 0)
    mark_succeeded_before(conn)
    fetcher.pages[REPORT_URL] = ("report v1", "text/plain")
    pid = run_once(conn, fetcher, [nov_meeting()]).new[0]
    uid = add_user(conn)
    conn.execute("insert into reviews (posting_id, user_id) values (%s, %s)", (pid, uid))
    conn.commit()
    assert status(conn, pid) == "reviewed"
    who = conn.execute("select last_reviewed_by_email from posting_status where id = %s",
                       (pid,)).fetchone()[0]
    assert who == "a@example.org"

    fetcher.pages[REPORT_URL] = ("report v2 adds a charter item", "text/plain")
    r = run_once(conn, fetcher, [nov_meeting()])
    assert r.updated == [pid]
    assert status(conn, pid) == "updated"
    assert conn.execute("select matched_keywords from posting_status where id = %s",
                        (pid,)).fetchone()[0] == ["charter"]


def test_new_agenda_item_is_an_update(conn, fetcher):
    mark_succeeded_before(conn)
    pid = run_once(conn, fetcher, [nov_meeting(documents=[])]).new[0]
    more = nov_meeting(documents=[])
    more.items.append(Item("Charter revocation hearing", number="12"))
    r = run_once(conn, fetcher, [more])
    assert r.updated == [pid]
    assert conn.execute("select count(*) from items where posting_id = %s",
                        (pid,)).fetchone()[0] == 3
    assert conn.execute("select count(*) from matches where posting_id = %s and source = 'item'",
                        (pid,)).fetchone()[0] == 1


def test_first_run_baselines_past_meetings_but_not_upcoming(conn, fetcher):
    past = nov_meeting(external_key="2026-09-10", meeting_date=dt.date(2026, 9, 10),
                       title="September meeting", documents=[])
    upcoming = nov_meeting(documents=[])
    r = run_once(conn, fetcher, [past, upcoming])
    assert len(r.new) == 1  # only the upcoming meeting is announced
    by_title = dict(conn.execute("select title, status from posting_status").fetchall())
    assert by_title == {"September meeting": "baseline", upcoming.title: "new"}
    assert conn.execute("select count(*) from posting_events").fetchone()[0] == 1


def test_document_fetch_failure_keeps_last_good_copy(conn, fetcher, monkeypatch):
    monkeypatch.setattr(pipeline, "DOC_RECHECK_HOURS", 0)
    mark_succeeded_before(conn)
    fetcher.pages[REPORT_URL] = ("charter report", "text/plain")
    pid = run_once(conn, fetcher, [nov_meeting()]).new[0]
    del fetcher.pages[REPORT_URL]
    r = run_once(conn, fetcher, [nov_meeting()])
    assert r.unchanged == 1
    text, err = conn.execute("select text, fetch_error from documents where posting_id = %s",
                             (pid,)).fetchone()
    assert text == "charter report" and "404" in err


def test_recent_documents_are_not_rechecked_every_run(conn, fetcher):
    mark_succeeded_before(conn)
    fetcher.pages[REPORT_URL] = ("v1", "text/plain")
    other = "https://www.cde.ca.gov/be/ag/ag/item06.pdf"
    run_once(conn, fetcher, [nov_meeting()])
    fetcher.requests.clear()
    fetcher.pages[other] = ("item six", "text/plain")
    p = nov_meeting()
    p.documents.append(DocLink(other, "Item 6"))
    r = run_once(conn, fetcher, [p])
    assert fetcher.requests == [other]  # only the new link is downloaded
    assert len(r.updated) == 1
    conn.execute("update documents set fetched_at = now() - interval '13 hours'")
    fetcher.requests.clear()
    run_once(conn, fetcher, [p])
    assert sorted(fetcher.requests) == sorted([REPORT_URL, other])


def test_old_meetings_do_not_redownload_documents(conn, fetcher):
    mark_succeeded_before(conn)
    old = nov_meeting(external_key="2026-01-08", meeting_date=dt.date(2026, 1, 8))
    fetcher.pages[REPORT_URL] = ("v1", "text/plain")
    run_once(conn, fetcher, [old])
    fetcher.requests.clear()
    run_once(conn, fetcher, [old])
    assert fetcher.requests == []


def test_new_keyword_searches_existing_postings_with_agency_scope(conn, fetcher):
    mark_succeeded_before(conn)
    mark_succeeded_before(conn, "calpers")
    run_once(conn, fetcher, [nov_meeting(documents=[])])
    run_once(conn, fetcher, [nov_meeting(documents=[], external_key="x")], agency="calpers")
    conn.execute("insert into keywords (term, match_type, agency_id) values ('Acme', 'word', 'sbe')")
    rows = conn.execute(
        """select p.agency_id from matches m join keywords k on k.id = m.keyword_id
           join postings p on p.id = m.posting_id where k.term = 'Acme'""").fetchall()
    assert rows == [("sbe",)]


def test_scraper_errors_and_empty_pages_count_as_failures(conn, fetcher):
    def boom(f):
        raise RuntimeError("layout changed")

    r = pipeline.run_agency(conn, fetcher, "sbe", boom, TODAY)
    assert not r.ok and r.consecutive_failures == 1
    conn.execute("update agencies set last_posting_count = 4, consecutive_failures = 0 "
                 "where id = 'sbe'")
    r = run_once(conn, fetcher, [])
    assert not r.ok and "found no postings" in r.error
    run_once(conn, fetcher, [nov_meeting(documents=[])])
    assert conn.execute("select consecutive_failures, last_error from agencies where id = 'sbe'"
                        ).fetchone() == (0, None)


def test_full_text_search(conn, fetcher):
    mark_succeeded_before(conn)
    fetcher.pages[REPORT_URL] = ("Facilities funding for charter schools", "text/plain")
    pid = run_once(conn, fetcher, [nov_meeting()]).new[0]
    rows = conn.execute("select posting_id, source from search_all('facilities funding')").fetchall()
    assert rows == [(pid, "document")]
    assert conn.execute("select count(*) from search_all('facilities', 'calpers')").fetchone()[0] == 0


def test_run_skips_agencies_without_scrapers(conn, fetcher, monkeypatch):
    monkeypatch.setattr(pipeline, "get_scraper", lambda agency_id: None)
    results = pipeline.run(conn, fetcher, ["fppc"], today=TODAY)
    assert results[0].skipped and results[0].error == "scraper not built yet"


def test_late_document_with_new_keyword_hit_flags_update(conn, fetcher):
    mark_succeeded_before(conn)
    pid = run_once(conn, fetcher, [nov_meeting()]).new[0]  # report not downloadable yet
    uid = add_user(conn)
    conn.execute("insert into reviews (posting_id, user_id) values (%s, %s)", (pid, uid))
    conn.commit()
    fetcher.pages[REPORT_URL] = ("Staff recommend denying the charter petition.", "text/plain")
    r = run_once(conn, fetcher, [nov_meeting()])
    assert r.updated == [pid]
    assert status(conn, pid) == "updated"


def test_late_document_without_new_hits_is_not_a_change(conn, fetcher):
    mark_succeeded_before(conn)
    pid = run_once(conn, fetcher, [nov_meeting()]).new[0]
    fetcher.pages[REPORT_URL] = ("Routine minutes.", "text/plain")
    r = run_once(conn, fetcher, [nov_meeting()])
    assert r.unchanged == 1 and not r.updated
    text, = conn.execute("select text from documents where posting_id = %s", (pid,)).fetchone()
    assert text == "Routine minutes."  # stored and searchable all the same


def test_nul_characters_in_text_are_dropped(conn, fetcher):
    mark_succeeded_before(conn)
    fetcher.pages[REPORT_URL] = ("charter\x00 report", "text/plain")
    p = nov_meeting()
    p.items[0].title = "Approval\x00 of minutes"
    pid = run_once(conn, fetcher, [p]).new[0]
    assert conn.execute("select text from documents where posting_id = %s", (pid,)).fetchone()[0] == "charter report"
    assert conn.execute("select title from items where posting_id = %s and position = 0",
                        (pid,)).fetchone()[0] == "Approval of minutes"


def test_rarely_checked_agency_waits_between_checks(conn, fetcher, monkeypatch):
    monkeypatch.setattr(pipeline, "CHECK_EVERY_HOURS", {"sbe": 6})
    calls = []

    class Fake:
        def fetch(self, f):
            calls.append(1)
            return [nov_meeting(documents=[])]

    monkeypatch.setattr(pipeline, "get_scraper", lambda a: Fake())
    monkeypatch.setattr("tracker.paced.CDE_AGENCIES", {})
    pipeline.run(conn, fetcher, ["sbe"], today=TODAY)
    r = pipeline.run(conn, fetcher, ["sbe"], today=TODAY)[0]
    assert calls == [1] and r.skipped and "every 6 hours" in r.note
    conn.execute("update agencies set last_checked_at = now() - interval '7 hours' where id = 'sbe'")
    pipeline.run(conn, fetcher, ["sbe"], today=TODAY)
    assert calls == [1, 1]


def test_partial_listing_leaves_stored_posting_alone(conn, fetcher):
    mark_succeeded_before(conn)
    pid = run_once(conn, fetcher, [nov_meeting(documents=[])]).new[0]
    bare = Posting(external_key="2026-11-05", title="November meeting", partial=True)
    r = run_once(conn, fetcher, [bare])
    assert r.unchanged == 1 and not r.updated
    assert conn.execute("select count(*) from items where posting_id = %s", (pid,)).fetchone()[0] == 2
    assert conn.execute("select title from postings where id = %s", (pid,)).fetchone()[0] \
        == "November 5-6, 2026 State Board Meeting"
