import { useState } from "react";
import { supabase } from "../supabase";
import type { PostingRow } from "../types";
import { fmtDate, fmtTime, must, useLoad } from "../util";

const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL as string | undefined)?.replace(/\/$/, "");

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** The team's hearing calendar: pick upcoming budget hearings, and subscribe to them in
 *  Outlook through a private feed address (one per person). */
export default function Calendar({ userId }: { userId: string }) {
  const [copied, setCopied] = useState(false);
  const { data, error, reload } = useLoad(async () => {
    const [hearings, feed] = await Promise.all([
      supabase.from("posting_status").select("*").eq("program", "budget").eq("kind", "meeting")
        .gte("meeting_date", today()).order("meeting_date").order("start_time", { nullsFirst: false }),
      supabase.from("calendar_feeds").select("token").eq("user_id", userId).maybeSingle(),
    ]);
    return {
      hearings: must(hearings) as PostingRow[],
      token: (must(feed) as { token: string } | null)?.token ?? null,
    };
  }, [userId]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return null;

  const feedUrl = data.token && SUPABASE_URL ? `${SUPABASE_URL}/functions/v1/calendar/${data.token}.ics` : null;
  const onCalendar = data.hearings.filter((h) => h.on_calendar);
  const others = data.hearings.filter((h) => !h.on_calendar);

  async function toggle(h: PostingRow) {
    const { error } = h.on_calendar
      ? await supabase.from("calendar_hearings").delete().eq("posting_id", h.id)
      : await supabase.from("calendar_hearings").insert({ posting_id: h.id });
    if (error) alert(error.message); else reload();
  }
  async function createFeed(regenerate: boolean) {
    if (regenerate && !confirm("Make a new address? The old one stops working, so you'll need to "
      + "subscribe again in Outlook with the new one.")) return;
    if (regenerate) await supabase.from("calendar_feeds").delete().eq("user_id", userId);
    const { error } = await supabase.from("calendar_feeds").insert({ user_id: userId });
    if (error) alert(error.message); else reload();
  }
  async function copy() {
    if (!feedUrl) return;
    try { await navigator.clipboard.writeText(feedUrl); setCopied(true); } catch { /* select it by hand */ }
  }

  function row(h: PostingRow) {
    return (
      <li key={h.id}>
        <div className="row-main">
          <b>{fmtDate(h.meeting_date)}{h.start_time && `, ${fmtTime(h.start_time)}`}</b>
          <span className="badge agency" title={h.agency_name}>{h.agency_short_name}</span>
          <a href={`#/posting/${h.id}`} className="title">{h.title}</a>
        </div>
        <div className="row-meta">
          {h.location && <span className="muted">{h.location}</span>}
          {h.matched_keywords.map((k) => <span key={k} className="badge kw">{k}</span>)}
          <button className={`small${h.on_calendar ? " secondary" : ""}`} onClick={() => toggle(h)}>
            {h.on_calendar ? "Remove from calendar" : "Add to calendar"}
          </button>
        </div>
      </li>
    );
  }

  return (
    <>
      <h1>Hearing calendar</h1>
      <p className="muted">
        Hearings added here appear in the Outlook calendar of everyone on the team who has subscribed, with
        their agenda items in the event description. When a committee revises an agenda or moves a hearing,
        the event updates the next time Outlook refreshes the calendar (usually within a few hours).
      </p>

      <section className="review-box">
        <h2>Your Outlook subscription</h2>
        {!SUPABASE_URL ? (
          <p className="error">The dashboard isn't configured with the Supabase address.</p>
        ) : feedUrl ? (
          <>
            <div className="feed-url">
              <input readOnly value={feedUrl} onFocus={(e) => e.target.select()} aria-label="Calendar address" />
              <button className="small" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            </div>
            <ol className="small-text">
              <li>In Outlook on the web, open <b>Calendar</b>, then choose <b>Add calendar</b> → <b>Subscribe from web</b>.</li>
              <li>Paste the address above, name it (e.g. “CA Budget Hearings”) and choose <b>Import</b>.</li>
              <li>Desktop Outlook: <b>Add calendar</b> → <b>From Internet…</b> and paste the same address.</li>
            </ol>
            <p className="muted small-text">
              This address is private to you: anyone who has it can see the team calendar.{" "}
              <button className="link" onClick={() => createFeed(true)}>Make a new address</button>
            </p>
          </>
        ) : (
          <button onClick={() => createFeed(false)}>Get my calendar address</button>
        )}
      </section>

      <h2>On the team calendar</h2>
      {onCalendar.length ? <ul className="postings">{onCalendar.map(row)}</ul>
        : <p className="empty">No upcoming hearings on the calendar yet. Add them from the list below.</p>}

      <h2>Other upcoming budget hearings</h2>
      {others.length ? <ul className="postings">{others.map(row)}</ul>
        : <p className="empty">No other upcoming hearings have been posted.</p>}
    </>
  );
}
