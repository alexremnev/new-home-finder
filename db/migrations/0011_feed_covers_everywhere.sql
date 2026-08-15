-- 0011_feed_covers_everywhere.sql — the feed's coverage is every known district.
--
-- Apply after 0010.
--
-- ── the gap this closes ──────────────────────────────────────────────────
--
-- 0009 added the `tg_feed` source but no `source_locations` rows for it, so
-- `UPDATE source_locations SET enabled = true WHERE source_key = 'tg_feed'`
-- updated nothing and the wizard kept offering the same three districts. The
-- source existed; its coverage did not.
--
-- ── why every district, and why that is not reckless ─────────────────────
--
-- For a scraped source, `enabled` is a cost: one more district is one more page
-- fetched every run, so the flag is a scope that has to be widened deliberately.
--
-- For a feed it is not a cost at all. Nothing is requested per district — messages
-- arrive with whatever postcode they arrive with, and the flag only answers a
-- different question: which districts may a subscriber name. Leaving it at three
-- while listings arrive from thirty means the other twenty-seven are thrown away
-- for no reason, and a subscriber in one of them is told their area "isn't covered
-- yet" when it demonstrably is.
--
-- So the two sources now mean different things by the same column, and that is
-- worth knowing before changing either: narrowing `tg_feed` discards listings,
-- widening `rightmove` adds page fetches.

BEGIN;

INSERT INTO source_locations (source_key, location_id, enabled, external_id)
SELECT 'tg_feed', l.id, true,
       -- `external_id` is how a source names the place in its own URLs. A feed has
       -- no URLs and is never asked about a district, so the code stands in for it:
       -- the column is NOT NULL, and inventing a blank string would read as missing
       -- data rather than as inapplicable.
       l.code
  FROM locations l
 WHERE l.kind = 'postcode_district'
ON CONFLICT (source_key, location_id) DO UPDATE SET enabled = true;

COMMIT;

-- Afterwards, the wizard offers every district in `locations`:
--
--   SELECT count(DISTINCT l.code)
--     FROM source_locations sl
--     JOIN locations l ON l.id = sl.location_id
--     JOIN sources s   ON s.key = sl.source_key AND s.enabled
--    WHERE sl.enabled;
--
-- If that is still small, `locations` itself is short — it is seeded once, and
-- widening it is `python scripts/seed_locations.py`, not a migration.
