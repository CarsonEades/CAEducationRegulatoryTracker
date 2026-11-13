"""The agencies we track. This list is the source of truth; each run syncs it
into the `agencies` table."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Agency:
    id: str
    name: str
    short_name: str
    url: str
    program: str = "education"  # education | budget
    group: str | None = None    # dashboard heading, e.g. "Assembly Budget"
    # The listing can legitimately be empty (e.g. a committee page between sessions), so
    # finding nothing is a warning rather than a failure.
    may_be_empty: bool = False


AGENCIES: list[Agency] = [
    Agency("sbe", "State Board of Education", "SBE",
           "https://www.cde.ca.gov/be/ag/ag/index.asp"),
    Agency("accs", "Advisory Commission on Charter Schools", "ACCS",
           "https://www.cde.ca.gov/be/cc/cs/accsagendas.asp"),
    Agency("opsc", "Office of Public School Construction", "OPSC",
           "https://www.dgs.ca.gov/OPSC/Meetings"),
    Agency("ctc", "Commission on Teacher Credentialing", "CTC",
           "https://meetings.ctc.ca.gov/"),
    Agency("csfa", "California School Finance Authority", "CSFA",
           "https://www.treasurer.ca.gov/csfa/meeting"),
    Agency("fcc", "Federal Communications Commission", "FCC",
           "https://www.fcc.gov/news-events/events/open-commission-meetings"),
    Agency("calpers", "CalPERS Board", "CalPERS",
           "https://www.calpers.ca.gov/about/board/board-meetings"),
    Agency("calstrs", "CalSTRS Board", "CalSTRS",
           "https://www.calstrs.com/board-meetings"),
    Agency("cpag", "California Practitioners Advisory Group", "CPAG",
           "https://www.cde.ca.gov/be/cc/cp/"),
    Agency("fppc", "Fair Political Practices Commission", "FPPC",
           "https://www.fppc.ca.gov/about-fppc/commission-hearings/"),
    Agency("ccee", "California Collaborative for Educational Excellence", "CCEE",
           "https://ccee-ca.org/governing-board/meetings/"),
    Agency("eaap", "Educational Audit Appeals Panel", "EAAP",
           "https://eaap.ca.gov/panel/agendas/"),
    Agency("ag_opinions", "Attorney General Legal Opinions", "AG Opinions",
           "https://oag.ca.gov/opinions/monthly-report"),
    Agency("ag_initiatives", "Attorney General Ballot Initiatives", "AG Initiatives",
           "https://oag.ca.gov/initiatives/active-measures"),
]

ABGT = "https://abgt.assembly.ca.gov"
SBUD = "https://sbud.senate.ca.gov"
ASM, SEN, DOF, LAO = "Assembly Budget", "Senate Budget", "Department of Finance", "Legislative Analyst's Office"

ASSEMBLY_SUBCOMMITTEES = [
    (1, "Health", "subcommittee-no-1-health/sub-1-hearing-agendas"),
    (2, "Human Services", "subcommittee-no-2-human-services/sub-2-hearing-agendas"),
    (3, "Education Finance", "subcommittee-no-3-education-finance/sub-3-hearing-agendas"),
    (4, "Climate Crisis, Resources, Energy and Transportation",
     "subcommittee-no-4-climate-crisis-resources-energy-and-transportation/sub-4-hearing-0"),
    (5, "State Administration", "subcommittee-no-5-state-administration/sub-5-hearing-agendas"),
    (6, "Public Safety", "subcommittee-no-6-public-safety/sub-6-hearing-agendas"),
    (7, "Accountability and Oversight",
     "subcommittee-no-7-accountability-and-oversight/sub-7-hearing-agendas"),
]
SENATE_SUBCOMMITTEES = [
    (1, "Education", "subcommittee-1-education"),
    (2, "Resources, Environmental Protection and Energy",
     "subcommittee-2-resources-environmental-protection-and-energy"),
    (3, "Health and Human Services", "subcommittee-3-health-and-human-services"),
    (4, "State Administration and General Government",
     "subcommittee-4-state-administration-and-general-government"),
    (5, "Corrections, Public Safety, Judiciary, Labor and Transportation",
     "subcommittee-5-corrections-public-safety-judiciary-labor-and-transportation"),
]

BUDGET_AGENCIES: list[Agency] = [
    Agency("abgt_full", "Assembly Budget Committee", "Asm Budget",
           f"{ABGT}/welcome-assembly-committee-budget/full-budget-committee",
           "budget", ASM, may_be_empty=True),
    *[Agency(f"abgt_sub{n}", f"Assembly Budget Subcommittee No. {n} on {name}", f"Asm Sub {n}",
             f"{ABGT}/sub-committees/{path}", "budget", ASM, may_be_empty=True)
      for n, name, path in ASSEMBLY_SUBCOMMITTEES],
    Agency("abgt_reports", "Assembly Budget Committee reports", "Asm Reports", f"{ABGT}/",
           "budget", ASM),
    Agency("sbud_full", "Senate Budget and Fiscal Review Committee", "Sen Budget",
           f"{SBUD}/hearingagendas/full-committee-hearings", "budget", SEN, may_be_empty=True),
    *[Agency(f"sbud_sub{n}", f"Senate Budget Subcommittee No. {n} on {name}", f"Sen Sub {n}",
             f"{SBUD}/hearingagendas/{path}", "budget", SEN, may_be_empty=True)
      for n, name, path in SENATE_SUBCOMMITTEES],
    Agency("sbud_reports", "Senate Budget highlights and reports", "Sen Reports", f"{SBUD}/",
           "budget", SEN),
    Agency("dof_trailer_bills", "Trailer bill language", "DOF TBL",
           "https://dof.ca.gov/esdapp/trailer-bill/", "budget", DOF),
    Agency("dof_budget", "Governor's Budget, May Revision and Enacted Budget", "DOF Budget",
           "https://ebudget.ca.gov/home", "budget", DOF),
    Agency("dof_finance_letters", "Finance Letters", "DOF Letters",
           "https://dof.ca.gov/budget/budget-details/", "budget", DOF),
    Agency("lao_budget", "LAO budget publications", "LAO Budget", "https://www.lao.ca.gov/Budget",
           "budget", LAO),
    Agency("lao_education", "LAO education publications", "LAO Education",
           "https://www.lao.ca.gov/Policy-Areas?areaId=4", "budget", LAO),
]
AGENCIES += BUDGET_AGENCIES

PROGRAMS = ("education", "budget")
BY_ID = {a.id: a for a in AGENCIES}
