export type Status = "new" | "updated" | "reviewed" | "baseline";

export interface Agency {
  id: string;
  name: string;
  short_name: string;
  url: string;
  active: boolean;
  scraper_ready: boolean;
  last_checked_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  consecutive_failures: number;
  last_posting_count: number | null;
}

export interface PostingRow {
  id: number;
  agency_id: string;
  agency_short_name: string;
  agency_name: string;
  kind: "meeting" | "opinion" | "initiative";
  title: string;
  meeting_date: string | null;
  url: string | null;
  first_seen_at: string;
  last_changed_at: string;
  status: Status;
  last_reviewed_at: string | null;
  last_reviewed_by_email: string | null;
  last_reviewed_by_name: string | null;
  match_count: number;
  matched_keywords: string[];
}

export interface Keyword {
  id: number;
  term: string;
  match_type: "word" | "phrase" | "regex";
  agency_id: string | null;
  active: boolean;
  created_at: string;
}

export interface Item {
  id: number;
  position: number;
  number: string | null;
  title: string;
  description: string | null;
}

export interface Doc {
  id: number;
  item_position: number | null;
  url: string;
  label: string | null;
  fetch_error: string | null;
  text: string | null;
  sha256: string | null;
}

export interface Match {
  id: number;
  keyword_id: number;
  item_id: number | null;
  document_id: number | null;
  source: "title" | "item" | "document";
  hit_count: number;
  snippet: string;
}

export interface Review {
  id: number;
  user_id: string;
  reviewed_at: string;
  note: string | null;
  profiles: { email: string; display_name: string | null } | null;
}
