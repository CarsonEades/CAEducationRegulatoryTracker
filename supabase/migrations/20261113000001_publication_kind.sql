-- Budget documents that aren't meetings: LAO reports, trailer bill language, budget
-- releases, Finance Letters, committee reports. In its own file because a new enum value
-- can't be used in the transaction that adds it (tracker/migrate.py runs pending
-- migrations in one transaction), so no migration refers to it by name.
alter type public.posting_kind add value if not exists 'publication';
