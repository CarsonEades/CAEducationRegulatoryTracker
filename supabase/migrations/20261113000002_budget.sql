-- California budget tracking: budget committees, DOF and LAO are tracked as more
-- "agencies" in a separate program, with shared notes, a hearing calendar (ICS feed)
-- and per-request logging for the health page.

-- ---------------------------------------------------------------------------
-- Programs and source health
-- ---------------------------------------------------------------------------

alter table public.agencies
  add column program      text not null default 'education'
                          check (program in ('education', 'budget')),
  -- Heading the dashboard groups sources under, e.g. 'Assembly Budget'.
  add column group_name   text,
  -- Listing pages that can legitimately be empty (e.g. between legislative sessions):
  -- finding nothing is a warning, not a failure.
  add column may_be_empty boolean not null default false,
  -- Warnings from the last successful check (e.g. an agenda PDF with no items found).
  add column last_note    text;

-- Hearing time and place, for the calendar. Null when the listing doesn't say.
alter table public.postings
  add column start_time time,
  add column location   text;

-- Every request is logged now (not just cde.ca.gov), with the source it was for and
-- what kind of request it was, so the health page can break acceptance down by type.
alter table public.crawl_log
  add column agency_id    text,
  add column request_type text check (request_type in ('page', 'document', 'api')),
  add column status       int;
create index on public.crawl_log (agency_id, requested_at desc);

-- null = both programs.
alter table public.keywords
  add column program text check (program in ('education', 'budget'));
alter table public.user_prefs
  add column programs text[];

-- ---------------------------------------------------------------------------
-- Shared notes (separate from reviews, so a note doesn't mark anything reviewed)
-- ---------------------------------------------------------------------------

create table public.posting_notes (
  id         bigint generated always as identity primary key,
  posting_id bigint not null references public.postings (id) on delete cascade,
  user_id    uuid not null default auth.uid() references public.profiles (id) on delete cascade,
  body       text not null check (length(trim(body)) > 0),
  created_at timestamptz not null default now(),
  edited_at  timestamptz
);
create index on public.posting_notes (posting_id, created_at);

alter table public.posting_notes enable row level security;
create policy "team read" on public.posting_notes for select to authenticated using (true);
create policy "note as self" on public.posting_notes for insert to authenticated
  with check (user_id = auth.uid());
create policy "edit own note" on public.posting_notes for update to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy "delete own note" on public.posting_notes for delete to authenticated
  using (user_id = auth.uid());

-- ---------------------------------------------------------------------------
-- Hearing calendar: the team picks hearings; each person subscribes to a private feed
-- ---------------------------------------------------------------------------

create table public.calendar_hearings (
  posting_id bigint primary key references public.postings (id) on delete cascade,
  added_by   uuid default auth.uid() references public.profiles (id) on delete set null,
  added_at   timestamptz not null default now()
);
alter table public.calendar_hearings enable row level security;
create policy "team read" on public.calendar_hearings for select to authenticated using (true);
create policy "team edits calendar" on public.calendar_hearings for all to authenticated
  using (true) with check (true);

-- The token is the feed URL's only protection, like Outlook's own "secret address"
-- links. Regenerating it (delete + insert) cuts off an old URL.
create table public.calendar_feeds (
  user_id    uuid primary key default auth.uid() references auth.users (id) on delete cascade,
  token      text not null unique default (replace(gen_random_uuid()::text, '-', '')
                                           || replace(gen_random_uuid()::text, '-', '')),
  created_at timestamptz not null default now()
);
alter table public.calendar_feeds enable row level security;
create policy "own feed" on public.calendar_feeds for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

-- iCalendar text escaping (RFC 5545 3.3.11).
create function public.ics_escape(s text) returns text
language sql immutable as $$
  select replace(replace(replace(replace(replace(coalesce(s, ''), '\', '\\'), ';', '\;'),
                                 ',', '\,'), E'\r', ''), E'\n', '\n')
$$;

-- Fold a content line to at most 75 octets per physical line (RFC 5545 3.1).
create function public.ics_fold(line text) returns text
language plpgsql immutable as $$
declare
  out_text text := '';
  cur text := '';
  ch text;
  limit_octets int := 75;
begin
  foreach ch in array regexp_split_to_array(line, '') loop
    if octet_length(cur) + octet_length(ch) > limit_octets then
      out_text := out_text || cur || E'\r\n';
      cur := ' ';
      limit_octets := 75;
    end if;
    cur := cur || ch;
  end loop;
  return out_text || cur;
end $$;

-- The feed for one subscriber: hearings on the team calendar from 60 days ago onward.
-- Called by the `calendar` Edge Function with the token from the URL; an unknown token
-- returns null. Times are Pacific; hearings without a time are all-day events.
create function public.calendar_ics(p_token text, p_dashboard_url text default null)
returns text
language plpgsql stable security definer set search_path = public as $$
declare
  lines text[] := array['BEGIN:VCALENDAR', 'VERSION:2.0',
                        'PRODID:-//CA Budget Tracker//Hearings//EN', 'CALSCALE:GREGORIAN',
                        'METHOD:PUBLISH', 'X-WR-CALNAME:CA Budget Hearings',
                        'X-PUBLISHED-TTL:PT1H', 'REFRESH-INTERVAL;VALUE=DURATION:PT1H'];
  r record;
  descr text;
  starts timestamptz;
  base text := rtrim(coalesce(p_dashboard_url, ''), '/');
begin
  if p_token is null or not exists (select 1 from calendar_feeds where token = p_token) then
    return null;
  end if;
  for r in
    select p.id, p.title, p.meeting_date, p.start_time, p.location, p.url,
           p.last_changed_at, a.name as agency_name,
           (select count(*) from posting_versions v where v.posting_id = p.id) as versions,
           (select string_agg('- ' || concat_ws(' ', i.number, i.title), E'\n' order by i.position)
              from items i where i.posting_id = p.id) as item_list
    from calendar_hearings c
    join postings p on p.id = c.posting_id
    join agencies a on a.id = p.agency_id
    where p.meeting_date >= current_date - 60
    order by p.meeting_date, p.id
  loop
    descr := r.agency_name
      || case when r.item_list is not null then E'\n\nAgenda items:\n' || r.item_list else '' end
      || case when r.url is not null then E'\n\nAgenda: ' || r.url else '' end
      || case when base <> '' then E'\nTracker: ' || base || '/#/posting/' || r.id else '' end;
    lines := lines || array['BEGIN:VEVENT',
      'UID:posting-' || r.id || '@ca-budget-tracker',
      'DTSTAMP:' || to_char(r.last_changed_at at time zone 'UTC', 'YYYYMMDD"T"HH24MISS"Z"'),
      'LAST-MODIFIED:' || to_char(r.last_changed_at at time zone 'UTC', 'YYYYMMDD"T"HH24MISS"Z"'),
      'SEQUENCE:' || greatest(r.versions - 1, 0)];
    if r.start_time is not null then
      starts := (r.meeting_date + r.start_time) at time zone 'America/Los_Angeles';
      lines := lines || array[
        'DTSTART:' || to_char(starts at time zone 'UTC', 'YYYYMMDD"T"HH24MISS"Z"'),
        'DTEND:' || to_char((starts + interval '2 hours') at time zone 'UTC', 'YYYYMMDD"T"HH24MISS"Z"')];
    else
      lines := lines || array[
        'DTSTART;VALUE=DATE:' || to_char(r.meeting_date, 'YYYYMMDD'),
        'DTEND;VALUE=DATE:' || to_char(r.meeting_date + 1, 'YYYYMMDD')];
    end if;
    lines := lines || array['SUMMARY:' || ics_escape(r.title),
                            'DESCRIPTION:' || ics_escape(descr)];
    if r.location is not null then
      lines := lines || ('LOCATION:' || ics_escape(r.location));
    end if;
    if r.url is not null then
      lines := lines || ('URL:' || r.url);
    end if;
    lines := lines || 'TRANSP:TRANSPARENT'::text || 'END:VEVENT'::text;
  end loop;
  lines := lines || 'END:VCALENDAR'::text;
  return (select string_agg(ics_fold(l), E'\r\n') from unnest(lines) l) || E'\r\n';
end $$;

-- Only the feed function is callable without signing in.
revoke all on function public.calendar_ics(text, text) from public;
grant execute on function public.calendar_ics(text, text) to anon, authenticated;

-- ---------------------------------------------------------------------------
-- Keyword matching respects a keyword's program
-- ---------------------------------------------------------------------------

create or replace function public.rematch(p_posting_ids bigint[], p_keyword_ids bigint[]) returns void
language plpgsql security definer set search_path = public as $$
begin
  delete from matches m
  where (p_posting_ids is null or m.posting_id = any (p_posting_ids))
    and (p_keyword_ids is null or m.keyword_id = any (p_keyword_ids));

  with kw as (
    select k.id, k.agency_id, k.program, keyword_pattern(k.term, k.match_type) as pat
    from keywords k
    where k.active and (p_keyword_ids is null or k.id = any (p_keyword_ids))
  ),
  scope as (
    select p.id as posting_id, kw.id as keyword_id, kw.pat
    from postings p
    join agencies a on a.id = p.agency_id
    join kw on (kw.agency_id is null or kw.agency_id = p.agency_id)
           and (kw.program is null or kw.program = a.program)
    where p_posting_ids is null or p.id = any (p_posting_ids)
  )
  insert into matches (keyword_id, posting_id, item_id, document_id, source, hit_count, snippet)
  select s.keyword_id, s.posting_id, null::bigint, null::bigint, 'title',
         regexp_count(p.title, s.pat, 1, 'i'), match_snippet(p.title, s.pat)
  from scope s join postings p on p.id = s.posting_id
  where p.title ~* s.pat
  union all
  select s.keyword_id, s.posting_id, i.id, null::bigint, 'item',
         regexp_count(t.body, s.pat, 1, 'i'), match_snippet(t.body, s.pat)
  from scope s
  join items i on i.posting_id = s.posting_id
  cross join lateral (
    select concat_ws(' ', i.number, i.title, i.description) as body
  ) t
  where t.body ~* s.pat
  union all
  select s.keyword_id, s.posting_id, null::bigint, d.id, 'document',
         regexp_count(t.body, s.pat, 1, 'i'), match_snippet(t.body, s.pat)
  from scope s
  join documents d on d.posting_id = s.posting_id
  cross join lateral (
    select concat_ws(' ', d.label, d.text) as body
  ) t
  where t.body ~* s.pat;
end $$;

drop trigger if exists keywords_rematch on public.keywords;
create trigger keywords_rematch
  after insert or update of term, match_type, agency_id, program, active on public.keywords
  for each row execute function public.keywords_rematch_trigger();

-- ---------------------------------------------------------------------------
-- Dashboard view and search, with program, group, notes and calendar
-- ---------------------------------------------------------------------------

drop view if exists public.posting_status;
create view public.posting_status with (security_invoker = true) as
select
  p.*,
  a.short_name as agency_short_name,
  a.name       as agency_name,
  a.program,
  a.group_name,
  r.reviewed_at as last_reviewed_at,
  r.user_id     as last_reviewed_by,
  pr.email      as last_reviewed_by_email,
  pr.display_name as last_reviewed_by_name,
  case
    when r.reviewed_at is null and not p.is_baseline then 'new'
    when p.last_changed_at > coalesce(r.reviewed_at, p.first_seen_at) then 'updated'
    when r.reviewed_at is null then 'baseline'
    else 'reviewed'
  end as status,
  coalesce(mc.match_count, 0) as match_count,
  coalesce(mc.keywords, '{}') as matched_keywords,
  (select count(*) from public.posting_notes n where n.posting_id = p.id)::int as note_count,
  exists (select 1 from public.calendar_hearings c where c.posting_id = p.id) as on_calendar
from public.postings p
join public.agencies a on a.id = p.agency_id
left join lateral (
  select rv.reviewed_at, rv.user_id from public.reviews rv
  where rv.posting_id = p.id order by rv.reviewed_at desc limit 1
) r on true
left join public.profiles pr on pr.id = r.user_id
left join lateral (
  select sum(m.hit_count)::int as match_count,
         array_agg(distinct k.term order by k.term) as keywords
  from public.matches m join public.keywords k on k.id = m.keyword_id
  where m.posting_id = p.id
) mc on true;

drop function if exists public.search_all(text, text, date, date);
create function public.search_all(q text, p_agency_id text default null,
                                  p_from date default null, p_to date default null,
                                  p_program text default null)
returns table (posting_id bigint, source text, item_id bigint, document_id bigint,
               headline text, rank real)
language sql stable security invoker set search_path = public as $$
  with query as (select websearch_to_tsquery('english', q) as tsq),
  hits as (
    select p.id, 'title'::text, null::bigint, null::bigint,
           ts_headline('english', p.title, query.tsq), ts_rank(p.search, query.tsq)
    from postings p, query where p.search @@ query.tsq
    union all
    select i.posting_id, 'item', i.id, null,
           ts_headline('english', concat_ws(' ', i.number, i.title, i.description), query.tsq,
                       'MaxFragments=2'),
           ts_rank(i.search, query.tsq)
    from items i, query where i.search @@ query.tsq
    union all
    select d.posting_id, 'document', null, d.id,
           ts_headline('english', coalesce(d.text, d.label, ''), query.tsq, 'MaxFragments=2'),
           ts_rank(d.search, query.tsq)
    from documents d, query where d.search @@ query.tsq
  )
  select h.* from hits h (posting_id, source, item_id, document_id, headline, rank)
  join postings p on p.id = h.posting_id
  join agencies a on a.id = p.agency_id
  where (p_agency_id is null or p.agency_id = p_agency_id)
    and (p_program is null or a.program = p_program)
    and (p_from is null or p.meeting_date >= p_from)
    and (p_to is null or p.meeting_date <= p_to)
  order by h.rank desc
  limit 200
$$;

-- Starter budget keywords; the team edits these in the dashboard. ("charter" already
-- applies to both programs.)
insert into public.keywords (term, match_type, program, created_by) values
  ('Proposition 98', 'phrase', 'budget', null),
  ('LCFF', 'word', 'budget', null),
  ('Local Control Funding Formula', 'phrase', 'budget', null),
  ('independent study', 'phrase', 'budget', null),
  ('Charter School Facility Grant', 'phrase', 'budget', null);
