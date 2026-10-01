-- 0056_derived_postcodes.sql — a postcode we worked out, kept apart from one
-- we were told.
--
-- Apply after 0055.
--
-- ── the gap this fills ────────────────────────────────────────────────────
--
-- Zoopla states no full postcode on a search page at all, and Rightmove states
-- one in about a third of its results. The rest are read from the listing's own
-- page at 47KB and 100KB a time, budgeted at `POSTCODE_BUDGET` per run — so on
-- a busy run most new listings are stored with `postcode` NULL. An alert then
-- says only "E14", which is two square miles, and `mark_duplicate` skips the
-- listing entirely because it compares on the full postcode.
--
-- Both payloads already carry coordinates, and api.postcodes.io turns a
-- coordinate into a postcode for nothing. So the gap can be closed without a
-- request per listing.
--
-- ── why the column, and not just the postcode ─────────────────────────────
--
-- Because what comes back is the NEAREST postcode centroid, not the postcode of
-- that building, and the difference matters in exactly one place. Measured
-- against the live API on 1 October 2026: within 100m of a point in Canary
-- Wharf there are five distinct unit postcodes, the closest 36m away and four
-- more at 81m. Across 100 random Greater London points the median distance to
-- the nearest centroid was 46m, and a fifth of them had more than one outcode
-- inside 250m.
--
-- For an alert that is plenty: it names the street rather than the district.
-- For `mark_duplicate` it is dangerous, and asymmetrically so. That rule merges
-- two listings from different portals sharing postcode, price, bedrooms and
-- bathrooms on one day — and a guessed postcode collapses the five candidates
-- above into one value, so two genuinely different flats in one block can be
-- declared the same flat. The cost of a false merge is not a duplicate
-- somebody can ignore; it is a real flat the subscriber is never told about,
-- silently. The cost of refusing to merge is a duplicate, which is the state
-- we are in today anyway for every listing whose postcode is NULL.
--
-- So: fill the postcode, write down where it came from, and let the duplicate
-- rule use only the ones a portal actually stated. Dedupe on derived postcodes
-- needs a distance test between the two coordinates as well, and that is a
-- separate change with its own measurements.

BEGIN;

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS postcode_source TEXT NOT NULL DEFAULT 'portal';

ALTER TABLE listings DROP CONSTRAINT IF EXISTS listings_postcode_source_ck;
ALTER TABLE listings ADD CONSTRAINT listings_postcode_source_ck
    CHECK (postcode_source IN ('portal', 'derived'));

COMMENT ON COLUMN listings.postcode_source IS
    'Where `postcode` came from. ''portal'' means the portal stated it, on the '
    'search page or on the listing''s own page, and it is the postcode of that '
    'property. ''derived'' means we read it off api.postcodes.io by coordinate '
    'and it is the nearest postcode centroid, which is good enough to print and '
    'not good enough to merge two listings on — see mark_duplicate.';

-- Everything already stored was stated by a portal: nothing else could have
-- written one until now. The default says so for new rows too, so a reader
-- that knows nothing about this column cannot accidentally claim otherwise.

-- The duplicate rule and the backfill both ask "which of these has no
-- postcode yet", and the backfill adds "and has somewhere to look".
CREATE INDEX IF NOT EXISTS listings_postcode_wanted
    ON listings (first_seen_at DESC)
 WHERE postcode IS NULL AND lat IS NOT NULL;

COMMIT;
