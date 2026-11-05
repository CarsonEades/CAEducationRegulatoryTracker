-- A snapshot of each posting every time it's first stored or flagged as changed, so the
-- dashboard can show what changed since someone last reviewed it. Items and documents
-- are rebuilt on change, so without this there's no record of what they used to be.

create table public.posting_versions (
  id         bigint generated always as identity primary key,
  posting_id bigint not null references public.postings (id) on delete cascade,
  -- The posting's last_changed_at when this snapshot was taken; compared with review times.
  changed_at timestamptz not null,
  snapshot   jsonb not null,
  unique (posting_id, changed_at)
);

alter table public.posting_versions enable row level security;
create policy "team read" on public.posting_versions for select to authenticated using (true);

-- What a posting lists right now: title, date, agenda items, linked documents (with a
-- hash of their contents) and where keywords matched.
create function public.posting_snapshot(p_id bigint) returns jsonb
language sql stable set search_path = public as $$
  select jsonb_build_object(
    'title', p.title,
    'meeting_date', p.meeting_date,
    'items', coalesce((
      select jsonb_agg(jsonb_build_object('position', i.position, 'number', i.number,
                                          'title', i.title, 'description', i.description)
                       order by i.position)
      from items i where i.posting_id = p.id), '[]'::jsonb),
    'documents', coalesce((
      select jsonb_agg(jsonb_build_object('url', d.url, 'label', d.label,
                                          'item_position', d.item_position, 'sha256', d.sha256)
                       order by d.id)
      from documents d where d.posting_id = p.id), '[]'::jsonb),
    'keyword_hits', coalesce((
      select jsonb_agg(distinct jsonb_build_object('term', k.term, 'source', m.source,
                                                   'item_position', i.position,
                                                   'document_url', d.url))
      from matches m
      join keywords k on k.id = m.keyword_id
      left join items i on i.id = m.item_id
      left join documents d on d.id = m.document_id
      where m.posting_id = p.id), '[]'::jsonb))
  from postings p where p.id = p_id
$$;

create function public.record_posting_version() returns trigger
language plpgsql set search_path = public as $$
begin
  insert into posting_versions (posting_id, changed_at, snapshot)
  select p.id, p.last_changed_at, posting_snapshot(p.id) from postings p where p.id = new.id
  on conflict (posting_id, changed_at) do update set snapshot = excluded.snapshot;
  return null;
end $$;

-- Deferred to the end of the transaction, so the snapshot sees the rebuilt items,
-- documents and keyword matches rather than the state halfway through storing them.
create constraint trigger posting_version_on_insert
  after insert on public.postings deferrable initially deferred
  for each row execute function public.record_posting_version();

create constraint trigger posting_version_on_change
  after update on public.postings deferrable initially deferred
  for each row when (new.last_changed_at is distinct from old.last_changed_at)
  execute function public.record_posting_version();

-- Start every existing posting's history with how it looks now, dated to its last change.
-- Changes made before this migration can't be reconstructed.
insert into public.posting_versions (posting_id, changed_at, snapshot)
select id, last_changed_at, public.posting_snapshot(id) from public.postings
on conflict do nothing;
