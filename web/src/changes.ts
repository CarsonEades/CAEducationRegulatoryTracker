// What changed in a posting since it was last reviewed, from the snapshot the database
// keeps each time a posting changes (posting_versions) compared with how it looks now.
import type { Doc, Item, Keyword, Match } from "./types";

export interface Snapshot {
  title: string;
  meeting_date: string | null;
  items: { position: number; number: string | null; title: string; description: string | null }[];
  documents: { url: string; label: string | null; item_position: number | null; sha256: string | null }[];
  keyword_hits: { term: string; source: string; item_position: number | null; document_url: string | null }[];
}

export interface Version {
  changed_at: string;
  snapshot: Snapshot;
}

export interface Changes {
  title: string | null;          // the old title, if it changed
  meetingDate: { from: string | null } | null;  // the old date, if it changed
  addedItems: Item[];
  changedItems: Item[];
  removedItems: Snapshot["items"];
  addedDocs: Doc[];
  revisedDocs: Doc[];
  removedDocs: Snapshot["documents"];
  newHits: { term: string; where: string }[];
  // For badges on the page.
  itemStatus: Map<number, "new" | "changed">;
  docStatus: Map<number, "new" | "revised">;
}

// Items are matched by their number ("5", "W-07") when they have one, otherwise by title,
// since positions shift when an item is inserted.
const itemKey = (i: { number: string | null; title: string }) =>
  i.number ? `#${i.number.trim()}` : `t:${i.title.trim()}`;

export function snapshotAtReview(versions: Version[], reviewedAt: string | null): Snapshot | null {
  if (!reviewedAt) return null;
  const at = Date.parse(reviewedAt);
  let best: Version | null = null;
  for (const v of versions) {
    if (Date.parse(v.changed_at) <= at && (!best || Date.parse(v.changed_at) > Date.parse(best.changed_at))) {
      best = v;
    }
  }
  return best?.snapshot ?? null;
}

export function diff(
  before: Snapshot,
  now: { title: string; meeting_date: string | null; items: Item[]; docs: Doc[]; matches: Match[] },
  keywords: Map<number, Keyword>,
): Changes {
  const oldItems = new Map(before.items.map((i) => [itemKey(i), i]));
  const newKeys = new Set(now.items.map(itemKey));
  const itemStatus = new Map<number, "new" | "changed">();
  const addedItems: Item[] = [];
  const changedItems: Item[] = [];
  for (const i of now.items) {
    const old = oldItems.get(itemKey(i));
    if (!old) {
      addedItems.push(i);
      itemStatus.set(i.id, "new");
    } else if (old.title !== i.title || (old.description ?? "") !== (i.description ?? "")) {
      changedItems.push(i);
      itemStatus.set(i.id, "changed");
    }
  }
  const removedItems = before.items.filter((i) => !newKeys.has(itemKey(i)));

  const oldDocs = new Map(before.documents.map((d) => [d.url, d]));
  const newUrls = new Set(now.docs.map((d) => d.url));
  const docStatus = new Map<number, "new" | "revised">();
  const addedDocs: Doc[] = [];
  const revisedDocs: Doc[] = [];
  for (const d of now.docs) {
    const old = oldDocs.get(d.url);
    if (!old) {
      addedDocs.push(d);
      docStatus.set(d.id, "new");
    } else if (old.sha256 && d.sha256 && old.sha256 !== d.sha256) {
      // Only a real change in contents; a document that was simply downloaded later
      // (it had no copy before) isn't a revision.
      revisedDocs.push(d);
      docStatus.set(d.id, "revised");
    }
  }
  const removedDocs = before.documents.filter((d) => !newUrls.has(d.url));

  const hitKey = (term: string, source: string, pos: number | null, url: string | null) =>
    `${term}|${source}|${pos ?? ""}|${url ?? ""}`;
  const oldHits = new Set(before.keyword_hits.map((h) => hitKey(h.term, h.source, h.item_position, h.document_url)));
  const seen = new Set<string>();
  const newHits: Changes["newHits"] = [];
  for (const m of now.matches) {
    const term = keywords.get(m.keyword_id)?.term;
    if (!term) continue;
    const item = now.items.find((i) => i.id === m.item_id);
    const doc = now.docs.find((d) => d.id === m.document_id);
    const key = hitKey(term, m.source, item?.position ?? null, doc?.url ?? null);
    if (oldHits.has(key) || seen.has(key)) continue;
    seen.add(key);
    const where = m.source === "title" ? "the title"
      : item ? `item ${item.number ?? item.position + 1}`
      : doc ? (doc.label || doc.url.split("/").pop() || "a document") : "the posting";
    newHits.push({ term, where });
  }

  return {
    title: before.title !== now.title ? before.title : null,
    meetingDate: (before.meeting_date ?? null) !== (now.meeting_date ?? null)
      ? { from: before.meeting_date ?? null } : null,
    addedItems, changedItems, removedItems, addedDocs, revisedDocs, removedDocs, newHits,
    itemStatus, docStatus,
  };
}

export function isEmpty(c: Changes): boolean {
  return !c.title && !c.meetingDate && !c.addedItems.length && !c.changedItems.length
    && !c.removedItems.length && !c.addedDocs.length && !c.revisedDocs.length
    && !c.removedDocs.length && !c.newHits.length;
}
