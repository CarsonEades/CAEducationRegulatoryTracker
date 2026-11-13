import { useState } from "react";
import { supabase } from "../supabase";
import type { Agency, PostingRow, Program } from "../types";
import { StatusBadge, fmtDate, must, useLoad } from "../util";

interface Hit {
  posting_id: number;
  source: "title" | "item" | "document";
  headline: string;
  rank: number;
}

// ts_headline marks matches with <b>…</b>; render those as <mark> without trusting other HTML.
function Headline({ html }: { html: string }) {
  const parts = html.split(/<\/?b>/);
  return <>{parts.map((t, i) => (i % 2 ? <mark key={i}>{t}</mark> : <span key={i}>{t}</span>))}</>;
}

export default function Search({ program }: { program: Program }) {
  const [q, setQ] = useState("");
  const [agency, setAgency] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [results, setResults] = useState<{ posting: PostingRow; hits: Hit[] }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const agencies = useLoad(async () => {
    const all = must(await supabase.from("agencies").select("*").order("name")) as Agency[];
    return all.filter((a) => (a.program ?? "education") === program);
  }, [program]);

  async function search(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setErr(null);
    try {
      const args = { q, p_agency_id: agency || null, p_from: from || null, p_to: to || null };
      let res = await supabase.rpc("search_all", { ...args, p_program: program });
      // Before the budget migration search_all has no program parameter.
      if (res.error && program === "education" && /p_program|search_all/.test(res.error.message)) {
        res = await supabase.rpc("search_all", args);
      }
      const hits = must(res) as Hit[];
      const ids = [...new Set(hits.map((h) => h.posting_id))];
      const postings = ids.length
        ? (must(await supabase.from("posting_status").select("*").in("id", ids)) as PostingRow[])
        : [];
      const byId = new Map(postings.map((p) => [p.id, p]));
      setResults(ids.filter((id) => byId.has(id)).map((id) => ({
        posting: byId.get(id)!, hits: hits.filter((h) => h.posting_id === id).slice(0, 3),
      })));
    } catch (e) {
      setErr(String((e as Error).message));
    }
    setLoading(false);
  }

  return (
    <>
      <h1>Search</h1>
      <form className="search-form" onSubmit={search}>
        <input placeholder='e.g. charter renewal, "material revision", facilities -lease'
               value={q} onChange={(e) => setQ(e.target.value)} required />
        <select value={agency} onChange={(e) => setAgency(e.target.value)}>
          <option value="">{program === "budget" ? "All committees and sources" : "All agencies"}</option>
          {(agencies.data ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <label>From <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label>To <input type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <button disabled={loading}>Search</button>
      </form>
      <p className="muted small-text">
        Searches every collected {program === "budget" ? "hearing agenda, agenda item, report and bill" : "agenda, item and document"}.
        Use quotes for exact phrases and - to exclude words.
      </p>
      {err && <p className="error">{err}</p>}
      {results && results.length === 0 && <p className="empty">No results.</p>}
      <ul className="postings">
        {results?.map(({ posting: p, hits }) => (
          <li key={p.id}>
            <div className="row-main">
              <StatusBadge status={p.status} />
              <span className="badge">{p.agency_short_name}</span>
              <a href={`#/posting/${p.id}`} className="title">{p.title}</a>
              {p.meeting_date && <span className="muted">{fmtDate(p.meeting_date)}</span>}
            </div>
            {hits.map((h, i) => (
              <blockquote key={i}><span className="muted">{h.source}: </span><Headline html={h.headline} /></blockquote>
            ))}
          </li>
        ))}
      </ul>
    </>
  );
}
