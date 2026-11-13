import { Fragment, useState } from "react";
import { supabase } from "../supabase";
import type { Agency, CrawlLogRow, Program } from "../types";
import { fmtDateTime, must, programPath, useLoad } from "../util";

/** Health of each source: whether its last checks worked, what it found, and any warnings
 *  (e.g. an agenda whose items couldn't be read). Each row expands to the requests made
 *  for that source, broken down by request type. */
export default function Agencies({ program }: { program: Program }) {
  const [open, setOpen] = useState<string | null>(null);
  const budget = program === "budget";
  const { data, error } = useLoad(async () => {
    const all = must(await supabase.from("agencies").select("*").order("name")) as Agency[];
    const agencies = all.filter((a) => (a.program ?? "education") === program);
    const open = must(await supabase.from("posting_status").select("agency_id")
      .in("status", ["new", "updated"]).in("agency_id", agencies.map((a) => a.id))) as { agency_id: string }[];
    const counts = new Map<string, number>();
    for (const r of open) counts.set(r.agency_id, (counts.get(r.agency_id) ?? 0) + 1);
    return { agencies, counts };
  }, [program]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return null;

  const groups = new Map<string, Agency[]>();
  for (const a of data.agencies) {
    const g = a.group_name ?? "";
    groups.set(g, [...(groups.get(g) ?? []), a]);
  }
  const problems = data.agencies.filter((a) => a.consecutive_failures > 0 || a.last_note).length;

  return (
    <>
      <h1>{budget ? "Tracker health" : "Agencies"}</h1>
      <p className="muted">
        {problems === 0 ? "Every source's last check worked." : `${problems} source(s) need a look.`}{" "}
        A source is <b>failing</b> after 3 failed checks in a row (admins are emailed); check it by hand until
        it's fixed. <b>Warnings</b> mean the check worked but something looked off. Click a source to see its
        recent requests.
      </p>
      <table className="agencies">
        <thead>
          <tr><th>Source</th><th>To review</th><th>Last checked</th><th>Last successful</th><th>Found</th><th>Health</th></tr>
        </thead>
        <tbody>
          {[...groups].map(([group, list]) => (
            <Fragment key={group || "none"}>
              {group && <tr className="group-row"><th colSpan={6}>{group}</th></tr>}
              {list.map((a) => (
                <Fragment key={a.id}>
                  <tr className="clickable" onClick={() => setOpen(open === a.id ? null : a.id)}>
                    <td>
                      <a href={programPath(program, `agency/${a.id}`)} onClick={(e) => e.stopPropagation()}>{a.name}</a>{" "}
                      <a href={a.url} target="_blank" rel="noreferrer" className="muted"
                         onClick={(e) => e.stopPropagation()}>site ↗</a>
                    </td>
                    <td>{data.counts.get(a.id) ?? 0}</td>
                    <td>{fmtDateTime(a.last_checked_at)}</td>
                    <td>{fmtDateTime(a.last_success_at)}</td>
                    <td>{a.last_posting_count ?? "–"}</td>
                    <td>
                      <HealthBadge a={a} />
                      {a.last_error && a.consecutive_failures > 0 && <div className="muted small-text">{a.last_error}</div>}
                      {a.last_note && <div className="muted small-text">{a.last_note}</div>}
                    </td>
                  </tr>
                  {open === a.id && (
                    <tr className="details"><td colSpan={6}><RequestLog agencyId={a.id} /></td></tr>
                  )}
                </Fragment>
              ))}
            </Fragment>
          ))}
        </tbody>
      </table>
    </>
  );
}

function HealthBadge({ a }: { a: Agency }) {
  if (!a.scraper_ready) return <span className="badge status-baseline">Not automated yet: check by hand</span>;
  if (a.consecutive_failures >= 3) return <span className="badge bad" title={a.last_error ?? ""}>Failing: check by hand</span>;
  if (a.consecutive_failures > 0) return <span className="badge warn" title={a.last_error ?? ""}>Last check failed</span>;
  if (!a.last_success_at) return <span className="muted">Waiting for first run</span>;
  if (a.last_note) return <span className="badge warn">OK, with warnings</span>;
  return <span className="badge ok">OK</span>;
}

function RequestLog({ agencyId }: { agencyId: string }) {
  const { data, error } = useLoad(async () => {
    const since = new Date(Date.now() - 14 * 86400e3).toISOString();
    const res = await supabase.from("crawl_log")
      .select("id, url, requested_at, gap_seconds, outcome, detail, request_type, status")
      .eq("agency_id", agencyId).gte("requested_at", since)
      .order("requested_at", { ascending: false }).limit(300);
    // Before the budget migration crawl_log has no agency_id; say so instead of failing.
    if (res.error && /agency_id/.test(res.error.message)) return "not-set-up" as const;
    return must(res) as CrawlLogRow[];
  }, [agencyId]);

  if (error) return <p className="error">{error}</p>;
  if (data === null) return null;
  if (data === "not-set-up") return <p className="muted">Request logging isn't set up in the database yet.</p>;
  if (data.length === 0) return <p className="muted">No requests logged in the last 14 days.</p>;

  const stats = new Map<string, { ok: number; challenged: number; error: number }>();
  for (const r of data) {
    const t = r.request_type ?? "other";
    const s = stats.get(t) ?? { ok: 0, challenged: 0, error: 0 };
    s[r.outcome] += 1;
    stats.set(t, s);
  }
  return (
    <div className="request-log">
      <p>
        <b>Last 14 days:</b>{" "}
        {[...stats].map(([t, s]) => (
          <span key={t} className="stat">
            {t === "page" ? "Web pages" : t === "document" ? "Documents" : t === "api" ? "Data feeds" : "Other"}:{" "}
            {s.ok} accepted{s.challenged ? `, ${s.challenged} challenged` : ""}{s.error ? `, ${s.error} failed` : ""}
          </span>
        ))}
      </p>
      <table className="compact">
        <thead><tr><th>When</th><th>Type</th><th>Result</th><th>Gap</th><th>Address</th></tr></thead>
        <tbody>
          {data.slice(0, 50).map((r) => (
            <tr key={r.id} className={r.outcome !== "ok" ? "bad-row" : ""}>
              <td>{fmtDateTime(r.requested_at)}</td>
              <td>{r.request_type ?? ""}</td>
              <td title={r.detail ?? ""}>{r.outcome}{r.status ? ` (${r.status})` : ""}</td>
              <td>{r.gap_seconds == null ? "" : r.gap_seconds < 120 ? `${r.gap_seconds}s` : `${Math.round(r.gap_seconds / 60)}m`}</td>
              <td className="url"><a href={r.url} target="_blank" rel="noreferrer">{r.url.replace(/^https?:\/\//, "")}</a>
                {r.detail && r.outcome !== "ok" && <div className="muted small-text">{r.detail}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
