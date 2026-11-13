# Handoff: CA Education Agency Tracker

Context for a new working session on this repository (`carsoneades/CAEducationRegulatoryTracker`,
formerly `Kalkin55/…`). Read this together with `README.md`, which has setup, the per-agency
status table and the CDE pacing variables.

Last updated: 2026-11-13 (budget tracking added; see "Budget tracking" below).

## What this is and why

The owner's team tracks California education-related agencies' board meetings. Before this,
they checked 14 websites by hand every day for new agendas, then read each agenda for
charter-school items. The tracker does that automatically:

- checks every agency on a schedule
- flags new and changed agendas
- searches agenda items and linked documents for team-defined keywords, scoped to all
  agencies or one
- tracks review status shared by the team ("New", "Updated since review", "Reviewed", with who
  reviewed it and when)
- emails alerts, which each person can set to none, a daily digest, or immediate

## Architecture (all free tier)

```
GitHub Actions (cron) → Python scrapers → Supabase Postgres ← React dashboard (GitHub Pages)
                          │                    ↑
                          └→ Resend (email)    └ Supabase Auth (invite-only email links)
```

- **`tracker/`**: Python package.
  - `pipeline.py`: storing postings and change detection.
  - `paced.py`: the special CDE crawler.
  - `notify.py`: emails.
  - `http.py`: polite fetching.
  - `documents.py`: PDF, DOCX, PPTX and HTML text extraction.
  - `migrate.py`: applies pending migrations at the start of each run.
  - `scrapers/`: one module per agency.
- **Supabase**: migrations live in `supabase/migrations/`.
  - Keyword matching is done in SQL (`keyword_pattern`, `rematch()`), so the scraper and the
    dashboard share one implementation, and a new keyword immediately searches all stored
    text through a trigger.
  - `posting_status` is the dashboard's main view.
  - Row-level security: any signed-in team member can read everything and edit keywords;
    reviews and preferences are per-user.
  - The scraper connects with the direct DB URL, which bypasses RLS.
- **`web/`**: Vite, React and TypeScript, with hash routing for GitHub Pages. Pages: inbox,
  posting detail, agencies, keywords, search, settings.
- **Workflows**:
  - `scrape.yml`: "Check agencies". Runs every 2h from about 6am to 8pm Pacific on weekdays,
    every 6h on weekends, or by hand. It has two jobs, `cloud` and `office`.
  - `digest.yml`: daily email digest, about 7am Pacific.
  - `deploy-web.yml`: builds and publishes the dashboard to Pages.
  - `test.yml`: runs pytest against a Postgres service, plus the web build.

## Current state

- **All 14 agencies are automated.** See the README table.
  - CDE agencies (SBE, ACCS, CPAG) go through the paced crawler.
  - FCC uses the Federal Register API instead of fcc.gov.
  - CCEE uses its public WordPress REST API instead of the HTML page.
  - EAAP is a normal scrape.
- **Everything runs on GitHub's servers.** `CDE_RUNNER=cloud`, so the office (self-hosted
  Windows) runner is no longer used. It may still be registered, and the owner was advised to
  remove it, because the repository is public.
- **CDE item documents have been backfilled** for meetings within 60 days, and are searched.
- **Tests:** 69 pass locally and on GitHub.
- **Dashboard:** after the rename it is at `https://carsoneades.github.io/CAEducationRegulatoryTracker`.

### Repository variables and secrets (last known; the owner sets these, an agent can't read them)

- **Secrets:** `DATABASE_URL` (Supabase session pooler), `RESEND_API_KEY`, `EMAIL_FROM`,
  `ADMIN_EMAILS`.
- **Variables:** `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `DASHBOARD_URL`, `CDE_RUNNER=cloud`,
  `CDE_PAGES_PER_RUN=10`, `CDE_PAGE_GAP_SECONDS=60`, `CDE_MIN_GAP_MINUTES=90`,
  `USE_OFFICE_RUNNER` (was `true`; it has no effect while `CDE_RUNNER=cloud`).
  The CDE values were lowered on Nov 4 after a challenge (see the CDE section below).
- **Optional:** `TRACKER_USER_AGENT`, `CHECK_EVERY_HOURS` (e.g. `eaap=6`), `OFFICE_AGENCIES`.

### Possibly unfinished after the username rename (check with the owner)

1. ~~Re-run **Deploy dashboard**~~: done (run by hand on Nov 4, succeeded).
2. Supabase **Authentication → URL Configuration**: update the Site URL and Redirect URLs to
   the new dashboard URL. Sign-in links break until this is done.
3. Update the `DASHBOARD_URL` variable, which is used for links in emails.

## Key decisions and why

### Stack
- **GitHub Actions + Supabase + GitHub Pages + Resend.** It's free, and there's no server to
  maintain. The team is small and shares one review status; logins are invite-only (public
  sign-ups are turned off in Supabase).

### Change detection (`tracker/pipeline.py`)
- **The content hash covers what the agenda lists, not document contents.** That's the title,
  date, items, and the set of document URLs. A document that downloads in a later run, or is
  deferred by a rate limit, therefore isn't treated as a change.
- **What counts as "updated":** the listing hash changed, *or* an already-downloaded document
  has new contents, *or* a document that arrives late adds a new keyword hit.
- **First run of an agency:** past meetings are stored as `is_baseline` ("Already posted"), so
  the inbox doesn't start with hundreds of items. Upcoming meetings stay "New".
- **Documents** are re-checked at most every 12h (`DOC_RECHECK_HOURS`), and only for meetings
  within 14 days. A failed download keeps the last good copy. Files that are too large
  (over 40 MB) are not retried.
- **Recent meetings only:** detail pages are re-read only for upcoming meetings and those from
  the last 30 days (`needs_details`, `DETAIL_WINDOW_DAYS`). Older listed meetings are passed
  as `Posting(partial=True)` and left as stored. This cut routine requests from about 53 to
  about 11 for the multi-page scrapers.
- **Failures:** an agency that errors, or suddenly returns nothing, counts as failing. After 3
  in a row (`FAILURE_ALERT_THRESHOLD`) the dashboard shows a badge and the admins are emailed.

### Politeness, not disguise
- **The owner asked about making requests look less like a bot. The decision was no.**
  - The tracker uses an honest, identifiable User-Agent. Don't fake browser headers, rotate
    IPs, or run sites' bot-check JavaScript.
  - Spoofing would be deceptive toward state agencies, and it doesn't help against
    behavior-based bot protection.
  - Evidence: EAAP's Cloudflare *challenged* a generic browser-style User-Agent but lets the
    honest one through.
- **What we do instead:**
  - at least 3s between requests to the same site
  - each site's request volume kept low
  - backing off when challenged
- **Recommended to the owner:** set `TRACKER_USER_AGENT` to include an organization name and
  contact address. That's the owner's choice to fill in; don't invent an address.

### cde.ca.gov (SBE, ACCS, CPAG): `tracker/paced.py`
- **The site uses Radware bot protection.** Challenges show up as a 302 to
  `validate.perfdrive.com`, or a 303 whose page footer reads "rdwr". `http.BlockedError`
  detects both.
- **What we learned:**
  - Requests seconds apart get challenged (8s apart from the office computer).
  - Early GitHub runs were challenged, but those had the blank User-Agent bug (see Gotchas).
  - After that fix, every spaced-out request was accepted, from both the office computer and
    GitHub's servers.
  - Later the owner tested bursts: 20 pages at 30s twice, then 70 pages at 25s. All were
    accepted, more than 100 requests in total (Nov 3–4, overnight UTC).
  - **First challenge since the User-Agent fix (Nov 4, 14:13 UTC):** the first request of
    the morning (SBE list, 7.9h after the previous one) was accepted, and the next one
    (ACCS, 26s later) was challenged. Run size wasn't the trigger, since it was only the
    second request. Likely causes are the overnight volume or that run's GitHub server; one
    challenge can't tell them apart. The owner lowered the settings to 10 pages at 60s,
    with 90 minutes between runs, then raised the gap further.
  - **Cause confirmed (Nov 4–5):** temporary diagnostics recorded cookie *names* and redirects.
    Reading a web page sets Radware's cookies (`__uzma`…`__uzmf`, `uzmx`), and the page
    includes its `perfdrive` script. The next web page, sent with those cookies, was redirected
    to `validate.perfdrive.com`, even 92s later. Documents only set a load-balancer cookie
    and 10 in a row at ~90s were accepted. So Radware expects its browser script to have run
    before a second page. **Fix: at most one web page per run; further pages are documents.**
  - **Decision: don't drop or reset the cookies to slip past the check.** That would be evading
    the site's bot check (the same reasoning as not faking browser headers). It could also get
    the User-Agent or GitHub's addresses blocked outright. If more speed is ever needed, set
    `TRACKER_USER_AGENT` with an organization and contact, and ask CDE to allowlist it or for
    a feed.
- **How the crawler works:**
  - It uses a rotating queue stored in `crawl_state`, in this order:
    1. unread meeting lists
    2. unread agendas
    3. lists due for a refresh (`LISTING_REFRESH_HOURS`: SBE 12h, ACCS and CPAG 24h)
    4. upcoming-agenda rechecks (every 20h)
    5. item documents: nearest upcoming meeting first, main docs before
       attachments/handouts/slides; DOCX, PDF and PPTX only; within 60 days
  - Steps 1–4 are web pages: **only a run's first page can be one** (`web_page_ok` in
    `choose_task`). Later pages in the run are documents, or the run stops.
  - Extra pages stop when nothing is due, so routine runs make about 1 request, whatever
    `CDE_PAGES_PER_RUN` is.
  - A challenge's warning includes what that agency read earlier in the run ("earlier this
    run: …"), so the request before the challenge is visible.
  - Every request is logged in `crawl_log`, with the gap since the previous request and its
    origin (`github`, `office` or `local`).
  - Each run summary shows acceptance by spacing and origin.
- **Settings:**
  - `CDE_PAGES_PER_RUN` (capped at 100; the http budget for the host follows it)
  - `CDE_PAGE_GAP_SECONDS`
  - `CDE_MIN_GAP_MINUTES`: computed from the last logged request plus the *current* setting,
    so changes apply immediately
- **On a challenge:** CDE is paused for 2h, doubling with each challenge in a row up to 24h
  (`backoff_until`). A lower gap setting can't cut a backoff short. Challenges are reported as
  warnings (`backing_off=True`), so they don't fail the job and trigger GitHub failure emails.
- **Late-arriving documents:** the text is stored and matched. A new keyword hit flags the
  meeting as updated only if it's upcoming or within 14 days (`DOC_ALERT_DAYS`); older
  meetings just become searchable.
- **Where it runs:** `CDE_RUNNER` (`office` or `cloud`), or the manual `cde_runner` workflow
  input for a single run. Both runners share one queue in the database.

### Changes since review (`posting_versions`)
- Items and documents are rebuilt whenever a posting changes, so a separate history keeps a
  snapshot (title, date, items, documents with content hashes, keyword hits) each time a
  posting is stored or flagged as changed. It's written by deferred triggers on `postings`, so
  it captures the state at commit, after items and matches are rebuilt.
- The agenda page compares the snapshot from the last review with the current agenda for
  "Updated since review" postings (`web/src/changes.ts`). Items are matched by number, or by
  title when they have none. A document that was only downloaded later isn't a revision.
- History started on Nov 5, 2026. Postings that changed before then show a note instead.

### Other agencies
- **FCC:** fcc.gov returns "Access Denied" (Akamai) to automated clients. We use the Federal
  Register API (`federalregister.gov/api/v1`) to find "Open Commission Meeting" Sunshine Act
  notices, and attach the govinfo.gov PDF as the searchable document.
- **CCEE:** the HTML page is behind Cloudflare, but the public WordPress REST API
  (`/wp-json/wp/v2/pages/31303`) serves the same content.
- **EAAP:** Cloudflare blocked a burst of about 6 requests while the scraper was being built.
  A routine check is one page, and it had 0 failures in 14 GitHub checks, so it's checked
  every run. If it starts refusing checks, set `CHECK_EVERY_HOURS=eaap=6`.
- **AG opinions:** one posting per opinion number, merged across the report's tables. "Assigned
  to" is left out for issued opinions to avoid false "updated" flags.
- **AG initiatives:** keyed by the base tracking number, so amendments such as `26-0006A2`
  update the original.
- **CTC:** the listing comes from the Kendo grid's JSON endpoint (`POST /GetDataSourceResult`),
  then `/Details/{Id}` pages.
- **CalPERS:** only the two latest months of committee pages are read. Item numbers continue
  within a session across lists, to match CalPERS' own `-item-5a-` document names.
- **OPSC:** includes meetings that don't have documents yet (e.g. webinars), so they're flagged
  early. The OPSC Online workshops accordion is skipped.
- **CPAG:** has one list page per year (`cpag{YEAR}agendas.asp`). In January it fails until CDE
  creates the new year's page. The fixtures were uploaded by the owner, because CDE blocked
  the agent's attempts to fetch them.

## Budget tracking (added Nov 13, 2026)

The team also wanted the California budget tracked: the Assembly and Senate budget committees
and subcommittees, the Department of Finance and the LAO. It's built into this repo as a second
**program** (`agencies.program = 'budget'`) rather than a separate app, so it reuses the scrapers'
pipeline, change detection, keywords, reviews, digest and login. The dashboard has an "Education
agencies | Budget" switch; budget pages live under `#/budget`.

### Sources and how each is read (checked Nov 13 with one request each; none challenged)

| Agency ids | Route | Protection seen | Changes by year? |
|---|---|---|---|
| `abgt_full`, `abgt_sub1`…`abgt_sub7`, `abgt_reports` | HTML committee pages; `/media/N` links redirect to `system/files/YYYY-MM/*.pdf` | Cloudflare (`__cf_bm` cookie only); plain requests accepted | No: same pages, new "2027 Agendas" heading each year |
| `sbud_full`, `sbud_sub1`…`sbud_sub5`, `sbud_reports` | Same CMS as the Assembly | Cloudflare, accepted | No |
| `dof_trailer_bills` | JSON: `trailerbill.dof.ca.gov/public/getAppInfo`, `/getTrailerBillsByPit/{p,m,e}`, PDFs at `/public/pdf/{id}` (found in the app's `main-*.js`) | none | Lists are per budget stage; `h` (last year's chaptered bills) is skipped |
| `dof_budget` | JSON: `ebudget.ca.gov/api/home/getLink`, `/api/{budget\|publication}/{pit}/{year}/appInfo`; summary PDF at `/{year}/pdf/[{Revised\|Enacted}/]BudgetSummary/FullBudgetSummary.pdf` | none | Latest year only (first entry of getLink) |
| `dof_finance_letters` | HTML: Budget Details page, then each "Proposed … Adjustments" page | none | Links change each year; found by their text |
| `lao_budget`, `lao_education` | HTML | Cloudflare, accepted | `/Budget` shows the current year |

The session environment needed these hosts allowed: `abgt.assembly.ca.gov`,
`sbud.senate.ca.gov`, `dof.ca.gov`, `trailerbill.dof.ca.gov`, `ebudget.ca.gov`, `www.lao.ca.gov`.
GitHub's runners reach them without setup.

### Decisions
- **Review status and notes are per hearing or publication** (the owner chose this over per
  item). Notes are a separate table (`posting_notes`) so writing one doesn't mark anything
  reviewed.
- **Agenda items come from the agenda PDF** (`tracker/agenda.py`). The scraper marks agenda links
  `DocLink(agenda=True)`; the pipeline splits the downloaded (or stored) text into items before
  hashing, so a revised agenda shows which items changed. Strategies, in order: "Issue N" headings
  under body department lines (contents lines, which end in page numbers or dot leaders, are
  ignored), a dot-leader contents list of departments, a bill list, a short numbered order of
  business. When none fit, a warning goes to `agencies.last_note` (health page).
- **Item numbers include the department** ("6980 · Issue 3") because issue numbers restart in
  some agendas, and "Part N ·" when a hearing has several agenda PDFs.
- **Per-site document limits per run** (`http.HOST_DOC_BUDGETS`: 40 for each legislative site
  and the trailer bill API, fewer for DOF, ebudget and LAO). A first run lists about 13 months of
  hearings, which would otherwise mean hundreds of PDFs at once. Deferred documents download in
  later runs, even for hearings older than the 30-day detail window, and items that appear only
  because an agenda finally downloaded are stored quietly (not "updated").
- **Late keyword hits** on postings loaded as "Already posted" no longer flag them as updated
  unless the meeting is recent or upcoming (this applies to the education side too).
- **Empty committee pages** are a warning (`Agency.may_be_empty`), since pages can be emptied
  between sessions.
- **Every request is logged** in `crawl_log` now (with `agency_id`, `request_type` and `status`),
  not just CDE's, for the health page. Rows older than 120 days are deleted at the start of each run.
- **Calendar:** `calendar_hearings` is the team's list; `calendar_feeds` holds each person's
  random token. The iCalendar text is built in SQL (`calendar_ics()`, tested in pytest) and served
  by the `calendar` Edge Function, deployed with JWT verification off. The token is the only
  protection, like Outlook's own "secret address" links; regenerating it cuts off the old address.
  Events use UTC times from the Pacific hearing time, all-day events when no time is listed, and
  `SEQUENCE` from the number of stored versions.
- **Hash compatibility:** `start_time` and `location` are hashed only when set, so education
  postings stored before they existed keep their hash.
- **Not done:** cross-checking the shared hearing pages against the committee pages (the Assembly
  page only lists hearings in session, and the Senate one only links to subcommittees).

### External addresses (check all of these after any rename)
- Dashboard: `https://carsoneades.github.io/CAEducationRegulatoryTracker` (GitHub Pages).
- Supabase **Authentication → URL Configuration**: Site URL and Redirect URLs = the dashboard.
- Repository variable `DASHBOARD_URL` (links in emails), also copied into the calendar
  function's `DASHBOARD_URL` secret by the "Deploy calendar feed" workflow.
- Resend: the verified sending domain and the `EMAIL_FROM` secret.
- Calendar feed: `https://<project-ref>.supabase.co/functions/v1/calendar/<token>.ics` (each
  person's Outlook subscription; changes if the Supabase project changes).

## Gotchas (each one has bitten us)

- **Blank User-Agent:** GitHub passes an unset repository variable as `""`, and a blank
  User-Agent got 403s from dgs.ca.gov and ccee-ca.org (and probably CDE challenges).
  `http.user_agent()` treats an empty value as unset. This was misdiagnosed for a while as
  "cloud IPs are blocked".
- **Compressed responses:** a body read with `iter_bytes()` is already decompressed, so the
  `Content-Encoding` header must be dropped (`http.py`).
- **NUL characters:** some PDFs' extracted text contains NUL characters, which Postgres
  rejects. They're stripped before storing (`_strip_nul`).
- **Windows office runner:**
  - `actions/setup-python` fails there ("Could not find platform independent libraries"), so
    the office job uses the installed Python in a venv (`py -3`).
  - `%-d` in strftime doesn't work on Windows. Use `common.long_date` and `short_date`; a test
    guards against `%-d`.
  - The run summary must be written as UTF-8.
  - The service install failed with "Error 1: Incorrect function", so the owner ran `run.cmd`
    by hand. Sleep or disconnects left office jobs queued.
- **Collation:** GitHub's Postgres sorts text with `en_US`; local servers may use C. Don't
  assert on text order in tests.
- **GitHub expressions:** the `runner` context isn't available in job-level `env`, only in
  steps. Run `actionlint` on workflow changes.
- **Concurrency:** queued office jobs are cancelled when a newer one queues ("higher priority
  waiting request"). That's expected. The `cloud` and `office` jobs have separate concurrency
  groups, so an offline computer never blocks cloud checks.
- **cde.ca.gov:** don't fetch CDE pages in bursts while developing. Use the saved fixtures in
  `tests/fixtures/{sbe,accs,cpag}/`, and `--no-db` preview skips CDE agencies on purpose.
- **Unbuilt-scraper test:** every agency has a scraper now, so
  `test_run_skips_agencies_without_scrapers` monkeypatches `get_scraper` to simulate a missing
  one.

## Working on it

- **Tests** need a Postgres server where they can create databases:
  ```bash
  TEST_ADMIN_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest -q
  ```
  `tests/sql/supabase_stub.sql` stands in for Supabase's `auth` schema and roles. Each test
  gets a fresh copy of a template database with all migrations applied.
- **Preview a scraper without a database:** `python -m tracker run --no-db --agency ctc`
- **Schema changes:** add a new file in `supabase/migrations/`. It's applied automatically at
  the start of the next run, tracked in `schema_migrations`.
- **Reading run results without logs:**
  - Run logs aren't reachable from Claude Code cloud sessions, but check-run *annotations*
    are: `gh api repos/{owner}/{repo}/check-runs/{job_id}/annotations`.
  - The tracker posts a notice for each agency's note (CDE reads, skip reasons), a warning for
    a CDE backoff, and an error for each failure.
  - Use `gh run list` / `gh run view --json jobs` to get job IDs. Starting a run by hand:
    `gh api -X POST repos/{owner}/{repo}/actions/workflows/scrape.yml/dispatches -f ref=main`
    (optional `-f 'inputs[cde_runner]=cloud'`).
- **Commits:** the owner commits as themselves. Earlier commits were made with the owner's git
  identity and a `Co-Authored-By` trailer.

## Open items and ideas

- Confirm the rename follow-ups above are done.
- **Watch for CDE challenges after the one-web-page-per-run fix** (Nov 5). There should be
  none. If documents start being challenged, raise `CDE_PAGE_GAP_SECONDS` or lower
  `CDE_PAGES_PER_RUN`. If lists or agendas fall behind, check `LISTING_REFRESH_HOURS`.
- **Remove the unused self-hosted runner** (security, since the repo is public).
- **Possible improvements, none started:**
  - conditional GETs (ETag/If-Modified-Since) to make re-checks cheaper for sites
  - per-keyword email alerts
  - showing `crawl_log` acceptance stats on the dashboard's Agencies page
  - (Done Nov 4) GitHub's actions were bumped to Node 24 versions: `checkout@v7`,
    `setup-python@v7`, `setup-node@v7`, `configure-pages@v6`, `upload-pages-artifact@v5`
    and `deploy-pages@v5`. `checkout@v7` needs runner v2.327.1 or later, which matters only
    if the office runner is ever used again.
