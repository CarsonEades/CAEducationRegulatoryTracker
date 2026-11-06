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

type ItemLike = { number: string | null; title: string; description: string | null };
const norm = (v: string | null) => (v ?? "").trim();
// An item's identity for matching keyword hits between versions.
const itemId = (i: ItemLike) => `${norm(i.number)}|${norm(i.title)}`;

/** Pair each current item with its earlier version. Item numbers aren't unique on every
 *  agency's agendas (CalSTRS restarts them in each committee), so items are matched by
 *  number, title and description together first, then by number and title, and by number
 *  alone only when exactly one item on each side still has that number. */
function matchItems<O extends ItemLike, N extends ItemLike>(before: O[], now: N[]) {
  const unmatchedOld = new Set(before.map((_, k) => k));
  const pairs = new Map<number, O>(); // index in now -> old item
  const pass = (key: (i: ItemLike) => string) => {
    const pool = new Map<string, number[]>();
    for (const k of unmatchedOld) {
      const list = pool.get(key(before[k])) ?? [];
      list.push(k);
      pool.set(key(before[k]), list);
    }
    now.forEach((i, n) => {
      if (pairs.has(n)) return;
      const k = pool.get(key(i))?.shift();
      if (k === undefined) return;
      pairs.set(n, before[k]);
      unmatchedOld.delete(k);
    });
  };
  pass((i) => `${norm(i.number)}|${norm(i.title)}|${norm(i.description)}`);
  pass((i) => `${norm(i.number)}|${norm(i.title)}`);
  // By number alone, only where it's unambiguous on both sides.
  const left = now.map((_, n) => n).filter((n) => !pairs.has(n));
  for (const n of left) {
    const num = norm(now[n].number);
    if (!num) continue;
    const olds = [...unmatchedOld].filter((k) => norm(before[k].number) === num);
    const news = left.filter((m) => !pairs.has(m) && norm(now[m].number) === num);
    if (olds.length === 1 && news.length === 1) {
      pairs.set(n, before[olds[0]]);
      unmatchedOld.delete(olds[0]);
    }
  }
  return { pairs, removed: [...unmatchedOld].sort((a, b) => a - b).map((k) => before[k]) };
}

const fileName = (url: string) => decodeURIComponent(url.split("?")[0].split("/").pop() ?? "").toLowerCase();

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
  const { pairs, removed: removedItems } = matchItems(before.items, now.items);
  const itemStatus = new Map<number, "new" | "changed">();
  const addedItems: Item[] = [];
  const changedItems: Item[] = [];
  now.items.forEach((i, n) => {
    const old = pairs.get(n);
    if (!old) {
      addedItems.push(i);
      itemStatus.set(i.id, "new");
    } else if (norm(old.title) !== norm(i.title) || norm(old.description) !== norm(i.description)) {
      changedItems.push(i);
      itemStatus.set(i.id, "changed");
    }
  });

  // Which item a document belongs to, as an identity that survives items moving.
  const oldItemAt = new Map(before.items.map((i) => [i.position, itemId(i)]));
  const newItemAt = new Map(now.items.map((i) => [i.position, itemId(i)]));
  const oldDocs = new Map(before.documents.map((d) => [d.url, d]));
  const newUrls = new Set(now.docs.map((d) => d.url));
  const goneDocs = before.documents.filter((d) => !newUrls.has(d.url));
  const docStatus = new Map<number, "new" | "revised">();
  const addedDocs: Doc[] = [];
  const revisedDocs: Doc[] = [];
  for (const d of now.docs) {
    const old = oldDocs.get(d.url);
    if (old) {
      // Only a real change in contents; a document that was simply downloaded later
      // (it had no copy before) isn't a revision.
      if (old.sha256 && d.sha256 && old.sha256 !== d.sha256) {
        revisedDocs.push(d);
        docStatus.set(d.id, "revised");
      }
      continue;
    }
    // Some agencies (CalSTRS) post a new version at a new address. A removed document on
    // the same item with the same file name, or the same label, is the earlier version.
    const item = d.item_position == null ? "" : newItemAt.get(d.item_position) ?? "";
    const sameItem = goneDocs.filter((g) =>
      (g.item_position == null ? "" : oldItemAt.get(g.item_position) ?? "") === item);
    const byName = sameItem.filter((g) => fileName(g.url) === fileName(d.url));
    const byLabel = sameItem.filter((g) => norm(g.label) && norm(g.label) === norm(d.label));
    const previous = byName.length === 1 ? byName[0] : byLabel.length === 1 ? byLabel[0] : null;
    if (previous) {
      goneDocs.splice(goneDocs.indexOf(previous), 1);
      revisedDocs.push(d);
      docStatus.set(d.id, "revised");
    } else {
      addedDocs.push(d);
      docStatus.set(d.id, "new");
    }
  }
  const removedDocs = goneDocs;

  // Keyword hits are compared by keyword, where (title, item or document) and which item,
  // so items moving don't make existing hits look new.
  const hitKey = (term: string, source: string, item: string, file: string) => `${term}|${source}|${item}|${file}`;
  const oldHits = new Set(before.keyword_hits.map((h) => hitKey(h.term, h.source,
    h.item_position == null ? "" : oldItemAt.get(h.item_position) ?? "",
    h.document_url ? fileName(h.document_url) : "")));
  const seen = new Set<string>();
  const newHits: Changes["newHits"] = [];
  for (const m of now.matches) {
    const term = keywords.get(m.keyword_id)?.term;
    if (!term) continue;
    const item = now.items.find((i) => i.id === m.item_id);
    const doc = now.docs.find((d) => d.id === m.document_id);
    const key = hitKey(term, m.source, item ? itemId(item) : "", doc ? fileName(doc.url) : "");
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
