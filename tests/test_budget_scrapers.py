"""Budget scrapers against saved copies of the committee, DOF and LAO pages
(tests/fixtures/{abgt,sbud,dof,lao}/), and agenda splitting on text extracted from real
agenda PDFs. When a site is redesigned, re-save the page and update these expectations."""

import datetime as dt
from pathlib import Path

from tests.test_pipeline import mark_succeeded_before
from tracker import pipeline
from tracker.agencies import BY_ID
from tracker.agenda import split_agenda
from tracker.scrapers import get_scraper
from tracker.scrapers.dof import BudgetReleases, FinanceLetters, TrailerBills, load_json
from tracker.scrapers.lao import LAOBudget, LAOEducation
from tracker.scrapers.legislature import parse_location, parse_time, subject_from

FIX = Path(__file__).parent / "fixtures"
TODAY = dt.date(2026, 11, 13)
SPRING = dt.date(2026, 4, 20)  # mid-session: recent hearings get their details read


def parse(agency_id, fixture, today=TODAY):
    scraper = get_scraper(agency_id)
    postings = scraper.parse((FIX / fixture).read_bytes(), BY_ID[agency_id].url, today)
    return scraper, {p.external_key: p for p in postings}


def test_assembly_subcommittee_listing():
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html")
    assert len(ps) == 16  # hearings within HISTORY_DAYS; older sessions are left out
    may19 = ps["2026-05-19"]
    assert may19.title == "May 19, 2026 hearing: May Revision"
    assert may19.url == "https://abgt.assembly.ca.gov/media/8102"
    assert [(d.label, d.agenda) for d in may19.documents] == [
        ("Hearing Agenda: May Revision", True), ("Attachment A", False)]
    assert ps["2026-04-29"].title == \
        "April 29, 2026 hearing: PreK-12 Education / Higher Education (UC, CSU)"
    # Two agendas (9am and 10am) under one date heading are one hearing.
    assert sum(d.agenda for d in ps["2026-02-10"].documents) == 2
    # The older 2025 section names hearings in the links ("December 8 Agenda: ...").
    assert ps["2025-12-08"].title.startswith("December 8, 2025 hearing: Joint Oversight Hearing")
    assert all(p.partial for p in ps.values())  # nothing within the last 30 days

    _, spring = parse("abgt_sub3", "abgt/sub3-agendas.html", SPRING)
    assert not spring["2026-04-14"].partial and spring["2026-03-03"].partial


def test_assembly_empty_hearings_page():
    scraper, ps = parse("abgt_sub1", "abgt/hearings.html")
    assert ps == {} and scraper.warnings == []


def test_senate_subcommittee_listing_infers_years_times_and_places():
    scraper, ps = parse("sbud_sub1", "sbud/sub1-agendas.html")
    aug = ps["2026-08-12"]  # "Wednesday, August 12th at 9:30 a.m. - State Capitol, room 112"
    assert aug.start_time == dt.time(9, 30) and aug.location == "State Capitol, room 112"
    may28 = ps["2026-05-28"]
    assert may28.location == "1021 O Street, Rm 2100" and may28.start_time is None
    assert may28.title == "May 28, 2026 hearing: Part A - K-12 Education / Part B - Higher Education"
    assert [d.label for d in may28.documents if d.agenda] == [
        "Hearing Agenda - Part A - K-12 Education", "Hearing Agenda - Part B - Higher Education"]
    assert "2026-05-20" in ps  # "Wednesday, May 20" with no year


def test_senate_full_committee_next_hearing_link_before_date():
    scraper, ps = parse("sbud_full", "sbud/full-committee.html", dt.date(2026, 8, 20))
    # The agenda link comes before "August 30, 2026 - Hearing Cancelled" and
    # "August 31, 2026 - ...": it belongs to the hearing that isn't cancelled.
    assert ps["2026-08-30"].title == "August 30, 2026 hearing (cancelled)"
    assert ps["2026-08-30"].documents == []
    assert [d.url for d in ps["2026-08-31"].documents] == ["https://sbud.senate.ca.gov/media/3691"]
    assert ps["2026-08-31"].location == "1021 O Street, room 1200"
    assert ps["2026-06-24"].title == \
        "June 24, 2026 hearing: A.C.A. No. 20- - Save for California's Future Act"
    assert ps["2026-06-24"].start_time == dt.time(16, 0)
    assert scraper.warnings == []


def test_committee_reports():
    _, asm = parse("abgt_reports", "abgt/home.html")
    assert len(asm) == 11
    assert asm["media-8115"].title == \
        "2026-27 Assembly Democratic Budget Plan: Subcommittee No. 3 on Education Finance"
    assert asm["media-8133"].meeting_date == dt.date(2026, 6, 27)
    assert asm["media-8133"].title == "Floor Report of the 2026-27 Budget (June 27, 2026)"
    assert all(p.kind == "publication" for p in asm.values())
    _, sen = parse("sbud_reports", "sbud/home.html")
    assert len(sen) == 9 and sen["media-3674"].meeting_date == dt.date(2026, 6, 26)


def test_label_helpers():
    assert subject_from("May 19 - Sub. 4 Agenda: May Revision") == "May Revision"
    assert subject_from("Hearing Agenda Part 1:  PreK-12 Education") == "PreK-12 Education"
    assert subject_from("June 11 Agenda [pdf]") is None
    assert parse_time("Monday, June 15, 2026 at 10:00 a.m.") == dt.time(10)
    assert parse_time("9 a.m. -- 1021 O Street") == dt.time(9)
    assert parse_location("August 31, 2026 - Upon Call of the Chair - 1021 O Street, room 1200") \
        == "1021 O Street, room 1200"


def agenda(name):
    return split_agenda((FIX / name).read_text())


def test_agenda_issues_with_departments():
    items = agenda("abgt/agenda-sub3_8102.txt")
    assert [i.number for i in items] == [
        "Issue 1", "6870 · Issue 2", "6980 · Issue 3", "6980 · Issue 4", "6440 · Issue 5",
        "Issue 6", "Issue 7", "Issue 8"]
    assert items[2].title == "Workforce Pell Implementation"
    assert items[2].description == "6980 Student Aid Commission"
    assert items[6].description.startswith("6100 California Department of Education; 7760 Office")
    cde = agenda("abgt/agenda-sub3_8044.txt")
    assert [i.number for i in cde] == [f"6100 · Issue {n}" for n in range(1, 5)]
    # Senate contents lines read "Issue 1: Proposition 98 Overview ..... 3"; only the body
    # headings count, so each issue appears once.
    senate = agenda("sbud/agenda-sub1-may20.txt")
    assert [(i.number, i.title) for i in senate] == [
        ("6100 · Issue 1", "Proposition 98 Overview"), ("6100 · Issue 2", "May Revision Proposals"),
        ("6360 · Issue 3", "May Revision Proposals")]


def test_agenda_other_shapes():
    vote_only = agenda("sbud/agenda-sub1_3640.txt")  # contents list of departments
    assert [(i.number, i.title) for i in vote_only][:2] == [
        ("6100", "Department of Education"), ("0985", "California School Finance Authority")]
    bills = agenda("sbud/agenda-full_3668.txt")
    assert [(i.number, i.title, i.description) for i in bills] == [
        ("AB 109", "Budget Act of 2026", "Author: Gabriel")]
    numbered = agenda("abgt/agenda-full_8120.txt")
    assert [i.title for i in numbered] == ["Overview of the 2026 Budget Legislative Agreement",
                                           "Subcommittee Chair Remarks"]
    roman = agenda("sbud/agenda-sub1_3599.txt")
    assert [i.number for i in roman] == ["II", "III", "IV"]  # I. Welcome and V. Public Comment left out
    assert split_agenda("") == [] and split_agenda("Some text\nwithout items") == []
    # A year at the start of a line isn't a department code.
    yearly = split_agenda("6100 Department of Education\nIssue 2: Proposition 98\n2025 Budget Act\n"
                          "Issue 3: Local Control Funding Formula")
    assert [i.number for i in yearly] == ["6100 · Issue 2", "6100 · Issue 3"]


def test_pipeline_reads_items_from_agenda_and_flags_revisions(conn, fetcher, monkeypatch):
    monkeypatch.setattr(pipeline, "DOC_RECHECK_HOURS", 0)
    mark_succeeded_before(conn, "abgt_sub3")
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html", SPRING)
    hearing = ps["2026-04-14"]
    agenda_url = hearing.documents[0].url
    v1 = (FIX / "abgt/agenda-sub3_8044.txt").read_text()
    fetcher.pages[agenda_url] = (v1, "text/plain")
    warnings: list[str] = []
    r = pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [hearing], SPRING,
                            warnings=warnings)
    pid = r.new[0]
    rows = conn.execute("select number, title from items where posting_id = %s order by position",
                        (pid,)).fetchall()
    assert rows[0] == ("6100 · Issue 1", "California Community Schools Partnership Program")
    assert len(rows) == 4 and warnings == []

    # Same listing, unchanged agenda: nothing new.
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html", SPRING)
    assert pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [ps["2026-04-14"]],
                               SPRING).unchanged == 1

    # The committee revises the agenda at the same address: a new issue is added.
    fetcher.pages[agenda_url] = (v1 + "\nIssue 5: Charter School Oversight\n", "text/plain")
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html", SPRING)
    r = pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [ps["2026-04-14"]], SPRING)
    assert r.updated == [pid]
    assert conn.execute("select count(*) from items where posting_id = %s", (pid,)).fetchone()[0] == 5
    terms = {t for (t,) in conn.execute(
        "select k.term from matches m join keywords k on k.id = m.keyword_id "
        "where m.posting_id = %s and m.source = 'item'", (pid,))}
    assert "charter" in terms


def test_agenda_without_recognizable_items_warns(conn, fetcher):
    mark_succeeded_before(conn, "sbud_sub1")
    _, ps = parse("sbud_sub1", "sbud/sub1-agendas.html", dt.date(2026, 4, 25))
    hearing = ps["2026-04-23"]
    fetcher.pages[hearing.documents[0].url] = ("Agenda\nA hearing about things.", "text/plain")
    warnings: list[str] = []
    pipeline.run_agency(conn, fetcher, "sbud_sub1", lambda f: [hearing], dt.date(2026, 4, 25),
                        warnings=warnings)
    assert warnings and "no agenda items recognized" in warnings[0]
    assert "no agenda items recognized" in conn.execute(
        "select last_note from agencies where id = 'sbud_sub1'").fetchone()[0]


def test_trailer_bills():
    bills = load_json((FIX / "dof/trailer-bills-m.json").read_bytes())
    ps = {p.external_key: p for p in TrailerBills().parse(bills, "2026-27 May Revision Budget TBL Tracking")}
    assert len(ps) == 82
    pell = ps["tbl-1408"]
    assert pell.title == "Workforce Pell Grant Program"
    assert pell.meeting_date == dt.date(2026, 5, 22) and pell.kind == "publication"
    assert pell.items[0].title == "Education"
    assert pell.documents[0].url == "https://trailerbill.dof.ca.gov/public/pdf/1408"
    info = load_json((FIX / "dof/trailer-app-info.json").read_bytes())
    assert info["pits"] == ["m", "p", "h"]


def test_budget_releases():
    ps = [BudgetReleases.posting("2026-27", pit, f"/budget/{pit}/2026-27/BudgetSummary",
                                 load_json((FIX / f"dof/ebudget-appinfo-{pit}.json").read_bytes()))
          for pit in "pme"]
    assert [(p.title, p.meeting_date) for p in ps] == [
        ("Governor's Budget 2026-27", dt.date(2026, 1, 9)),
        ("May Revision 2026-27", dt.date(2026, 5, 14)),
        ("Enacted Budget 2026-27", dt.date(2026, 6, 29))]
    assert [p.documents[0].url for p in ps] == [
        "https://ebudget.ca.gov/2026-27/pdf/BudgetSummary/FullBudgetSummary.pdf",
        "https://ebudget.ca.gov/2026-27/pdf/Revised/BudgetSummary/FullBudgetSummary.pdf",
        "https://ebudget.ca.gov/2026-27/pdf/Enacted/BudgetSummary/FullBudgetSummary.pdf"]
    links = load_json((FIX / "dof/ebudget-getlink.json").read_bytes())
    assert links[0]["budgetYear"] == "2026-27" and links[0]["summary_m"]


def test_finance_letters():
    fl = FinanceLetters()
    pages = fl.letter_pages((FIX / "dof/budget-details.html").read_bytes())
    assert [label for label, _ in pages] == [
        "2026-27 Proposed May Adjustments to the Governor’s Budget",
        "2026-27 Proposed April 1st Adjustments to the Governor’s Budget"]
    p = fl.parse_letters((FIX / "dof/finance-letters-april.html").read_bytes(), pages[1][1], pages[1][0])
    assert p.external_key == "2026-27-proposed-april-1st-adjustments-to-the-governors-budget"
    assert len(p.items) == 8 and p.items[0].title == "Legislative, Judicial, and Executive"
    assert p.documents[0].url.endswith("/LJE.pdf") and p.documents[0].item_index == 0


def test_lao():
    budget = {p.external_key: p for p in LAOBudget().parse((FIX / "lao/budget.html").read_bytes(), TODAY)}
    assert len(budget) == 52
    assert budget["5101"].title == "Overview of the Governor’s Budget"
    assert budget["5101"].meeting_date == dt.date(2026, 1, 12)
    assert budget["5101"].items[0].title == "The Overall Budget"
    edu = {p.external_key: p for p in LAOEducation().parse((FIX / "lao/education.html").read_bytes(), TODAY)}
    assert len(edu) == 14
    k12 = edu["5209"]
    assert k12.title == "The 2026-27 California Spending Plan: Proposition 98 and K-12 Education"
    assert k12.meeting_date == dt.date(2026, 10, 9)
    assert k12.items[0].number == "Report"
    assert k12.documents[0].url == "https://www.lao.ca.gov/Publications/Report/5209"
    # A year later, these are all older than HISTORY_DAYS.
    assert LAOEducation().parse((FIX / "lao/education.html").read_bytes(), dt.date(2028, 1, 1)) == []


def test_agenda_downloaded_in_a_later_run_is_not_a_change(conn, fetcher, monkeypatch):
    """First run: the agenda is deferred (per-site document limit). When it downloads in a
    later run, its items are stored without flagging the hearing as updated."""
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html", SPRING)
    hearing = ps["2026-03-24"]  # older than 30 days: loaded as "already posted"
    pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [hearing], SPRING)  # first run, no agenda
    pid = conn.execute("select id from postings where external_key = '2026-03-24'").fetchone()[0]
    assert conn.execute("select count(*) from items where posting_id = %s", (pid,)).fetchone()[0] == 0

    agenda_text = (FIX / "abgt/agenda-sub3_8044.txt").read_text()  # mentions charter schools? no
    fetcher.pages[hearing.documents[0].url] = (agenda_text + "\ncharter school mention", "text/plain")
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html", SPRING)
    hearing = ps["2026-03-24"]
    hearing.partial = False  # as if it were still within the detail window
    r = pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [hearing], SPRING)
    assert r.unchanged == 1 and not r.updated
    assert conn.execute("select count(*) from items where posting_id = %s", (pid,)).fetchone()[0] == 4
    assert conn.execute("select status from posting_status where id = %s", (pid,)).fetchone()[0] == "baseline"
    assert conn.execute("select count(*) from posting_events").fetchone()[0] == 0
    snapshot = conn.execute("select snapshot from posting_versions where posting_id = %s "
                            "order by changed_at desc limit 1", (pid,)).fetchone()[0]
    assert len(snapshot["items"]) == 4 and snapshot["keyword_hits"]
    # And it stays unchanged afterwards.
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html", SPRING)
    ps["2026-03-24"].partial = False
    assert pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [ps["2026-03-24"]], SPRING).unchanged == 1


def test_deferred_documents_of_older_hearings_still_download(conn, fetcher):
    from tracker.documents import FetchedDocument

    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html")  # November: all hearings are partial
    hearing = ps["2026-04-14"]
    url = hearing.documents[0].url
    deferred = FetchedDocument(None, None, "document limit for abgt.assembly.ca.gov reached this run; "
                               "will download in a later run")
    import tracker.pipeline as pl
    real = pl.fetch_document
    pl.fetch_document = lambda f, u: deferred
    try:
        pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [hearing], TODAY)
    finally:
        pl.fetch_document = real
    fetcher.pages[url] = ((FIX / "abgt/agenda-sub3_8044.txt").read_text(), "text/plain")
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html")
    assert ps["2026-04-14"].partial
    r = pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [ps["2026-04-14"]], TODAY)
    assert r.unchanged == 1 and url in fetcher.requests
    assert conn.execute("select count(*) from items").fetchone()[0] == 4
    fetcher.requests.clear()
    _, ps = parse("abgt_sub3", "abgt/sub3-agendas.html")
    pipeline.run_agency(conn, fetcher, "abgt_sub3", lambda f: [ps["2026-04-14"]], TODAY)
    assert fetcher.requests == []  # nothing left to download: back to leaving it as stored
