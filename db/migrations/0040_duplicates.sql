-- 0040_duplicates.sql — the same flat, listed on two portals.
--
-- Apply after 0039.
--
-- Rightmove and Zoopla both carry most London stock, so one letting agent's
-- flat arrives twice: same postcode, same rent, same rooms, minutes apart. Both
-- were stored and both were sent, so a subscriber got the same property twice
-- and the district counts said a district produced twice what it did.
--
-- ── the fingerprint ──────────────────────────────────────────────────────
--
-- Full postcode, rent, bedrooms, bathrooms, first seen on the same London day.
-- A full postcode is roughly a building, and a building rarely has two flats
-- let at the identical rent with identical rooms on the identical day — unless
-- they are the same flat.
--
-- A listing with no postcode is never matched. The district alone would make
-- every 2-bed at £2,100 in SE16 the same flat, which is a busy district's
-- worth of real listings thrown away.
--
-- `available_from` is deliberately NOT in the fingerprint, though it usually
-- matches too: one portal often omits it, and requiring it to agree would miss
-- exactly the pairs this exists to catch.
--
-- ── why only across sources ──────────────────────────────────────────────
--
-- Two listings with one fingerprint from the SAME portal are usually a
-- build-to-rent block: forty identical studios at one address, one price, let
-- by one operator, all real and all separately available. Collapsing those
-- would hide most of the stock in new developments.
--
-- Across portals it is the opposite: the same agent syndicating one flat. So
-- the rule requires the sources to differ, which is also precisely what was
-- observed — the same flat from Zoopla and from Rightmove.
--
-- ── why a pointer and not a delete ───────────────────────────────────────
--
-- The duplicate row stays. It carries the other portal's url, which is worth
-- having, and `source_messages` references it. `duplicate_of` names the copy we
-- keep, so counting duplicates is a WHERE clause rather than a lost record —
-- and the admin can show how many arrive, which is how we learn whether the
-- rule is worth its risk.
--
-- The pointer always goes to the OLDEST of a group, never in a chain: a third
-- copy points at the first, not at the second.

BEGIN;

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS duplicate_of BIGINT REFERENCES listings(id) ON DELETE SET NULL;

COMMENT ON COLUMN listings.duplicate_of IS
    'The listing this one is a second copy of, on another portal. NULL means '
    'this is the one that counts. Never sent, never counted in per-district '
    'statistics; kept for its url and its message.';

-- The lookup the marker makes: the fingerprint within one day. Partial, because
-- a listing with no postcode is never a candidate.
CREATE INDEX IF NOT EXISTS listings_fingerprint
    ON listings (postcode, price_pcm, bedrooms, first_seen_at)
 WHERE postcode IS NOT NULL AND duplicate_of IS NULL;

-- What the admin counts, and what the matcher excludes.
CREATE INDEX IF NOT EXISTS listings_duplicates
    ON listings (first_seen_at DESC)
 WHERE duplicate_of IS NOT NULL;

-- ── mark what is already stored ──────────────────────────────────────────
--
-- So the district history stops double-counting the past as well as the future,
-- and so the new admin panel has something to show before the next pair
-- arrives. The same rule as the marker, expressed once over the whole table.
WITH ranked AS (
    SELECT l.id,
           l.source_key,
           first_value(l.id) OVER grp AS keeper_id,
           first_value(l.source_key) OVER grp AS keeper_source
      FROM listings l
     WHERE l.postcode IS NOT NULL
    WINDOW grp AS (
        PARTITION BY l.postcode, l.price_pcm, l.bedrooms, l.bathrooms,
                     (l.first_seen_at AT TIME ZONE 'Europe/London')::date
        ORDER BY l.first_seen_at, l.id
    )
)
UPDATE listings l
   SET duplicate_of = ranked.keeper_id
  FROM ranked
 WHERE l.id = ranked.id
   AND ranked.id <> ranked.keeper_id
   -- Compared against the oldest of the group only. That row is never itself a
   -- duplicate, so no chain can form, and a second listing from the same portal
   -- as the oldest is left alone as a real flat in the same block.
   AND ranked.source_key <> ranked.keeper_source
   AND l.duplicate_of IS NULL;

-- ── repair the counts that were computed with the copies in them ─────────
--
-- The rollup only recomputes the last few days, so without this every district
-- day before today keeps its doubled figure and the month view stays wrong.
-- Recomputed over the whole table rather than a window, because it is a one-off
-- and the table is small.
INSERT INTO district_days (day, district, listings, computed_at)
SELECT (l.first_seen_at AT TIME ZONE 'Europe/London')::date AS day,
       l.postcode_district,
       count(*),
       now()
  FROM listings l
 WHERE l.postcode_district IS NOT NULL
   AND l.duplicate_of IS NULL
 GROUP BY 1, 2
ON CONFLICT (day, district) DO UPDATE
   SET listings    = EXCLUDED.listings,
       computed_at = now();

-- A district whose every listing that day turned out to be a copy now counts
-- nought, and the INSERT above cannot say so because it produces no row at all.
UPDATE district_days d
   SET listings = 0, computed_at = now()
 WHERE NOT EXISTS (
     SELECT 1 FROM listings l
      WHERE l.postcode_district = d.district
        AND l.duplicate_of IS NULL
        AND (l.first_seen_at AT TIME ZONE 'Europe/London')::date = d.day
 );

-- The same repair for the one column in `daily_stats` that counts listings.
UPDATE daily_stats s
   SET listings_added = (
       SELECT count(*) FROM listings l
        WHERE l.first_seen_at::date = s.day
          AND l.duplicate_of IS NULL
   ),
       computed_at = now();

COMMIT;
