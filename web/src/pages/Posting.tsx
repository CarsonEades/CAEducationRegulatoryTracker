import { useState } from "react";
import { supabase } from "../supabase";
import type { Doc, Item, Keyword, Match, Note, PostingRow, Review } from "../types";
import {
  Highlight, StatusBadge, dateLabel, fmtDate, fmtDateTime, fmtTime, keywordRegex, must, programPath, useLoad,
} from "../util";
import { type Changes, type Version, diff, isEmpty, snapshotAtReview } from "../changes";

export default function PostingPage({ id, userId }: { id: number; userId: string }) {
  const [note, setNote] = useState("");
  const [onlyHits, setOnlyHits] = useState(false);

  const { data, error, reload } = useLoad(async () => {
    const [posting, items, docs, matches, reviews, keywords, versions, notes] = await Promise.all([
      supabase.from("posting_status").select("*").eq("id", id).single(),
      supabase.from("items").select("*").eq("posting_id", id).order("position"),
      supabase.from("documents").select("id, item_position, url, label, fetch_error, text, sha256")
        .eq("posting_id", id).order("id"),
      supabase.from("matches").select("*").eq("posting_id", id),
      supabase.from("reviews").select("*, profiles(email, display_name)").eq("posting_id", id)
        .order("reviewed_at", { ascending: false }),
      supabase.from("keywords").select("*"),
      supabase.from("posting_versions").select("changed_at, snapshot").eq("posting_id", id),
      supabase.from("posting_notes").select("*, profiles(email, display_name)").eq("posting_id", id)
        .order("created_at"),
    ]);
    return {
      posting: must(posting) as PostingRow,
      items: must(items) as Item[],
      docs: must(docs) as Doc[],
      matches: must(matches) as Match[],
      reviews: must(reviews) as Review[],
      keywords: must(keywords) as Keyword[],
      // Missing until the database has the history table; the page works without it.
      versions: versions.error ? null : (versions.data as Version[]),
      // Likewise for shared notes (added with budget tracking).
      notes: notes.error ? null : (notes.data as Note[]),
    };
  }, [id]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return null;
  const { posting: p, items, docs, matches, reviews, keywords, versions, notes } = data;

  const kwById = new Map(keywords.map((k) => [k.id, k]));
  // Only for "Updated since review": what changed since the last review.
  const reviewedSnapshot = p.status === "updated" && versions
    ? snapshotAtReview(versions, p.last_reviewed_at) : null;
  const changes = reviewedSnapshot
    ? diff(reviewedSnapshot, { title: p.title, meeting_date: p.meeting_date, items, docs, matches }, kwById)
    : null;
  const patterns = [...new Set(matches.map((m) => m.keyword_id))]
    .map((k) => kwById.get(k)).filter(Boolean).map((k) => keywordRegex(k!));
  const matchedItems = new Set(matches.filter((m) => m.item_id).map((m) => m.item_id));
  const docsByItem = new Map<number, Doc[]>();
  for (const d of docs) {
    if (d.item_position == null) continue;
    docsByItem.set(d.item_position, [...(docsByItem.get(d.item_position) ?? []), d]);
  }
  const generalDocs = docs.filter((d) => d.item_position == null);

  async function review() {
    const { error } = await supabase.from("reviews").insert({ posting_id: id, note: note || null });
    if (error) alert(error.message);
    else { setNote(""); reload(); }
  }
  async function toggleCalendar() {
    const { error } = p.on_calendar
      ? await supabase.from("calendar_hearings").delete().eq("posting_id", id)
      : await supabase.from("calendar_hearings").insert({ posting_id: id });
    if (error) alert(error.message);
    else reload();
  }
  async function undo(reviewId: number) {
    const { error } = await supabase.from("reviews").delete().eq("id", reviewId);
    if (error) alert(error.message);
    else reload();
  }

  return (
    <article className="posting">
      <p>
        <a href={programPath(p.program, `agency/${p.agency_id}`)}>
          ← {p.group_name && !p.agency_name.startsWith(p.group_name.split(" ")[0]) ? `${p.group_name}: ` : ""}{p.agency_name}
        </a>
      </p>
      <h1><Highlight text={p.title} patterns={patterns} /></h1>
      <p className="row-meta">
        <StatusBadge status={p.status} />
        {p.meeting_date && (
          <span>
            {dateLabel(p.kind, p.program)} {fmtDate(p.meeting_date)}
            {p.start_time && <>, {fmtTime(p.start_time)}</>}
            {p.location && <> · {p.location}</>}
          </span>
        )}
        <span className="muted">First seen {fmtDateTime(p.first_seen_at)}</span>
        {p.last_changed_at !== p.first_seen_at && <span className="muted">Changed {fmtDateTime(p.last_changed_at)}</span>}
        {p.url && <a href={p.url} target="_blank" rel="noreferrer">Open on agency site ↗</a>}
        {p.kind === "meeting" && p.on_calendar !== undefined && (
          <button className="small secondary" onClick={toggleCalendar}
                  title="Hearings on the team calendar appear in everyone's subscribed Outlook calendar">
            {p.on_calendar ? "✓ On team calendar (remove)" : "Add to team calendar"}
          </button>
        )}
      </p>

      {p.status === "updated" && versions && (
        <ChangesSinceReview changes={changes} reviewedAt={p.last_reviewed_at}
                            reviewedBy={p.last_reviewed_by_name || p.last_reviewed_by_email} />
      )}

      <section className="review-box">
        <textarea placeholder="Optional note (e.g. 'Item 5 is the Acme appeal, flagged for legal')"
                  value={note} onChange={(e) => setNote(e.target.value)} />
        <button onClick={review}>{p.status === "reviewed" ? "Add note / review again" : "Mark reviewed"}</button>
        {reviews.length > 0 && (
          <ul className="reviews">
            {reviews.map((r) => (
              <li key={r.id}>
                <b>{r.profiles?.display_name || r.profiles?.email}</b> reviewed {fmtDateTime(r.reviewed_at)}
                {r.note && <>: {r.note}</>}
                {r.user_id === userId && <button className="link" onClick={() => undo(r.id)}>undo</button>}
              </li>
            ))}
          </ul>
        )}
      </section>

      {notes && <Notes postingId={id} notes={notes} userId={userId} onChange={reload} />}

      {matches.length > 0 && (
        <section>
          <h2>Keyword matches</h2>
          <ul className="matches">
            {matches.map((m) => {
              const doc = docs.find((d) => d.id === m.document_id);
              const item = items.find((i) => i.id === m.item_id);
              return (
                <li key={m.id}>
                  <span className="badge kw">{kwById.get(m.keyword_id)?.term}</span>{" "}
                  <span className="muted">
                    {m.source === "title" && "in title"}
                    {item && <>in item <a href={`#item-${item.id}`} onClick={(e) => { e.preventDefault(); document.getElementById(`item-${item.id}`)?.scrollIntoView(); }}>{item.number ?? item.position + 1}</a></>}
                    {doc && <>in <a href={doc.url} target="_blank" rel="noreferrer">{doc.label || "document"}</a></>}
                    {m.hit_count > 1 && ` (${m.hit_count}×)`}
                  </span>
                  <blockquote><Highlight text={m.snippet} patterns={patterns} /></blockquote>
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {items.length > 0 && (
        <section>
          <h2>Agenda items</h2>
          <label className="muted inline">
            <input type="checkbox" checked={onlyHits} onChange={(e) => setOnlyHits(e.target.checked)} />{" "}
            Only items with keyword hits
          </label>
          <ol className="items">
            {items.filter((i) => !onlyHits || matchedItems.has(i.id)).map((i) => (
              <li key={i.id} id={`item-${i.id}`}
                  className={[matchedItems.has(i.id) ? "hit" : "", changes?.itemStatus.has(i.id) ? "changed" : ""].join(" ").trim()}>
                <div>
                  {changes?.itemStatus.get(i.id) && (
                    <span className="badge status-updated">
                      {changes.itemStatus.get(i.id) === "new" ? "New since review" : "Changed since review"}
                    </span>
                  )}{" "}
                  <b>{i.number}</b> <Highlight text={i.title} patterns={patterns} />
                </div>
                {i.description && <p><Highlight text={i.description} patterns={patterns} /></p>}
                {(docsByItem.get(i.position) ?? []).map((d) => <DocLink key={d.id} d={d} change={changes?.docStatus.get(d.id)} />)}
              </li>
            ))}
          </ol>
        </section>
      )}

      {generalDocs.length > 0 && (
        <section>
          <h2>Documents</h2>
          <ul>{generalDocs.map((d) => <li key={d.id}><DocLink d={d} change={changes?.docStatus.get(d.id)} /></li>)}</ul>
        </section>
      )}
    </article>
  );
}

/** Shared notes: anyone on the team can read them; you can edit or delete your own. Adding a
 *  note doesn't mark the posting reviewed. */
function Notes({ postingId, notes, userId, onChange }: {
  postingId: number; notes: Note[]; userId: string; onChange: () => void;
}) {
  const [body, setBody] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [editBody, setEditBody] = useState("");

  async function add() {
    if (!body.trim()) return;
    const { error } = await supabase.from("posting_notes").insert({ posting_id: postingId, body: body.trim() });
    if (error) alert(error.message);
    else { setBody(""); onChange(); }
  }
  async function save(n: Note) {
    const { error } = await supabase.from("posting_notes")
      .update({ body: editBody.trim(), edited_at: new Date().toISOString() }).eq("id", n.id);
    if (error) alert(error.message);
    else { setEditing(null); onChange(); }
  }
  async function remove(n: Note) {
    if (!confirm("Delete this note?")) return;
    const { error } = await supabase.from("posting_notes").delete().eq("id", n.id);
    if (error) alert(error.message);
    else onChange();
  }

  return (
    <section className="notes">
      <h2>Team notes {notes.length > 0 && <span className="count">{notes.length}</span>}</h2>
      {notes.length > 0 && (
        <ul className="note-list">
          {notes.map((n) => (
            <li key={n.id}>
              <div className="muted small-text">
                <b>{n.profiles?.display_name || n.profiles?.email}</b> · {fmtDateTime(n.created_at)}
                {n.edited_at && " (edited)"}
                {n.user_id === userId && editing !== n.id && (
                  <>
                    <button className="link" onClick={() => { setEditing(n.id); setEditBody(n.body); }}>edit</button>
                    <button className="link danger" onClick={() => remove(n)}>delete</button>
                  </>
                )}
              </div>
              {editing === n.id ? (
                <div className="stack-tight">
                  <textarea value={editBody} onChange={(e) => setEditBody(e.target.value)} />
                  <div>
                    <button className="small" onClick={() => save(n)} disabled={!editBody.trim()}>Save</button>{" "}
                    <button className="small secondary" onClick={() => setEditing(null)}>Cancel</button>
                  </div>
                </div>
              ) : (
                <p className="note-body">{n.body}</p>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="stack-tight">
        <textarea placeholder="Add a note for the team (e.g. 'Issue 3 affects charter facility grants; flag for Monday')"
                  value={body} onChange={(e) => setBody(e.target.value)} />
        <div><button className="small" onClick={add} disabled={!body.trim()}>Add note</button></div>
      </div>
    </section>
  );
}

function DocLink({ d, change }: { d: Doc; change?: "new" | "revised" }) {
  return (
    <div className="doc">
      {change && (
        <><span className="badge status-updated">{change === "new" ? "New since review" : "Revised since review"}</span>{" "}</>
      )}
      <a href={d.url} target="_blank" rel="noreferrer">{d.label || d.url.split("/").pop()}</a>
      {d.fetch_error && <span className="muted" title={d.fetch_error}> (text not searchable: {d.fetch_error})</span>}
    </div>
  );
}

function scrollToItem(id: number) {
  document.getElementById(`item-${id}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
}

function ChangesSinceReview({ changes, reviewedAt, reviewedBy }: {
  changes: Changes | null; reviewedAt: string | null; reviewedBy: string | null;
}) {
  const since = <>since {reviewedBy ? `${reviewedBy}'s review` : "the last review"} on {fmtDateTime(reviewedAt)}</>;
  if (!changes) {
    return (
      <section className="changes">
        <h2>What changed {since}</h2>
        <p className="muted">
          This change happened before the tracker started keeping a history of each agenda, so it
          can't list the specific changes. Check the agenda by hand this time; changes from now on
          will be listed here.
        </p>
      </section>
    );
  }
  const itemLabel = (i: { number: string | null; title: string }) => (i.number ? `${i.number}: ` : "") + i.title;
  return (
    <section className="changes">
      <h2>What changed {since}</h2>
      {isEmpty(changes) ? (
        <p className="muted">No differences in the agenda items, documents or keyword matches. The
          change may have been to something the tracker doesn't show, such as a document link's label.</p>
      ) : (
        <ul>
          {changes.title && <li>Title changed (was “{changes.title}”)</li>}
          {changes.meetingDate && <li>Date changed (was {changes.meetingDate.from ? fmtDate(changes.meetingDate.from) : "not listed"})</li>}
          {changes.addedItems.map((i) => (
            <li key={`a${i.id}`}><b>New item</b>{" "}
              <a href={`#item-${i.id}`} onClick={(e) => { e.preventDefault(); scrollToItem(i.id); }}>{itemLabel(i)}</a></li>
          ))}
          {changes.changedItems.map((i) => (
            <li key={`c${i.id}`}><b>Changed item</b>{" "}
              <a href={`#item-${i.id}`} onClick={(e) => { e.preventDefault(); scrollToItem(i.id); }}>{itemLabel(i)}</a></li>
          ))}
          {changes.removedItems.map((i) => (
            <li key={`r${i.position}`}><b>Removed item</b> <s>{itemLabel(i)}</s></li>
          ))}
          {changes.addedDocs.map((d) => (
            <li key={`ad${d.id}`}><b>New document</b>{" "}
              <a href={d.url} target="_blank" rel="noreferrer">{d.label || d.url.split("/").pop()}</a></li>
          ))}
          {changes.revisedDocs.map((d) => (
            <li key={`rd${d.id}`}><b>Revised document</b>{" "}
              <a href={d.url} target="_blank" rel="noreferrer">{d.label || d.url.split("/").pop()}</a></li>
          ))}
          {changes.removedDocs.map((d) => (
            <li key={`xd${d.url}`}><b>Removed document</b> <s>{d.label || d.url.split("/").pop()}</s></li>
          ))}
          {changes.newHits.map((h) => (
            <li key={`h${h.term}${h.where}`}><b>New keyword match</b> <span className="badge kw">{h.term}</span> in {h.where}</li>
          ))}
        </ul>
      )}
    </section>
  );
}
