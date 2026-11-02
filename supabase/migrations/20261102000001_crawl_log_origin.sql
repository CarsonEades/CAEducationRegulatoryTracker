-- Where each rate-limited request came from (GitHub's servers or the office
-- computer), to compare how cde.ca.gov treats them.
alter table public.crawl_log add column origin text;
