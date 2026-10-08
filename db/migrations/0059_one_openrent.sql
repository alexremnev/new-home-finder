-- 0059_one_openrent.sql — one OpenRent reader, under one key.
--
-- Apply after 0058.
--
-- ── what this removes ─────────────────────────────────────────────────────
--
-- `openrent`, the sitemap reader seeded in 0001. It discovered listings from
-- the nationwide sitemap because that is the only thing `robots.txt` pointed
-- at, and the measurements that retired it are in 0051 and worth repeating:
-- the sitemap carries no `lastmod`, no `ETag` and no `Last-Modified`, ignores
-- `Range`, and is served uncompressed whatever `Accept-Encoding` asks for. So
-- every run downloaded the whole United Kingdom to find out what changed in
-- E14 — about 950MB a day for two or three listings. On top of that OpenRent
-- answers this server 405 on listing pages, so the installer had it disabled
-- anyway and it has not run in weeks.
--
-- ── and what it renames ───────────────────────────────────────────────────
--
-- `openrent_v2`, added in 0051 as a second reader precisely so the two could
-- be compared before either was switched off, becomes `openrent`. The
-- comparison is over; carrying `_v2` in a source key, a job name, a timer, a
-- systemd unit and a module path only tells whoever reads it next that there
-- is a v1 somewhere to find, and there is not.
--
-- ── why the old reader's listings go with it ──────────────────────────────
--
-- They cannot stay. `listings` is UNIQUE (source_key, external_id), and the
-- same flat has a row under each reader — 0051 chose that deliberately, and
-- 0040's duplicate rule kept it from being announced twice. Folding the two
-- keys into one makes those pairs collide, so one row of each pair has to go,
-- and it is the sitemap reader's: the search reader is the one whose watermarks
-- in `source_sweeps`, resolved ids in `source_seen_ids` and sightings in
-- `listing_sightings` describe the state the next run starts from.
--
-- Deleting the source row is what does it, through ON DELETE CASCADE: its
-- listings, their price log, their sightings and the notifications that
-- announced them. Historical OpenRent intake numbers move accordingly, which
-- is the honest cost of having had two keys for one site.
--
-- ── the one thing that must not happen ────────────────────────────────────
--
-- Nobody gets told about a flat twice. `openrent` is undated — there is no
-- published listing date to compare against — so the reader decides what is
-- new by asking whether it has seen the id before, and losing the old reader's
-- rows could make an already-announced flat look new. Hence the first
-- statement below: every external_id the sitemap reader stored is written into
-- `source_seen_ids` *before* anything is deleted, so the search reader skips
-- it on the next run without a single request. 0053 already backfilled most of
-- these; this covers anything stored since, and is a no-op where it does not.
--
-- Order matters throughout, and the statements are not reorderable:
--   1. remember the old reader's ids, while its listings still exist;
--   2. keep its district coverage, while its source row still exists;
--   3. delete the old reader, its runs and its listings;
--   4. create `openrent` afresh from the search reader's own configuration;
--   5. move every child row onto it;
--   6. drop the now-empty `openrent_v2`.

BEGIN;

-- ── 1. so that nothing already sent can look new again ────────────────────
--
-- Recorded under `openrent_v2`, because that is the key the rows are moved
-- from in step 5 and the only source that still exists at this point. The
-- district comes from the listing itself rather than from a slug lookup, which
-- is exactly what this table exists to save.
INSERT INTO source_seen_ids (source_key, external_id, district, seen_at)
SELECT 'openrent_v2', l.external_id, upper(l.postcode_district), l.first_seen_at
  FROM listings l
 WHERE l.source_key = 'openrent'
   AND l.external_id IS NOT NULL
ON CONFLICT (source_key, external_id) DO NOTHING;

-- ── 2. district coverage survives the source it was attached to ───────────
--
-- `source_locations` is what the public wizard reads through `enabledDistricts`
-- and what the README documents as the coverage knob. 0011 gave `tg_feed` a row
-- for every district, so letting these cascade away would not shrink the
-- wizard — but the rows are the record of what OpenRent is set to watch, and
-- re-pointing them is one statement.
INSERT INTO source_locations (source_key, location_id, external_id, enabled)
SELECT 'openrent_v2', sl.location_id, sl.external_id, sl.enabled
  FROM source_locations sl
 WHERE sl.source_key = 'openrent'
ON CONFLICT (source_key, location_id) DO NOTHING;

-- ── 3. the sitemap reader, its runs and its listings ──────────────────────
--
-- The `scrape` job existed only to run it. Deleting the runs cascades their
-- stages and events, which is what clears the per-run `in_sitemap` and
-- `sitemaps` counters the stunted-sitemap guard used to read — the guard is
-- gone from the worker, and these are the last rows that mentioned it.
DELETE FROM job_runs WHERE job = 'scrape';

-- Any stage or event written under the old key by a job that has other work
-- too. There should be none; a stray row would otherwise be silently folded
-- into the search reader's history in step 5 and make the admin's Portal
-- scrapers tile wrong.
DELETE FROM job_stages WHERE source_key = 'openrent';
DELETE FROM job_events WHERE source_key = 'openrent';

-- Sightings naming the old reader. Its own listings cascade below and take
-- theirs with them; this catches any recorded against a listing stored by
-- somebody else, which would otherwise collide with the rename in step 5.
DELETE FROM listing_sightings WHERE reader = 'openrent';

-- And the source itself. CASCADE reaches listings, listing_price_log,
-- listing_sightings, notifications, source_locations, parse_schemas,
-- source_sweeps and source_seen_ids. `source_messages.listing_id` is
-- ON DELETE SET NULL by design — a feed message is still evidence of what
-- arrived, even once the listing it produced is gone.
DELETE FROM sources WHERE key = 'openrent';

-- ── 4. `openrent` again, as the search reader ─────────────────────────────
--
-- Created from `openrent_v2`'s own row rather than written out, so the config
-- cannot drift from what 0051 established. The key cannot simply be UPDATEd:
-- every child FK is ON DELETE CASCADE but NO ACTION on update, so Postgres
-- refuses to move a key out from under rows that still reference it.
INSERT INTO sources (key, display_name, enabled, min_items, health,
                     health_note, health_until, consecutive_fails, config)
SELECT 'openrent', 'OpenRent', s.enabled, s.min_items, s.health,
       s.health_note, s.health_until, s.consecutive_fails,
       s.config || '{"reader": "worker.sources.openrent"}'::jsonb
  FROM sources s
 WHERE s.key = 'openrent_v2';

-- ── 5. every child row onto the new key ───────────────────────────────────
--
-- No ON CONFLICT clauses: step 3 removed everything they could have collided
-- with, and a conflict here would mean that is not true, which is worth an
-- error rather than a silent DO NOTHING.
UPDATE listings         SET source_key = 'openrent' WHERE source_key = 'openrent_v2';
UPDATE source_seen_ids  SET source_key = 'openrent' WHERE source_key = 'openrent_v2';
UPDATE source_sweeps    SET source_key = 'openrent' WHERE source_key = 'openrent_v2';
UPDATE source_locations SET source_key = 'openrent' WHERE source_key = 'openrent_v2';
UPDATE parse_schemas    SET source_key = 'openrent' WHERE source_key = 'openrent_v2';
UPDATE source_messages  SET source_key = 'openrent' WHERE source_key = 'openrent_v2';
UPDATE ingest_cursors   SET source_key = 'openrent' WHERE source_key = 'openrent_v2';

-- Who saw what. See 0052: the reader column holds a source key.
UPDATE listing_sightings SET reader = 'openrent' WHERE reader = 'openrent_v2';

-- The observability trail, so the admin's history does not break at this
-- migration. `job_runs.job` is the job name, which for a portal reader *is*
-- its source key — that is the whole point of the one-job-per-portal split.
UPDATE job_runs   SET job        = 'openrent' WHERE job        = 'openrent_v2';
UPDATE job_stages SET source_key = 'openrent' WHERE source_key = 'openrent_v2';
UPDATE job_events SET source_key = 'openrent' WHERE source_key = 'openrent_v2';

-- ── 6. and the old key, now with nothing under it ─────────────────────────
DELETE FROM sources WHERE key = 'openrent_v2';

COMMIT;
