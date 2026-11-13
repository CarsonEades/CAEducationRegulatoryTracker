// Private hearing calendar feed for Outlook ("Add calendar → Subscribe from web").
//
//   https://<project>.supabase.co/functions/v1/calendar?token=<token>
//   https://<project>.supabase.co/functions/v1/calendar/<token>.ics
//
// Each team member gets their own token on the dashboard's Calendar page. The feed itself
// is built in SQL (public.calendar_ics, tested with the rest of the schema); this function
// only passes the token through and returns the result as text/calendar. Deploy it with
// JWT verification off (Outlook can't sign in): the token is the access check, and an
// unknown token gets a 404.
//
// Environment: SUPABASE_URL and SUPABASE_ANON_KEY are provided by Supabase. Set
// DASHBOARD_URL (supabase secrets set DASHBOARD_URL=...) so events link back to the tracker.

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const ANON_KEY = Deno.env.get("SUPABASE_ANON_KEY") ?? "";
const DASHBOARD_URL = Deno.env.get("DASHBOARD_URL") ?? null;

function tokenFrom(url: URL): string | null {
  const q = url.searchParams.get("token");
  if (q) return q;
  const last = url.pathname.split("/").filter(Boolean).pop() ?? "";
  const m = last.match(/^([0-9a-f]{32,128})(\.ics)?$/i);
  return m ? m[1] : null;
}

Deno.serve(async (req) => {
  if (req.method !== "GET" && req.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405 });
  }
  const token = tokenFrom(new URL(req.url));
  if (!token) return new Response("Not found", { status: 404 });

  const res = await fetch(`${SUPABASE_URL}/rest/v1/rpc/calendar_ics`, {
    method: "POST",
    headers: {
      apikey: ANON_KEY,
      Authorization: `Bearer ${ANON_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ p_token: token, p_dashboard_url: DASHBOARD_URL }),
  });
  if (!res.ok) {
    console.error("calendar_ics failed", res.status, await res.text());
    return new Response("Calendar unavailable", { status: 502 });
  }
  const ics = await res.json();
  if (typeof ics !== "string") return new Response("Not found", { status: 404 });
  return new Response(req.method === "HEAD" ? null : ics, {
    headers: {
      "Content-Type": "text/calendar; charset=utf-8",
      "Content-Disposition": 'inline; filename="ca-budget-hearings.ics"',
      "Cache-Control": "private, max-age=900",
    },
  });
});
