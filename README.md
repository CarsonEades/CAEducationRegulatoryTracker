# CA Education Agency Tracker and CA Budget Tracker

Two trackers in one dashboard, with the same login:

- **Education agencies:** watches California education-related agencies' meeting pages, plus AG
  opinions and ballot initiatives.
- **Budget:** watches the Assembly and Senate budget committees and their subcommittees, the
  Department of Finance (trailer bill language, the Governor's Budget, May Revision and Enacted
  Budget, Finance Letters) and the Legislative Analyst's Office.

Both flag new or changed agendas and publications, search agenda items and linked documents for
the team's keywords, track which postings someone has reviewed, and keep shared notes. Budget
hearings can be put on a team calendar that each person subscribes to in Outlook.

```
GitHub Actions (every 2h) ──► Python scrapers ──► Supabase Postgres ◄── Dashboard (GitHub Pages)
                                    │                   ▲      │
                                    └──► Resend email   │      └──► Calendar feed (Edge Function) ──► Outlook
                                                        └── Supabase Auth (invite-only logins)
```

For the history of decisions, current settings and known pitfalls, see
[docs/HANDOFF.md](docs/HANDOFF.md).

## Status

| Agency | Automated | Notes |
|---|---|---|
| AG Legal Opinions | Yes | One posting per opinion; pending → issued shows as an update |
| AG Ballot Initiatives | Yes | Amendments (e.g. 26-0006A2) update the original initiative |
| Commission on Teacher Credentialing | Yes | Reads the portal's data feed and each meeting's items |
| Office of Public School Construction | Yes | Meetings appear before their documents are posted |
| California School Finance Authority | Yes | Items come from each meeting's staff reports page |
| Fair Political Practices Commission | Yes | |
| CalPERS | Yes | Committee agenda pages for the two latest months |
| CalSTRS | Yes | All committees of each board meeting |
| CCEE | Yes | Via the site's public WordPress API (the HTML page is behind Cloudflare) |
| State Board of Education | Yes, paced | Requests spaced out to suit the site's bot protection; see below |
| Advisory Commission on Charter Schools | Yes, paced | Same site as SBE |
| California Practitioners Advisory Group | Yes, paced | Same site and approach as SBE |
| FCC Open Commission Meetings | Yes | Via the Federal Register API (fcc.gov denies automated requests); the notice PDF is searched |
| Educational Audit Appeals Panel | Yes | Behind Cloudflare, which allows occasional requests (one page per check); reports name appealing districts and charter schools. If it starts refusing checks, set the `CHECK_EVERY_HOURS` variable to `eaap=6` |

Agencies that aren't automated show in the dashboard as "Not automated yet: check by hand".

### Budget sources

| Source | How it's read | Notes |
|---|---|---|
| Assembly Budget Committee and Subcommittees 1–7 | Each committee's agenda page, then the agenda PDFs | Items come from the agenda PDF ("Issue 3: ..."); see below |
| Assembly Budget reports | Committee home page | Floor reports, highlights of the Governor's budget and May Revision, budget plans |
| Senate Budget and Fiscal Review Committee and Subcommittees 1–5 | Same as the Assembly | Dates often leave out the year; it's inferred |
| Senate Budget highlights and reports | Committee home page | |
| DOF trailer bill language | The trailer bill app's data feed (`trailerbill.dof.ca.gov/public/...`) | One item per proposal; a newer "last updated" date shows as an update and its PDF is read again |
| Governor's Budget, May Revision, Enacted Budget | ebudget.ca.gov's data feed (`/api/home/getLink`, `/appInfo`) | The latest budget year; each with its full Budget Summary PDF |
| DOF Finance Letters | Budget Details page, then each set's page | April 1 and May adjustments, one PDF per subject area |
| LAO budget publications | lao.ca.gov/Budget | Publications from the last year |
| LAO education publications | lao.ca.gov/Policy-Areas?areaId=4 | Publications from the last year; the HTML version is searched |

The shared hearing pages (`abgt.assembly.ca.gov/hearings`, `sbud.senate.ca.gov/hearingagendas`)
only point to the committee pages, so they aren't read separately.

### cde.ca.gov (SBE, ACCS, CPAG)

The site uses Radware bot protection. Reading a web page (a meeting list or agenda) sets
Radware's tracking cookies, and a second web page in the same visit is challenged unless a
browser has run Radware's script, however far apart the two requests are. This was confirmed in
November 2026: second pages were challenged at 26, 62 and 92 seconds. Documents (PDF, Word,
PowerPoint) aren't checked; dozens in a row were all accepted.

So **each run reads at most one web page**, and any further pages that run are documents. The
tracker doesn't discard or reset the cookies to get past the check. It identifies itself honestly
and stays within what the site allows. All three agencies are checked from GitHub's servers.

**What gets read.** The three agencies share one queue (`tracker/paced.py`), stored in the
database, in this order:
1. meeting lists not read yet
2. agenda pages not read yet
3. meeting lists due for a refresh: SBE every 12 hours, ACCS and CPAG every 24 hours
4. re-checks of upcoming agendas (every 20 hours)
5. item documents: nearest upcoming meeting first, main item documents before attachments;
   Word, PDF and PowerPoint; meetings within 60 days

Steps 1–4 are web pages, so only a run's first page can be one of them. With about 8 runs on a
weekday, a newly posted agenda is usually read within a few hours.

Agenda pages include each item's subject, which keyword matching uses right away. Documents
become searchable once they're downloaded. A run stops early when nothing is due, so a routine
run makes about one request, however many pages it's allowed.

**Pacing** is set with repository variables:

| Variable | What it does | Default | Recommended (Nov 2026) |
|---|---|---|---|
| `CDE_PAGES_PER_RUN` | Most pages read per run (capped at 100); after the first, documents only | 1 | 10 |
| `CDE_PAGE_GAP_SECONDS` | Wait between pages within a run | 90 | 60 |
| `CDE_MIN_GAP_MINUTES` | Minimum time between runs that contact CDE | 100 | 90 |

A change to `CDE_MIN_GAP_MINUTES` applies on the next run. If CDE challenges a request, CDE
checks pause for 2 hours, doubling with each challenge in a row up to 24 hours. Lowering the
settings doesn't shorten a pause. Challenges appear as warnings on the run, not failures.

**Monitoring.** Every request is logged in `crawl_log` with the gap since the previous request
and where it came from. Each run's summary shows how often CDE accepted requests at different
spacings, and the run's annotations list every page read. A challenge's warning also lists what
that agency read earlier in the run. Spacing between pages doesn't prevent challenges on web
pages, so a lower gap is fine; if documents start being challenged, raise `CDE_PAGE_GAP_SECONDS`
or lower `CDE_PAGES_PER_RUN`.

**Where it runs.** The `CDE_RUNNER` variable is `cloud` (GitHub's servers) or `office` (a
self-hosted runner, used only when `USE_OFFICE_RUNNER` is `true`). For a single run, choose
"Where to check CDE" when running the workflow by hand. Both share the same queue. The office
runner is no longer used.

## How it behaves

- **New / Updated since review / Reviewed:** review status is shared across the team. If a
  reviewed agenda later changes (an item is added, or a linked PDF is revised), it moves back to
  **Updated since review**.
- **First run:** when an agency is scraped for the first time, its past meetings are loaded as
  "Already posted" so the inbox doesn't start with hundreds of items. Upcoming meetings still
  show as **New**.
- **Keywords:** these are matched against posting titles, agenda items, and the text of linked
  PDFs, HTML pages and .docx files. There are three match types:
  - *Word:* the whole word, plurals included ("charter" matches "charters" but not "chartered").
  - *Exact phrase.*
  - *Regex.*

  A keyword can apply to all agencies or to one. Adding a keyword also searches everything
  already collected.
- **Documents:** linked documents are re-downloaded for meetings up to 14 days in the past, so
  revised agendas and late attachments are caught. If a download fails, the last good copy is
  kept.
- **Scraper health:** an agency counts as failing if its scraper errors, or suddenly finds
  nothing. After 3 failures in a row, it shows a red badge in the dashboard and `ADMIN_EMAILS`
  get an email.
- **Email:** each person chooses no email, a daily digest (about 7am Pacific), or immediate
  alerts. They can also limit alerts to education agencies or the budget (or both), to certain
  agencies, or to postings with keyword matches. The daily digest also lists hearings on the team
  calendar in the next 7 days and any source that needs attention.
- **Notes:** anyone can add a note to a posting; everyone sees it. A note doesn't mark the posting
  reviewed. You can edit or delete your own.

### Budget-specific behavior

- **Agenda items come from the agenda PDF.** Budget committees post agendas only as PDFs, so the
  tracker reads each agenda's text and splits it into items: "Issue N" headings with their
  department ("6100 · Issue 3"), a contents list of departments (vote-only agendas), a bill list
  (bill hearings), or a numbered order of business. If none of these fit, the health page shows a
  warning; the agenda's full text is still searched. A revised agenda (same link, new file) is
  flagged as updated, with the changed items highlighted.
- **Dates:** hearings are listed by hearing date; reports, bills and letters by their publication
  or "last updated" date; anything without a date by when the tracker found it.
- **First run:** past hearings and publications load as "Already posted". Committee pages list
  hearings from about the last 13 months; LAO pages, publications from the last year.
- **Document limits:** each run downloads at most 40 documents from each legislative site (and
  fewer from DOF, ebudget and LAO), so a first run spreads its downloads over a few runs. Documents
  that download late don't count as changes.
- **Empty committee pages** (for example between sessions) show as a warning, not a failure.
- **Hearing calendar:** on a hearing's page, or on the Calendar page, choose **Add to team calendar**.
  Each person gets a private calendar address on the Calendar page and subscribes to it once in
  Outlook (**Add calendar → Subscribe from web**). Events include the agenda items and links, and
  update when Outlook next refreshes (usually within a few hours).
- **Health page** (Budget → Health; Agencies for the education side): each source's last check,
  how many postings it found, warnings and errors. Click a source to see its recent requests and
  how many were accepted, by type (web pages, documents, data feeds).

## Setup

### 1. Supabase (database and logins, free tier)
1. Create a project at https://supabase.com.
2. Apply the migration: in the SQL Editor, run `supabase/migrations/20261030000001_init.sql`.
   Alternatively, use `supabase db push` with the Supabase CLI.
3. Under **Authentication → Sign In / Providers**, turn **off** "Allow new users to sign up".
   Then invite each team member from **Authentication → Users → Invite user**.
4. Under **Authentication → URL Configuration**, set the Site URL to your dashboard's URL
   (from step 3 below).
5. Note down three values:
   - Project URL and anon key: **Project Settings → API**.
   - Database connection string: **Connect → Session pooler**. This is `DATABASE_URL`.

### 2. Resend (email, free tier)
Create an account at https://resend.com, verify a sending domain (or use their test sender
while trying it out), and create an API key.

### 3. GitHub repository
1. Push this code to a GitHub repo.
2. **Settings → Secrets and variables → Actions**, then add:
   - **Secrets:** `DATABASE_URL`, `RESEND_API_KEY`, `EMAIL_FROM` (e.g.
     `Agency Tracker <tracker@yourdomain.org>`), `ADMIN_EMAILS` (comma-separated).
   - **Variables:** `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `DASHBOARD_URL` (e.g.
     `https://yourorg.github.io/ca-ed-tracker`), and optionally `TRACKER_USER_AGENT` with a
     contact address so agencies can reach you.
3. Under **Settings → Pages**, set Source to **GitHub Actions**. The **Deploy dashboard**
   workflow then publishes the site.
4. **Check agencies** runs on its schedule. To run it now, open **Actions → Check agencies →
   Run workflow**.

### 4. Calendar feed (Outlook)
1. Create a Supabase access token at https://supabase.com/dashboard/account/tokens.
2. Add repository **secrets** `SUPABASE_ACCESS_TOKEN` (that token) and `SUPABASE_PROJECT_REF` (the
   id in your project's URL, `https://<ref>.supabase.co`).
3. Run **Actions → Deploy calendar feed → Run workflow**. It deploys
   `supabase/functions/calendar` with JWT verification off (Outlook can't sign in; each person's
   private token protects their feed) and sets the function's `DASHBOARD_URL` from the repository
   variable, so events link back to the tracker.

The feed address is `https://<ref>.supabase.co/functions/v1/calendar/<token>.ics`; each person
gets theirs on the dashboard's Calendar page.

## Development

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

Tests need a Postgres server where they can create databases:

```bash
TEST_ADMIN_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest
```

See what the scrapers find, without needing a database:

```bash
python -m tracker run --no-db --agency sbe
```

Only one program: `--program budget` (or `education`).

Run the full pipeline against the database, then roll everything back (it prints any keyword
matches):

```bash
python -m tracker run --dry-run --agency sbe
```

Dashboard:

```bash
cd web && cp .env.example .env.local   # fill in the Supabase URL and anon key
npm install && npm run dev
```

### Adding a scraper

Create `tracker/scrapers/<agency>.py` with a class that subclasses `Scraper`, sets `agency_id`,
and implements `fetch(fetcher) -> list[Posting]`. Register it with the `@register` decorator,
and import the module at the bottom of `tracker/scrapers/__init__.py`.

A scraper only describes what the listing page shows: postings, their items, and document
links. Mark a hearing's agenda PDF with `DocLink(..., agenda=True)` and leave `items` empty to
have the pipeline read the items from the agenda's text (`tracker/agenda.py`). Problems that
shouldn't fail the check go in `self.warnings`, which the health page shows. The pipeline handles downloading, change detection, matching and alerts. Save a copy of
the agency's page under `tests/fixtures/<agency>/` and test the scraper against it, so a site
redesign shows up as a failing test rather than silently missing agendas.
