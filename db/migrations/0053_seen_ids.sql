-- 0053_seen_ids.sql — ids we have already looked up, so we stop looking again.
--
-- ── the run this fixes ────────────────────────────────────────────────────
--
-- `openrent_v2` read 150MB in a day, stored 969 rows, and sent nobody
-- anything. Three causes, stacked:
--
--  1. It stored listings under the source key `openrent`, because it reuses
--     `openrent.as_listing` and that function hard-coded its own module's key.
--     Its own "have I seen this id?" check asked about `openrent_v2`, so the
--     answer was always no.
--  2. So every id looked new on every run: the same hundreds of listing pages
--     were fetched again every twenty minutes, and the 969 "stored" were
--     upserts of the same flats.
--  3. The per-run budget was therefore exhausted on the first district, every
--     district after it reported an incomplete read, and an incomplete read is
--     never marked as read-through. A district that never settles never
--     announces anything — which is exactly what a subscriber saw.
--
-- (1) is fixed in the worker. This table fixes the part that would still have
-- cost money afterwards.
--
-- ── why a table and not just `listings` ──────────────────────────────────
--
-- OpenRent's district search is a two-kilometre radius, not the outcode: about
-- a third of what it returns belongs to a neighbouring district. Those
-- listings are correctly not stored — they are not ours — and so `listings`
-- can never answer "have I already established that this id is somebody
-- else's". Without somewhere to write that down, every run pays a redirect
-- lookup per foreign id, for ever.
--
-- One row per id we have resolved, with the district its slug named. Resolving
-- an id is then a once-in-its-lifetime cost rather than a per-run one.
--
-- `district` is NULL when the slug could not be read. That is still worth
-- recording: an id we cannot understand is an id not worth asking about again.

CREATE TABLE IF NOT EXISTS source_seen_ids (
    source_key  TEXT NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    -- The outward code the listing's own slug named, uppercase. NULL when it
    -- could not be read.
    district    TEXT,
    seen_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_key, external_id)
);

COMMENT ON TABLE source_seen_ids IS
    'Listing ids a source has already resolved, and which district each turned '
    'out to be in. Exists because a radius search returns ids in other '
    'districts, which are never stored and so would otherwise be looked up '
    'again on every single run.';

-- Answering "which of these several hundred ids are new" for one district.
CREATE INDEX IF NOT EXISTS source_seen_ids_district
    ON source_seen_ids (source_key, district);

ALTER TABLE source_seen_ids ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON source_seen_ids FROM %I', target);
        END IF;
    END LOOP;
END $$;

-- Every OpenRent listing already stored counts as resolved: we know its id and
-- we know its district, so there is no reason to spend a redirect on it again.
-- Recorded under `openrent_v2` because that is the reader that will ask.
INSERT INTO source_seen_ids (source_key, external_id, district, seen_at)
SELECT 'openrent_v2', l.external_id, upper(l.postcode_district), l.first_seen_at
  FROM listings l
 WHERE l.source_key IN ('openrent', 'openrent_v2')
   AND l.external_id IS NOT NULL
ON CONFLICT (source_key, external_id) DO NOTHING;

-- The districts `openrent_v2` has been reading but could never mark as read
-- through, because of the bug above. Left alone deliberately: with the ids
-- above now known, the next run finds nothing new in a district it has already
-- covered, and settles it the ordinary way. Forcing them settled here would
-- announce as new whatever the budget had not reached yet.
