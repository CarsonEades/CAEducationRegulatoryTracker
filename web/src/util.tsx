import { useCallback, useEffect, useState } from "react";
import type { Keyword, Status } from "./types";

export function fmtDate(d: string | null): string {
  if (!d) return "";
  // Dates without a time are calendar dates; don't shift them by time zone.
  const date = d.length === 10 ? new Date(d + "T12:00:00") : new Date(d);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

/** "09:30:00" -> "9:30 AM". */
export function fmtTime(t: string | null | undefined): string {
  if (!t) return "";
  const [h, m] = t.split(":").map(Number);
  return new Date(2000, 0, 1, h, m).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

/** What a posting's date means, for labels: hearings and meetings vs. publications. */
export function dateLabel(kind: string, program?: string): string {
  if (kind === "publication") return "Published";
  if (kind === "meeting") return program === "budget" ? "Hearing" : "Meeting";
  return "Dated";
}

/** Program-specific route prefix: "#/budget/..." for budget, "#/..." for education. */
export function programPath(program: string | undefined, path: string): string {
  return program === "budget" ? `#/budget${path ? "/" + path : ""}` : `#/${path}`;
}

export function fmtDateTime(d: string | null): string {
  if (!d) return "never";
  return new Date(d).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

const STATUS_LABEL: Record<Status, string> = {
  new: "New", updated: "Updated since review", reviewed: "Reviewed", baseline: "Already posted",
};

export function StatusBadge({ status }: { status: Status }) {
  return <span className={`badge status-${status}`}>{STATUS_LABEL[status]}</span>;
}

// Mirrors keyword_pattern() in the database closely enough for highlighting.
export function keywordRegex(k: Pick<Keyword, "term" | "match_type">): RegExp | null {
  try {
    if (k.match_type === "regex") {
      return new RegExp(k.term.replace(/\\y/g, "\\b").replace(/\\m|\\M/g, "\\b"), "gi");
    }
    const body = k.term.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+");
    return new RegExp(`\\b${body}${k.match_type === "word" ? "(s|es|'s)?" : ""}\\b`, "gi");
  } catch {
    return null;
  }
}

export function Highlight({ text, patterns }: { text: string; patterns: (RegExp | null)[] }) {
  const ranges: [number, number][] = [];
  for (const re of patterns) {
    if (!re) continue;
    re.lastIndex = 0;
    for (const m of text.matchAll(re)) {
      if (m[0].length) ranges.push([m.index!, m.index! + m[0].length]);
    }
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const out: React.ReactNode[] = [];
  let pos = 0;
  for (const [s, e] of ranges) {
    if (s < pos) continue;
    out.push(text.slice(pos, s), <mark key={s}>{text.slice(s, e)}</mark>);
    pos = e;
  }
  out.push(text.slice(pos));
  return <>{out}</>;
}

/** Load data with a reload function; errors are shown by the caller. */
export function useLoad<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const run = useCallback(() => {
    load().then(setData, (e) => setError(String(e?.message ?? e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(run, [run]);
  return { data, error, reload: run };
}

/** Throw Supabase errors so useLoad can show them. */
export function must<T>(res: { data: T | null; error: { message: string } | null }): T {
  if (res.error) throw new Error(res.error.message);
  return res.data as T;
}
