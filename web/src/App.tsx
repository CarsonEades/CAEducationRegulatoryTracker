import { useEffect, useState } from "react";
import type { Session } from "@supabase/supabase-js";
import { configured, supabase } from "./supabase";
import type { Program } from "./types";
import Inbox from "./pages/Inbox";
import Agencies from "./pages/Agencies";
import PostingPage from "./pages/Posting";
import Keywords from "./pages/Keywords";
import Search from "./pages/Search";
import Settings from "./pages/Settings";
import Calendar from "./pages/Calendar";
import Login from "./pages/Login";

// Hash routes (#/posting/12) so the site works on GitHub Pages without server rewrites.
function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

const NAV: Record<Program, (readonly [string, string])[]> = {
  education: [
    ["", "Inbox"],
    ["agencies", "Agencies"],
    ["search", "Search"],
    ["keywords", "Keywords"],
    ["settings", "Settings"],
  ],
  budget: [
    ["budget", "Inbox"],
    ["budget/health", "Health"],
    ["budget/calendar", "Calendar"],
    ["budget/search", "Search"],
    ["keywords", "Keywords"],
    ["settings", "Settings"],
  ],
};

const TITLES: Record<Program, string> = {
  education: "CA Education Agency Tracker",
  budget: "CA Budget Tracker",
};

// The section last used, so shared pages (a posting, keywords, settings) keep their nav.
function savedProgram(): Program {
  try {
    return localStorage.getItem("program") === "budget" ? "budget" : "education";
  } catch {
    return "education";
  }
}

export default function App() {
  const [session, setSession] = useState<Session | null | undefined>(undefined);
  const route = useRoute();
  const [program, setProgram] = useState<Program>(savedProgram);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => setSession(data.session));
    const { data } = supabase.auth.onAuthStateChange((_e, s) => setSession(s));
    return () => data.subscription.unsubscribe();
  }, []);

  // Budget pages live under #/budget; the education pages are everything else except the
  // shared ones, which keep whichever section was in use.
  const shared = ["posting", "keywords", "settings"].includes(route[0] ?? "");
  const routeProgram: Program = route[0] === "budget" ? "budget" : "education";
  useEffect(() => {
    if (shared) return;
    setProgram(routeProgram);
    try { localStorage.setItem("program", routeProgram); } catch { /* private mode */ }
  }, [routeProgram, shared]);

  if (!configured) {
    return (
      <main className="narrow">
        <h1>Agency Tracker</h1>
        <p className="error">
          Not configured: set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY (see web/.env.example).
        </p>
      </main>
    );
  }
  if (session === undefined) return null;
  if (!session) return <Login />;

  const userId = session.user.id;
  let body;
  let current = route.join("/");
  if (route[0] === "budget") {
    const [, page, arg] = route;
    switch (page ?? "") {
      case "health": body = <Agencies program="budget" />; break;
      case "calendar": body = <Calendar userId={userId} />; break;
      case "search": body = <Search program="budget" />; break;
      case "agency": body = <Inbox key={`b-${arg}`} program="budget" agencyFilter={arg} />; current = "budget"; break;
      default: body = <Inbox key="budget" program="budget" />; current = "budget";
    }
  } else {
    const [page, arg] = route;
    switch (page ?? "") {
      case "agencies": body = <Agencies program="education" />; break;
      case "posting": body = <PostingPage id={Number(arg)} userId={userId} />; break;
      case "keywords": body = <Keywords program={program} />; break;
      case "search": body = <Search program="education" />; break;
      case "settings": body = <Settings userId={userId} />; break;
      case "agency": body = <Inbox key={`e-${arg}`} program="education" agencyFilter={arg} />; current = ""; break;
      default: body = <Inbox key="education" program="education" />; current = "";
    }
  }

  return (
    <>
      <header className="topbar">
        <a href={program === "budget" ? "#/budget" : "#/"} className="brand">{TITLES[program]}</a>
        <div className="segmented program-switch" role="group" aria-label="Section">
          <a href="#/" className={program === "education" ? "on" : ""}>Education agencies</a>
          <a href="#/budget" className={program === "budget" ? "on" : ""}>Budget</a>
        </div>
        <nav>
          {NAV[program].map(([path, label]) => (
            <a key={path} href={`#/${path}`} className={current === path ? "active" : ""}>
              {label}
            </a>
          ))}
        </nav>
        <button className="link" onClick={() => supabase.auth.signOut()}>
          Sign out
        </button>
      </header>
      <main>{body}</main>
    </>
  );
}
