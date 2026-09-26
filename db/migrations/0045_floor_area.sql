-- 0045_floor_area.sql — how big the place is.
--
-- Apply after 0044.
--
-- Both sources state it and neither was stored. The Telegram feed carries a
-- `Size` field, which until now was kept only as a string inside `raw` where
-- nothing could compare it; OpenRent's pages carry it as "105 sq m".
--
-- ── one unit, and it is feet ──────────────────────────────────────────────
--
-- Square feet, because that is what the filter is stated in. Storing both units
-- would invite a row where they disagree, and the conversion is exact enough in
-- one direction to be pointless in two.
--
-- OpenRent's metres are converted on the way in. A source that gives no unit is
-- refused rather than guessed at: a bare "65" is 65 square feet or 65 square
-- metres depending on who wrote it, and either reading silently removes homes
-- from somebody's alerts.
--
-- ── absent means "matches" ────────────────────────────────────────────────
--
-- Most listings say nothing about size, so NULL has to match every filter — the
-- same rule the matcher already applies to bathrooms, furnishing and property
-- type. A filter on area therefore narrows what does say, and never hides what
-- does not. Said out loud on the form, because the opposite assumption is the
-- reasonable one to make.

BEGIN;

ALTER TABLE listings
    ADD COLUMN IF NOT EXISTS floor_area_sqft INTEGER;

ALTER TABLE listings DROP CONSTRAINT IF EXISTS listings_floor_area_ck;
ALTER TABLE listings ADD CONSTRAINT listings_floor_area_ck
    CHECK (floor_area_sqft IS NULL OR floor_area_sqft BETWEEN 50 AND 20000);

COMMENT ON COLUMN listings.floor_area_sqft IS
    'Internal floor area in square feet. NULL when the source did not say, or '
    'said it without a unit. Metres are converted on the way in.';

-- The filter asks for a range, and a range over a mostly-NULL column wants the
-- rows that have one.
CREATE INDEX IF NOT EXISTS listings_floor_area
    ON listings (floor_area_sqft)
 WHERE floor_area_sqft IS NOT NULL;

COMMIT;
