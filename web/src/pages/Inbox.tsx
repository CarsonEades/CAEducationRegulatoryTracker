import { useState } from "react";
import { supabase } from "../supabase";
import type { Agency, PostingRow, Program } from "../types";
import { StatusBadge, dateLabel, fmtDate, fmtDateTime, fmtTime, must, programPath, useLoad } from "../util";

type View = "needs-review" | "recent" | "upcoming" | "keyword-hits" | "all";

const RECENT_LIMIT = 200;

/** The date a posting is listed under in Recent items: its own date (the meeting or
 *  hearing date for agendas; the publication date for reports, letters and bills; issued or
 *  filed date for AG opinions and initiatives), or, when the source gives none, the day the
 *  tracker first found it. */
function recentDate(p: PostingRow): string {
  return p.meeting_date ?? p.first_seen_at.slice(0, 10);
}

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** A posting_status query for one program. Until the budget migration has run there's no
 *  program column, so education pages fall back to an unfiltered query. */
async function query(program: Program, build: (q: any) => any): Promise<PostingRow[]> {
  const res = await build(supabase.from("posting_status").select("*").eq("program", program));
  if (res.error && program === "education" && /program/.test(res.error.message)) {
    return must(await build(supabase.from("posting_status").select("*"))) as PostingRow[];
  }
  return must(res) as PostingRow[];
}

/** All of a program's postings in one list, newest date first. Postings without a date
 *  are fetched separately so they can be placed by the day they were found. */
async function recentPostings(program: Program, agency: string): Promise<PostingRow[]> {
  const scoped = (q: any) => (agency ? q.eq("agency_id", agency) : q);
  const [dated, undated] = await Promise.all([
    query(program, (q) => scoped(q).not("meeting_date", "is", null).order("meeting_date", { ascending: false })
      .order("first_seen_at", { ascending: false }).limit(RECENT_LIMIT)),
    query(program, (q) => scoped(q).is("meeting_date", null).order("first_seen_at", { ascending: false })
      .limit(RECENT_LIMIT)),
  ]);
  return [...dated, ...undated]
    .sort((a, b) => recentDate(b).localeCompare(recentDate(a))
      || Date.parse(b.first_seen_at) - Date.parse(a.first_seen_at))
    .slice(0, RECENT_LIMIT);
}

export default function Inbox({ program, agencyFilter }: { program: Program; agencyFilter?: string }) {
  const [view, setView] = useState<View>("needs-review");
  const [agency, setAgency] = useState(agencyFilter ?? "");
  const [busy, setBusy] = useState<number | null>(null);
  const budget = program === "budget";

  const agencies = useLoad(async () => {
    const all = must(await supabase.from("agencies").select("*").order("name")) as Agency[];
    // Without the program column (before the budget migration) everything is education.
    return all.filter((a) => (a.program ?? "education") === program);
  }, [program]);
  const postings = useLoad(async () => {
    const scoped = (q: any) => (agency ? q.eq("agency_id", agency) : q);
    if (view === "recent") return recentPostings(program, agency);
    if (view === "upcoming") {
      return query(program, (q) => scoped(q).eq("kind", "meeting").gte("meeting_date", today())
        .order("meeting_date").order("start_time", { nullsFirst: false }).limit(200));
    }
    return query(program, (q) => {
      q = scoped(q);
      if (view === "needs-review") q = q.in("status", ["new", "updated"]);
      if (view === "keyword-hits") q = q.gt("match_count", 0);
      return q.order("meeting_date", { ascending: false, nullsFirst: true })
        .order("first_seen_at", { ascending: false }).limit(view === "needs-review" ? 500 : 200);
    });
  }, [view, agency, program]);

  async function markReviewed(id: number) {
    setBusy(id);
    const { error } = await supabase.from("reviews").insert({ posting_id: id });
    setBusy(null);
    if (error) alert(error.message);
    else postings.reload();
  }

  // Grouped views: by agency, or for the budget by committee under its group
  // (Assembly Budget, Senate Budget, Department of Finance, LAO), in agency-list order.
  const flat = view === "recent" || view === "upcoming";
  const order = new Map((agencies.data ?? []).map((a, i) => [a.id, i]));
  const groups = new Map<string, { heading: string; group?: string | null; rows: PostingRow[] }>();
  const rows = flat ? [] : [...(postings.data ?? [])].sort((a, b) =>
    (a.group_name ?? "").localeCompare(b.group_name ?? "")
    || (order.get(a.agency_id) ?? 0) - (order.get(b.agency_id) ?? 0));
  for (const p of rows) {
    const g = groups.get(p.agency_id) ?? { heading: p.agency_name, group: p.group_name, rows: [] };
    g.rows.push(p);
    groups.set(p.agency_id, g);
  }

  function renderRow(p: PostingRow, showAgency: boolean) {
    return (
      <li key={p.id}>
        <div className="row-main">
          <StatusBadge status={p.status} />
          {showAgency && <span className="badge agency" title={p.agency_name}>{p.agency_short_name}</span>}
          <a href={`#/posting/${p.id}`} className="title">{p.title}</a>
          {p.meeting_date && (
            <span className="muted" title={dateLabel(p.kind, p.program)}>
              {budget && <>{dateLabel(p.kind, p.program)} </>}{fmtDate(p.meeting_date)}
              {p.start_time && <> · {fmtTime(p.start_time)}</>}
            </span>
          )}
          {p.on_calendar && <span className="badge cal" title="On the team hearing calendar">On calendar</span>}
        </div>
        <div className="row-meta">
          {showAgency && !p.meeting_date && <span className="muted">Found {fmtDateTime(p.first_seen_at)}</span>}
          {p.matched_keywords.map((k) => <span key={k} className="badge kw">{k}</span>)}
          {p.status === "reviewed" && (
            <span className="muted">
              Reviewed by {p.last_reviewed_by_name || p.last_reviewed_by_email} {fmtDate(p.last_reviewed_at)}
            </span>
          )}
          {!!p.note_count && (
            <a href={`#/posting/${p.id}`} className="muted">{p.note_count} note{p.note_count > 1 ? "s" : ""}</a>
          )}
          {p.url && <a href={p.url} target="_blank" rel="noreferrer">Source ↗</a>}
          {(p.status === "new" || p.status === "updated") && (
            <button className="small" disabled={busy === p.id} onClick={() => markReviewed(p.id)}>
              Mark reviewed
            </button>
          )}
        </div>
      </li>
    );
  }

  const failing = (agencies.data ?? []).filter((a) => a.consecutive_failures >= 3);
  const views: [View, string][] = budget
    ? [["needs-review", "Needs review"], ["recent", "All items by date"], ["upcoming", "Upcoming hearings"],
       ["keyword-hits", "Keyword hits"], ["all", "By committee"]]
    : [["needs-review", "Needs review"], ["recent", "Recent items"], ["keyword-hits", "Keyword hits"],
       ["all", "All"]];
  const byGroup = new Map<string, Agency[]>();
  for (const a of agencies.data ?? []) {
    const g = a.group_name ?? "";
    byGroup.set(g, [...(byGroup.get(g) ?? []), a]);
  }
  let lastGroup: string | null | undefined = undefined;

  return (
    <>
      <div className="toolbar">
        <div className="segmented">
          {views.map(([v, label]) => (
            <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>{label}</button>
          ))}
        </div>
        <select value={agency} onChange={(e) => setAgency(e.target.value)}>
          <option value="">{budget ? "All committees and sources" : "All agencies"}</option>
          {[...byGroup].map(([g, list]) => g
            ? <optgroup key={g} label={g}>{list.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</optgroup>
            : list.map((a) => <option key={a.id} value={a.id}>{a.name}</option>))}
        </select>
      </div>

      {failing.length > 0 && (
        <p className="warning">
          Not updating, so check these by hand: {failing.map((a) => a.short_name).join(", ")}.{" "}
          <a href={programPath(program, budget ? "health" : "agencies")}>Details</a>
        </p>
      )}
      {postings.error && <p className="error">{postings.error}</p>}
      {postings.data && postings.data.length === 0 && (
        <p className="empty">
          {view === "needs-review" ? "Nothing to review. You're caught up."
            : view === "upcoming" ? "No upcoming hearings posted yet." : "No postings."}
        </p>
      )}

      {flat && postings.data && postings.data.length > 0 && (
        <ul className="postings">{postings.data.map((p) => renderRow(p, true))}</ul>
      )}

      {[...groups].map(([agencyId, g]) => {
        const groupHeading = budget && g.group !== lastGroup ? g.group : null;
        lastGroup = g.group;
        return (
          <section key={agencyId} className="group">
            {groupHeading && <h2 className="group-name">{groupHeading}</h2>}
            <h3>
              <a href={programPath(program, `agency/${agencyId}`)}>{g.heading}</a>{" "}
              <span className="count">{g.rows.length}</span>
            </h3>
            <ul className="postings">
              {g.rows.map((p) => renderRow(p, false))}
            </ul>
          </section>
        );
      })}
    </>
  );
}
