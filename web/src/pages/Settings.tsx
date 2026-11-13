import { useEffect, useState } from "react";
import { supabase } from "../supabase";
import type { Agency, Program } from "../types";
import { must, useLoad } from "../util";

interface Prefs {
  email_mode: "none" | "daily" | "immediate";
  agency_ids: string[] | null;
  only_keyword_hits: boolean;
  programs?: Program[] | null;  // null = both; missing before the budget migration
}

const PROGRAM_LABEL: Record<Program, string> = {
  education: "Education agencies",
  budget: "Budget (committees, Department of Finance, LAO)",
};

export default function Settings({ userId }: { userId: string }) {
  const { data, error } = useLoad(async () => {
    const [prefs, agencies, profile] = await Promise.all([
      supabase.from("user_prefs").select("*").eq("user_id", userId).maybeSingle(),
      supabase.from("agencies").select("*").order("name"),
      supabase.from("profiles").select("display_name, email").eq("id", userId).maybeSingle(),
    ]);
    return {
      prefs: (must(prefs) as Prefs | null) ?? { email_mode: "none", agency_ids: null, only_keyword_hits: false },
      agencies: must(agencies) as Agency[],
      profile: must(profile) as { display_name: string | null; email: string } | null,
    };
  }, [userId]);

  const [prefs, setPrefs] = useState<Prefs | null>(null);
  const [name, setName] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  useEffect(() => {
    if (data) { setPrefs(data.prefs); setName(data.profile?.display_name ?? ""); }
  }, [data]);

  if (error) return <p className="error">{error}</p>;
  if (!data || !prefs) return null;

  async function save() {
    const a = await supabase.from("user_prefs").upsert({ user_id: userId, ...prefs });
    const b = await supabase.from("profiles").update({ display_name: name || null }).eq("id", userId);
    setSaved(a.error?.message ?? b.error?.message ?? "Saved.");
  }

  const allAgencies = prefs.agency_ids === null;
  function toggleAgency(id: string, on: boolean) {
    const current = new Set(prefs!.agency_ids ?? data!.agencies.map((a) => a.id));
    if (on) current.add(id); else current.delete(id);
    setPrefs({ ...prefs!, agency_ids: [...current] });
  }

  const hasPrograms = prefs.programs !== undefined;
  const programs: Program[] = prefs.programs ?? ["education", "budget"];
  function toggleProgram(p: Program, on: boolean) {
    const next = on ? [...new Set([...programs, p])] : programs.filter((x) => x !== p);
    setPrefs({ ...prefs!, programs: next.length === 2 ? null : next });
  }
  const shownAgencies = data.agencies.filter((a) => programs.includes(a.program ?? "education"));

  return (
    <div className="narrow stack">
      <h1>Settings</h1>
      <label>
        Your name (shown on reviews)
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder={data.profile?.email} />
      </label>

      <fieldset>
        <legend>Email alerts to {data.profile?.email}</legend>
        {([
          ["none", "No email (dashboard only)"],
          ["daily", "Daily digest each morning"],
          ["immediate", "Immediately when something new is found"],
        ] as const).map(([v, label]) => (
          <label key={v} className="inline">
            <input type="radio" name="mode" checked={prefs.email_mode === v}
                   onChange={() => setPrefs({ ...prefs, email_mode: v })} /> {label}
          </label>
        ))}
        <label className="inline">
          <input type="checkbox" checked={prefs.only_keyword_hits}
                 onChange={(e) => setPrefs({ ...prefs, only_keyword_hits: e.target.checked })} />{" "}
          Only email about postings with keyword matches
        </label>
        <p className="muted small-text">
          The daily digest also lists hearings on the team calendar in the coming week and any source
          that needs attention.
        </p>
      </fieldset>

      {hasPrograms && (
        <fieldset>
          <legend>Email me about</legend>
          {(["education", "budget"] as const).map((p) => (
            <label key={p} className="inline">
              <input type="checkbox" checked={programs.includes(p)}
                     onChange={(e) => toggleProgram(p, e.target.checked)} /> {PROGRAM_LABEL[p]}
            </label>
          ))}
        </fieldset>
      )}

      <fieldset>
        <legend>Email me about these agencies and sources</legend>
        <label className="inline">
          <input type="checkbox" checked={allAgencies}
                 onChange={(e) => setPrefs({ ...prefs, agency_ids: e.target.checked ? null : data.agencies.map((a) => a.id) })} />{" "}
          All of them
        </label>
        {!allAgencies && shownAgencies.map((a) => (
          <label key={a.id} className="inline">
            <input type="checkbox" checked={prefs.agency_ids!.includes(a.id)}
                   onChange={(e) => toggleAgency(a.id, e.target.checked)} /> {a.name}
          </label>
        ))}
      </fieldset>

      <button onClick={save}>Save settings</button>
      {saved && <p className="muted">{saved}</p>}
    </div>
  );
}
