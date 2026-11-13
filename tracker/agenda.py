"""Split a budget hearing agenda (text extracted from the PDF) into agenda items.

Budget agendas come in a few shapes, tried in this order:

1. Issue headings: "Issue 3: Workforce Pell Implementation", under department lines
   such as "6980 – Student Aid Commission" (most Assembly and many Senate agendas). Issue
   numbers restart in some agendas, so items keep their department in the number:
   "6980 · Issue 3". Only body department lines count, not the contents list (whose
   lines end in page numbers); several in a row are one group of departments.
2. A table of contents of departments: "6100 Department of Education ........ 1" (Senate
   vote-only and action agendas).
3. A bill list: "AB 109 Gabriel Budget Act of 2026" (full committee bill hearings).
4. A short numbered order of business: "2. Overview of the 2026 Budget Legislative
   Agreement", leaving out procedural lines (call to order, public comment...).

An agenda that fits none of these gives no items; its full text is still searchable.
"""

from __future__ import annotations

import re

from tracker.models import Item

ISSUE = re.compile(r"^\s*Issue\s+(\d{1,3}[A-Za-z]?)\s*([:.\-–—])\s*(.+?)\s*$", re.IGNORECASE)
# A department heading in the body: "6100 California Department of Education" or
# "6870 – California Community Colleges". Contents lines end in a page number instead.
DEPARTMENT = re.compile(r"^\s*(\d{4})\s*[–—-]?\s+([A-Z][A-Za-z.,&'’()\-/ ]{3,90}?)\s*$")
DEPT_TOC = re.compile(r"^\s*(\d{4})\s+(.+?)\s*\.{4,}\s*\d+\s*$")
BILL = re.compile(r"^\s*((?:AB|SB|ACA|SCA|AJR|SJR)\s*\d+)\s+(\S+)\s+(.{4,}?)\s*$")
NUMBERED = re.compile(r"^\s*([IVX]{1,5}|\d{1,2})\.\s+([A-Z].{3,}?)\s*$")
PROCEDURAL = re.compile(
    r"call to order|roll call|public comment|adjourn|welcome|opening remarks|introductions?$"
    r"|pledge|member comments|closing remarks|establish(ment of)? quorum", re.IGNORECASE)
NOT_A_DEPARTMENT = re.compile(r"\b(street|room|rm\.?|suite|the budget|proposes|million|billion)\b",
                              re.IGNORECASE)
PAGE_NUMBER = re.compile(r"\s+\d{1,3}$")
DOT_LEADER = re.compile(r"\s*\.{4,}\s*\d*\s*$")
# Only the first part of an agenda is the order of business; later numbered lines are
# usually lists inside the background text.
NUMBERED_SCAN_CHARS = 4000
MAX_ITEMS = 150


def _department(line: str) -> tuple[str, str] | None:
    m = DEPARTMENT.match(line)
    if not m:
        return None
    name = m[2].strip().rstrip(".").strip()
    if len(name.split()) > 10 or NOT_A_DEPARTMENT.search(name) or name.startswith(("The ", "and ")):
        return None
    return m[1], name


def split_agenda(text: str | None) -> list[Item]:
    if not text:
        return []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    for strategy in (_issues, _department_toc, _bills, _numbered):
        items = strategy(lines)
        if items:
            return items[:MAX_ITEMS]
    return []


def _issues(lines: list[str]) -> list[Item]:
    # (separator, issue number, departments [(code, name)], title)
    found: list[tuple[str, str, list[tuple[str, str]], str]] = []
    depts: list[tuple[str, str]] = []
    prev_was_dept = False
    for line in lines:
        d = _department(line)
        if d:
            depts = (depts if prev_was_dept else []) + [d]
            prev_was_dept = True
            continue
        prev_was_dept = False
        m = ISSUE.match(line)
        if m:
            sep, title = m[2], m[3].strip()
            if DOT_LEADER.search(title):  # "Issue 1: Overview ........ 3" is a contents line
                sep, title = ".", DOT_LEADER.sub("", title).strip()
            elif sep == ".":
                title = PAGE_NUMBER.sub("", title).strip()
            found.append((sep, m[1], depts, title))
    if not found:
        return []
    # Headings in the body use "Issue 1:"; the contents list uses "Issue 1." with a page
    # number. Prefer the body; fall back to the contents if that's all there is.
    body = [f for f in found if f[0] != "."] or found
    items: list[Item] = []
    seen: set[tuple] = set()
    for _, number, depts, title in body:
        code = depts[0][0] if len(depts) == 1 else None
        key = (code, number.lower(), title.lower())
        if key in seen:
            continue
        seen.add(key)
        label = f"{code} · Issue {number}" if code else f"Issue {number}"
        description = "; ".join(f"{c} {n}" for c, n in depts) or None
        items.append(Item(title=title, number=label, description=description))
    return items


def _department_toc(lines: list[str]) -> list[Item]:
    items, seen = [], set()
    for line in lines:
        m = DEPT_TOC.match(line)
        if m and m[1] not in seen:
            seen.add(m[1])
            items.append(Item(title=m[2].rstrip(". ").strip(), number=m[1]))
    return items


def _bills(lines: list[str]) -> list[Item]:
    items, seen = [], set()
    for line in lines:
        if line.strip().lower().startswith("bill no"):
            break  # the bill analyses that follow the list
        m = BILL.match(line)
        if m:
            number = re.sub(r"\s+", " ", m[1])
            if number not in seen:
                seen.add(number)
                items.append(Item(title=m[3], number=number, description=f"Author: {m[2]}"))
    return items


def _numbered(lines: list[str]) -> list[Item]:
    items, used = [], 0
    for line in lines:
        used += len(line) + 1
        if used > NUMBERED_SCAN_CHARS:
            break
        m = NUMBERED.match(line)
        if m and not PROCEDURAL.search(m[2]):
            items.append(Item(title=m[2], number=m[1]))
    return items
