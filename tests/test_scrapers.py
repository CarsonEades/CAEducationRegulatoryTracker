"""Scrapers against saved copies of the agency pages (tests/fixtures/<agency>/).
When a site is redesigned, re-save the page and update these expectations."""

import datetime as dt
import json
from pathlib import Path

from tests.conftest import FakeFetcher
from tracker.scrapers.ctc import BASE, CTC
from tracker.scrapers.oag import AGInitiatives, AGOpinions

FIX = Path(__file__).parent / "fixtures"


def test_ag_opinions_merges_tables_per_opinion():
    postings = {p.external_key: p for p in AGOpinions().parse((FIX / "ag_opinions/monthly-report.html").read_bytes())}
    assert len(postings) == 15
    issued = postings["26-901"]
    assert issued.kind == "opinion"
    assert issued.meeting_date == dt.date(2026, 10, 7)
    assert issued.title.startswith("Opinion 26-901 (Issued 10/07/2026): DANIEL BOOKMAN")
    assert [i.number for i in issued.items] == ["Question", "Conclusion", "Status"]
    assert issued.items[2].title == "Issued 10/07/2026"  # no month-specific "assigned to"
    assert [d.url for d in issued.documents] == ["https://oag.ca.gov/system/files/opinions/pdfs/26-901.pdf"]

    withdrawn = postings["26-203"]
    assert withdrawn.items[-1].title == "Withdrawn on 09/01/2026" and not withdrawn.documents

    pending = postings["26-602"]
    assert pending.items[-1].title == "Pending (assigned to Thomas)"
    assert pending.meeting_date is None


def test_ag_initiatives():
    postings = AGInitiatives().parse((FIX / "ag_initiatives/active-measures.html").read_bytes())
    assert len(postings) == 46
    by_key = {p.external_key: p for p in postings}
    amended = by_key["26-0006"]  # 26-0006A2 updates the original posting
    assert amended.title == "26-0006A2: “Approval Voting Act”"
    assert amended.meeting_date == dt.date(2026, 9, 11)
    assert [i.title for i in amended.items] == [
        "Submitted for Title and Summary on September 11, 2026", "Amanda Mahlendorf"]

    food = by_key["26-0005"]
    assert food.meeting_date == dt.date(2026, 9, 25)
    assert [d.label for d in food.documents] == [
        "Initiative text (26-0005)", "Official title and summary", "Fiscal Impact Estimate Report"]


def test_ctc_meeting_with_items_and_documents():
    meetings = json.loads((FIX / "ctc/meetings.json").read_text())
    fetcher = FakeFetcher({f"{BASE}/Details/381": ((FIX / "ctc/details-381.html").read_bytes(), "text/html")})
    fetcher.post = lambda url, **kw: type("R", (), {"json": lambda self: meetings})()
    [p] = CTC().fetch(fetcher)
    assert p.external_key == "381"
    assert p.meeting_date == dt.date(2026, 10, 29)
    assert p.title == "Committee on Accreditation Meeting - October 29, 2026 (Committee on Accreditation (COA))"
    assert len(p.items) == 18
    item12 = p.items[11]
    assert (item12.number, item12.title) == ("12", "Report of the Accreditation Site Visit Team to Summit Prep Charter HS")
    assert p.items[-1].number is None and p.items[-1].title == "Adjournment"
    assert len(p.documents) == 11
    insert = next(d for d in p.documents if "INSERT" in (d.label or ""))
    assert insert.url == f"{BASE}/Document/Download/10915" and p.items[insert.item_index].number == "5"


def test_opsc_meetings_including_ones_without_documents_yet():
    from tracker.scrapers.opsc import OPSC

    postings = OPSC().parse((FIX / "opsc/meetings.html").read_bytes(), dt.date(2026, 10, 30))
    assert [(p.title, len(p.documents)) for p in postings] == [
        ("State Allocation Board Meeting - October 28, 2026", 2),
        ("Charter School Facilities Program Webinar - November 3, 2026", 0),  # year inferred
        ("Stakeholder Meeting - November 5, 2026", 2),
    ]
    assert postings[0].documents[1].label == "SAB Agenda with Supporting Documentation"


def test_csfa_listing_and_staff_reports():
    from tracker.scrapers.csfa import CSFA

    meetings = CSFA().parse_listing((FIX / "csfa/meeting.html").read_bytes())
    assert len(meetings) == 10
    assert meetings[0]["date"] == dt.date(2026, 10, 29)
    assert [label for label, _ in meetings[0]["links"]] == ["Agenda", "Staff Reports and Resolutions"]
    assert meetings[2]["cancelled"] and not meetings[2]["links"]

    items, docs = CSFA().parse_staff_page((FIX / "csfa/october-29-2026-staff.html").read_bytes(),
                                          "https://www.treasurer.ca.gov/x")
    assert [i.number for i in items] == ["4", "5", "6", "7", "8", "9", "10", "11", "12"]
    assert "Tree of Life International Charter" in items[2].title
    assert len(docs) == 21
    # Item 12 is split over two list entries on the page; both documents stay with it.
    assert [d.item_index for d in docs[-2:]] == [8, 8]


def test_fppc_listing_and_agenda():
    from tracker.scrapers.fppc import FPPC

    listing = FPPC().parse_listing((FIX / "fppc/commission-hearings.html").read_bytes())
    assert len(listing) == 7
    assert listing[0] == ("https://www.fppc.ca.gov/events/commission-hearings/2026/oct-2026-agenda/",
                          "October 15, 2026")

    items, docs = FPPC().parse_agenda((FIX / "fppc/oct-2026-agenda.html").read_bytes(),
                                      "https://www.fppc.ca.gov/events/commission-hearings/2026/oct-2026-agenda/")
    assert [i.number for i in items] == [str(n) for n in range(1, 11)]
    assert items[2].title == "In the Matter of Michael Gatto and Joshua Meister; FPPC Case No. 20/945."
    assert items[2].description.startswith("Staff: Neal Bucknell")
    assert len(docs) == 28
    assert (docs[1].label, docs[1].item_index) == ("Gatto and Meister - Stipulation", 2)


def test_calpers_listing_and_committee_page():
    from tracker.scrapers.calpers import CalPERS

    urls = CalPERS().parse_listing((FIX / "calpers/board-meetings.html").read_bytes())
    # Only the two latest months, not the years of archived meetings the page links.
    assert {u[-6:] for u in urls} == {"202609", "202607"}
    url = "https://www.calpers.ca.gov/about/board/board-meetings/pension-202609"
    p = CalPERS().parse_meeting((FIX / "calpers/pension-202609.html").read_bytes(), url)
    assert (p.external_key, p.meeting_date) == ("pension-202609", dt.date(2026, 9, 15))
    numbers = [i.number for i in p.items]
    assert numbers[:2] == ["1", "1"] and numbers[7:9] == ["2", "3"]  # open session resumes at 2
    # Item numbers line up with CalPERS' own document names.
    for d in p.documents:
        assert f"-item-{p.items[d.item_index].number}-" in d.url
    assert len(p.documents) == 12


def test_calstrs_listing_and_meeting():
    from tracker.scrapers.calstrs import CalSTRS

    listing = CalSTRS().parse_listing((FIX / "calstrs/board-meetings.html").read_bytes())
    assert listing[0] == ("https://www.calstrs.com/2026-11-03-board-meeting-november-2026", dt.date(2026, 11, 3))
    assert len(listing) == 6
    title, items, docs = CalSTRS().parse_meeting((FIX / "calstrs/2026-11-03.html").read_bytes(), listing[0][0])
    assert len(items) == 71 and len(docs) == 101
    assert items[2].title.startswith("Consideration of Proposed Administrative Decision")
    assert items[2].description.startswith("Appeals Committee - 2:30 p.m. (Nov 3) · Session: Open")
    assert items[-1].description.startswith("Regular Meeting - 10:45 a.m. (Nov 5)")
    assert (docs[0].label, docs[0].item_index) == ("Item 01", 0)


def test_sbe_listing_and_agenda():
    from tracker.scrapers.cde import SBE, parse_agenda_page

    listing = SBE().parse_listing((FIX / "sbe/index.html").read_bytes())
    assert listing[0] == ("202609", "https://www.cde.ca.gov/be/ag/ag/yr26/agenda202609.asp",
                          "September 9, 2026", dt.date(2026, 9, 9))
    items, docs = parse_agenda_page((FIX / "sbe/agenda202609.html").read_bytes(), listing[0][1])
    assert len(items) == 29
    assert items[5].number == "06"
    assert items[5].title.startswith("Assignment of Charter School Numbers to Newly Established Charter Schools")
    assert items[10].number == "W-01"
    assert len(docs) == 42
    assert docs[0].url == "https://www.cde.ca.gov/be/ag/ag/yr26/documents/sep26item01.docx"
    assert (docs[2].label, docs[2].item_index) == ("Item 02 Attachment 1 (DOCX)", 1)


def test_accs_listing_and_agenda():
    from tracker.scrapers.cde import ACCS, parse_agenda_page

    listing = ACCS().parse_listing((FIX / "accs/accsagendas.html").read_bytes())
    assert listing[0][:3] == ("accsnotice100626", "https://www.cde.ca.gov/be/cc/cs/accsnotice100626.asp",
                              "October 6, 2026")
    assert any(label.endswith("(Canceled)") for _, _, label, _ in listing)
    items, docs = parse_agenda_page((FIX / "accs/accsnotice100626.html").read_bytes(), listing[0][1])
    assert [i.number for i in items] == ["01", "02", "03"]
    assert "Highlands Community Charter School" in items[1].title
    assert all(d.item_index is not None for d in docs)


def test_long_dates():
    from tracker.scrapers.common import parse_long_date

    assert parse_long_date("September 2026") is None  # no day: don't read "20" from the year
    assert parse_long_date("Nov. 5-6, 2026") == dt.date(2026, 11, 5)
    assert parse_long_date("Tuesday, November 3", dt.date(2026, 10, 30)) == dt.date(2026, 11, 3)
    assert parse_long_date("January 8", dt.date(2026, 12, 20)) == dt.date(2027, 1, 8)


def test_ccee_meetings_from_wordpress_api():
    from tracker.scrapers.ccee import CCEE

    page = json.loads((FIX / "ccee/pages-meetings.json").read_text())
    postings = CCEE().parse(page["content"]["rendered"], today=dt.date(2026, 10, 30))
    # Scheduled meetings without documents are skipped; history is limited to ~a year.
    assert [p.meeting_date for p in postings] == [
        dt.date(2026, 8, 13), dt.date(2026, 6, 18), dt.date(2026, 2, 26), dt.date(2025, 12, 18)]
    assert postings[0].title.startswith("CCEE Governing Board Meeting - August 13, 2026")
    assert [d.label for d in postings[0].documents] == ["Agenda", "Public Correspondence"]


def test_fcc_open_meetings_from_federal_register():
    from tracker.scrapers.fcc import FCC

    postings = FCC().parse(json.loads((FIX / "fcc/documents.json").read_text()))
    assert len(postings) == 3
    p = postings[0]
    assert p.external_key == "2026-22080"
    assert p.title == "FCC Open Commission Meeting Thursday, October 29, 2026"
    assert p.meeting_date == dt.date(2026, 10, 29)
    assert p.items[0].title.startswith("The Commission will consider a Further Notice")
    assert p.documents[0].url == "https://www.govinfo.gov/content/pkg/FR-2026-10-29/pdf/2026-22080.pdf"


def test_no_platform_specific_date_formats():
    # %-d works on Linux only; the office runner may be Windows.
    root = Path(__file__).parent.parent / "tracker"
    offenders = [str(p) for p in root.rglob("*.py") if "%-d" in p.read_text()]
    assert offenders == []


def test_eaap_meetings_and_reports():
    from tracker.scrapers.eaap import EAAP

    postings = EAAP().parse((FIX / "eaap/agendas.html").read_bytes(), today=dt.date(2026, 11, 2))
    assert len(postings) == 7  # about a year of meetings
    p = postings[0]
    assert (p.title, p.meeting_date) == ("EAAP Meeting - October 19, 2026", dt.date(2026, 10, 19))
    assert p.url.endswith("/Agenda-10-19-2026.pdf")
    assert [d.label for d in p.documents][:3] == [
        "Agenda", "Summary Review Report 10-9-2026", "Formal Appeals Report 10-9-2026"]
    assert postings[-1].meeting_date == dt.date(2025, 10, 27)
