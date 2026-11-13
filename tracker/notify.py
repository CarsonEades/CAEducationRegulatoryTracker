"""Email alerts: immediate, daily digest, and scraper-health alerts to admins.

Emails go through Resend (https://resend.com). Without RESEND_API_KEY nothing is
sent and nothing is marked as sent, so alerts are not lost while email is unset.
"""

from __future__ import annotations

import html
import logging
import os
from collections import defaultdict

import httpx
import psycopg

log = logging.getLogger(__name__)

# Don't email about events older than this. Stops a flood of old alerts
# when someone first turns on email.
IMMEDIATE_LOOKBACK = "2 days"
DIGEST_LOOKBACK = "26 hours"


def dashboard_url() -> str:
    return os.environ.get("DASHBOARD_URL", "").rstrip("/")


def send_email(to: list[str], subject: str, html_body: str) -> bool:
    key = os.environ.get("RESEND_API_KEY")
    sender = os.environ.get("EMAIL_FROM")
    if not key or not sender:
        log.warning("RESEND_API_KEY / EMAIL_FROM not set; not sending %r", subject)
        return False
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {key}"},
        json={"from": sender, "to": to, "subject": subject, "html": html_body},
        timeout=30,
    )
    if resp.status_code >= 300:
        log.error("email to %s failed: %s %s", to, resp.status_code, resp.text[:500])
        return False
    return True


def pending_events(conn: psycopg.Connection, user_id, lookback: str) -> list[dict]:
    """Events this user hasn't been emailed about, filtered by their preferences."""
    rows = conn.execute(
        """
        select e.id, e.kind, e.created_at, p.id, p.title, p.meeting_date, p.url,
               a.short_name, a.name, a.program, a.group_name
        from posting_events e
        join postings p on p.id = e.posting_id
        join agencies a on a.id = p.agency_id
        join user_prefs up on up.user_id = %(uid)s
        where e.created_at > now() - %(lookback)s::interval
          and not exists (select 1 from notifications_sent n
                          where n.user_id = %(uid)s and n.event_id = e.id)
          and (up.agency_ids is null or p.agency_id = any (up.agency_ids))
          and (up.programs is null or a.program = any (up.programs))
          and (not up.only_keyword_hits
               or exists (select 1 from matches m where m.posting_id = p.id))
        order by a.program, a.group_name nulls first, a.short_name, p.meeting_date nulls last, e.id
        """,
        {"uid": user_id, "lookback": lookback},
    ).fetchall()
    events = []
    for eid, kind, created, pid, title, mdate, url, agency_short, agency_name, program, group in rows:
        hits = conn.execute(
            """select k.term, m.snippet from matches m join keywords k on k.id = m.keyword_id
               where m.posting_id = %s order by k.term, m.source limit 6""",
            (pid,),
        ).fetchall()
        events.append(dict(event_id=eid, kind=kind, posting_id=pid, title=title,
                           meeting_date=mdate, url=url, agency=agency_short,
                           agency_name=agency_name, program=program, group=group, hits=hits))
    # One email line per posting even if it changed several times.
    latest: dict[int, dict] = {}
    for e in events:
        prev = latest.get(e["posting_id"])
        e["event_ids"] = (prev["event_ids"] if prev else []) + [e["event_id"]]
        if prev and prev["kind"] == "new":
            e["kind"] = "new"
        latest[e["posting_id"]] = e
    return list(latest.values())


def upcoming_hearings(conn: psycopg.Connection, user_id, days: int = 7) -> list[dict]:
    """Hearings on the team calendar in the next `days` days, for users who get budget email."""
    rows = conn.execute(
        """select p.id, p.title, p.meeting_date, p.start_time, p.location, a.name
           from calendar_hearings c
           join postings p on p.id = c.posting_id
           join agencies a on a.id = p.agency_id
           join user_prefs up on up.user_id = %s
           where p.meeting_date between current_date and current_date + %s
             and (up.programs is null or a.program = any (up.programs))
           order by p.meeting_date, p.start_time nulls last""",
        (user_id, days),
    ).fetchall()
    return [dict(posting_id=r[0], title=r[1], meeting_date=r[2], start_time=r[3], location=r[4],
                 agency_name=r[5]) for r in rows]


def sources_needing_attention(conn: psycopg.Connection, user_id) -> list[dict]:
    """Sources that failed their last check, or whose last check left a warning."""
    rows = conn.execute(
        """select a.name, a.consecutive_failures, a.last_error, a.last_note
           from agencies a join user_prefs up on up.user_id = %s
           where a.active and a.scraper_ready
             and (a.consecutive_failures > 0 or a.last_note is not null)
             and (up.programs is null or a.program = any (up.programs))
           order by a.consecutive_failures desc, a.name""",
        (user_id,),
    ).fetchall()
    return [dict(name=r[0], failures=r[1], error=r[2], note=r[3]) for r in rows]


def render(events: list[dict], heading: str, hearings: list[dict] | None = None,
           attention: list[dict] | None = None) -> str:
    """Events grouped by agency (budget sources under their group, e.g. "Assembly Budget:
    Subcommittee No. 3"), then the upcoming calendar hearings and any unhealthy sources."""
    by_agency: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        name = e["agency_name"]
        if e.get("group") and not name.startswith(e["group"].split()[0]):
            name = f"{e['group']}: {name}"
        by_agency[name].append(e)
    base = dashboard_url()
    out = [f"<h2 style='font-family:sans-serif'>{html.escape(heading)}</h2>"]
    if not events:
        out.append("<p style='font-family:sans-serif'>No new or updated postings.</p>")
    for agency, evs in by_agency.items():
        out.append(f"<h3 style='font-family:sans-serif;margin-bottom:4px'>{html.escape(agency)}</h3><ul>")
        for e in evs:
            label = "NEW" if e["kind"] == "new" else "UPDATED"
            date = e["meeting_date"].strftime("%b %d, %Y") if e["meeting_date"] else ""
            link = f"{base}/#/posting/{e['posting_id']}" if base else (e["url"] or "")
            out.append(
                f"<li style='font-family:sans-serif;margin-bottom:8px'><b>[{label}]</b> "
                f"<a href='{html.escape(link)}'>{html.escape(e['title'])}</a> {html.escape(date)}"
            )
            if e["hits"]:
                out.append("<ul>")
                for term, snippet in e["hits"]:
                    out.append(f"<li><b>{html.escape(term)}</b>: <i>{html.escape(snippet)}</i></li>")
                out.append("</ul>")
            out.append("</li>")
        out.append("</ul>")
    if hearings:
        out.append("<h3 style='font-family:sans-serif;margin-bottom:4px'>Hearings on the team calendar, "
                   "next 7 days</h3><ul>")
        for h in hearings:
            when = h["meeting_date"].strftime("%a %b %d")
            if h["start_time"]:
                when += " " + h["start_time"].strftime("%I:%M %p").lstrip("0")
            link = f"{base}/#/posting/{h['posting_id']}" if base else ""
            title = (f"<a href='{html.escape(link)}'>{html.escape(h['title'])}</a>" if link
                     else html.escape(h["title"]))
            where = f", {html.escape(h['location'])}" if h["location"] else ""
            out.append(f"<li style='font-family:sans-serif;margin-bottom:6px'><b>{html.escape(when)}</b> "
                       f"{html.escape(h['agency_name'])}: {title}{where}</li>")
        out.append("</ul>")
    if attention:
        out.append("<h3 style='font-family:sans-serif;margin-bottom:4px'>Sources needing attention</h3><ul>")
        for a in attention:
            detail = (f"failed {a['failures']} check(s): {a['error'] or ''}" if a["failures"]
                      else a["note"] or "")
            out.append(f"<li style='font-family:sans-serif'><b>{html.escape(a['name'])}</b>: "
                       f"{html.escape(detail[:300])}</li>")
        out.append("</ul>")
    if base:
        out.append(f"<p style='font-family:sans-serif'><a href='{html.escape(base)}'>Open the tracker</a></p>")
    return "\n".join(out)


def subject_for(events: list[dict], prefix: str) -> str:
    hits = sum(1 for e in events if e["hits"])
    agencies = sorted({e["agency"] for e in events})
    s = f"{prefix}: {len(events)} new/updated posting{'s' if len(events) != 1 else ''}"
    if hits:
        s += f", {hits} with keyword hits"
    return s + f" ({', '.join(agencies[:4])}{'…' if len(agencies) > 4 else ''})"


def send_user_alerts(conn: psycopg.Connection, mode: str) -> int:
    """mode: 'immediate' or 'daily'. Returns the number of emails sent."""
    lookback = IMMEDIATE_LOOKBACK if mode == "immediate" else DIGEST_LOOKBACK
    users = conn.execute(
        """select pr.id, pr.email from user_prefs up join profiles pr on pr.id = up.user_id
           where up.email_mode = %s""",
        (mode,),
    ).fetchall()
    sent = 0
    for uid, email in users:
        events = pending_events(conn, uid, lookback)
        hearings = upcoming_hearings(conn, uid) if mode == "daily" else []
        if not events and not hearings:
            continue
        attention = sources_needing_attention(conn, uid) if mode == "daily" else []
        prefix = "Agency tracker" if mode == "immediate" else "Agency tracker daily digest"
        heading = "New agency postings" if mode == "immediate" else "Last 24 hours"
        subject = (subject_for(events, prefix) if events
                   else f"{prefix}: {len(hearings)} hearing(s) on the calendar this week")
        if send_email([email], subject, render(events, heading, hearings, attention)):
            for e in events:
                for eid in e["event_ids"]:
                    conn.execute(
                        """insert into notifications_sent (user_id, event_id, channel)
                           values (%s, %s, %s) on conflict do nothing""",
                        (uid, eid, mode),
                    )
            conn.commit()
            sent += 1
    return sent


def send_health_alerts(results) -> None:
    """Email admins when an agency first reaches the failure threshold."""
    from tracker.agencies import BY_ID
    from tracker.pipeline import FAILURE_ALERT_THRESHOLD

    admins = [a.strip() for a in os.environ.get("ADMIN_EMAILS", "").split(",") if a.strip()]
    broken = [r for r in results if r.consecutive_failures == FAILURE_ALERT_THRESHOLD]
    if not admins or not broken:
        return
    lines = "".join(
        f"<li><b>{html.escape(BY_ID[r.agency_id].name)}</b>: {html.escape(r.error or '')}</li>"
        for r in broken
    )
    send_email(
        admins,
        f"Agency tracker: {len(broken)} scraper(s) failing",
        f"<p style='font-family:sans-serif'>These agencies have failed "
        f"{FAILURE_ALERT_THRESHOLD} checks in a row, so their pages may have changed. "
        f"Check them by hand until the scraper is fixed.</p><ul>{lines}</ul>",
    )
