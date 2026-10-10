-- 0062_listing_types.sql — one column so the property type can be filled in
-- for a listing that arrived without one.
--
-- Apply after 0061.
--
-- ── what was wrong ────────────────────────────────────────────────────────
--
-- A listing from the Telegram feed has no property type. The feed message has
-- no Type field at all — `worker.ingest.tg_feed.LABELS` is the whole of what
-- it carries — so only a studio or a room gets one, inferred from the bedroom
-- line. Two consequences, and the second is the serious one:
--
--   * the alert read "2 Bedrooms" where a scraped listing reads
--     "2 Bedrooms · Flat";
--   * `match._check_property_type` passes a listing whose type is unknown —
--     silence must never exclude — so somebody who had asked for flats was
--     sent houses and rooms as well.
--
-- The scrapers read the type off the portal, but they could not repair a
-- feed-first row either: `store.insert_listing` is ON CONFLICT by
-- (source_key, external_id) and kept only `last_seen_at`, so whichever reader
-- saw a flat first owned every field about it for ever. That is fixed in the
-- same change, in SQL, with COALESCE.
--
-- This column is the other half: the ingest job reads the type out of the
-- listing page's own title, in the document it already fetches for the
-- photograph. See `worker.ingest.kind`.
--
-- ── why a timestamp and not a flag ────────────────────────────────────────
--
-- Exactly the reason 0026 gives for `image_checked_at`: "we have not looked"
-- and "we looked and the page named nothing" are different states, and
-- without the distinction every untyped listing is fetched again on every run,
-- forever. Zoopla would be most of that cost — it answers a plain client with
-- a 403 — for a page that can never be read this way.

BEGIN;

ALTER TABLE listings ADD COLUMN IF NOT EXISTS type_checked_at TIMESTAMPTZ;

COMMENT ON COLUMN listings.type_checked_at IS
    'When the listing page was read for its property type. Set even when the '
    'page named none, so that a page with nothing to say is not fetched '
    'again. Null means nobody has looked.';

-- The work queue for the type step: untyped listings nobody has looked at,
-- newest first, because an alert about to be sent matters more than one from
-- March. Partial on both conditions, so a typed listing is not in the index
-- at all.
CREATE INDEX IF NOT EXISTS listings_type_pending
    ON listings (first_seen_at DESC)
    WHERE type_checked_at IS NULL AND property_type IS NULL;

COMMIT;
