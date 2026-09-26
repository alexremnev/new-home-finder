-- 0042_studio_is_a_flat.sql — a studio is a flat with nought bedrooms.
--
-- Apply after 0041.
--
-- `property_type` held 'studio' as a type of its own, beside 'flat', 'house'
-- and 'room'. The matcher compares the stored type against the filter as an
-- exact string, so that made a studio invisible to a filter for flats — which
-- is exactly the filter somebody hunting a studio also ticks. The bedroom
-- count already said "studio" on its own: nought.
--
-- So the type becomes 'flat' and the zero carries the meaning. The form asks
-- for a studio with the bedroom slider, where it reads as the word "Studio".
--
-- ── two rewrites, not one ────────────────────────────────────────────────
--
-- The listings, so a filter for flats finds them; and the subscriptions, whose
-- saved criteria may name 'studio' as a wanted type. Leaving the second alone
-- would be worse than the bug being fixed: 'studio' would match nothing at all,
-- and those subscribers would silently stop hearing about anything.

BEGIN;

-- Every stored studio is already nought bedrooms — both parsers wrote the two
-- together — but stated here rather than assumed, because a row that is not
-- would otherwise become an ordinary flat of unknown size.
UPDATE listings
   SET property_type = 'flat'
 WHERE property_type = 'studio'
   AND bedrooms = 0;

-- A studio recorded with bedrooms it cannot have. Left as it is rather than
-- guessed at: nothing reads 'studio' any more, so it simply matches no type
-- filter, which is the safe failure for a row we cannot interpret.
DO $$
DECLARE
    odd bigint;
BEGIN
    SELECT count(*) INTO odd
      FROM listings WHERE property_type = 'studio' AND bedrooms <> 0;
    IF odd > 0 THEN
        RAISE NOTICE '% studio listings had bedrooms <> 0 and were left alone', odd;
    END IF;
END $$;

-- The saved filters. 'studio' becomes 'flat', and the array is rebuilt distinct
-- so a filter that asked for both does not end up with 'flat' twice.
UPDATE subscriptions s
   SET criteria = jsonb_set(
           s.criteria,
           '{property_types}',
           (SELECT jsonb_agg(DISTINCT t)
              FROM jsonb_array_elements_text(s.criteria->'property_types') AS e(v)
              CROSS JOIN LATERAL (SELECT CASE WHEN e.v = 'studio' THEN 'flat' ELSE e.v END) AS x(t))
       )
 WHERE jsonb_typeof(s.criteria->'property_types') = 'array'
   AND s.criteria->'property_types' @> '["studio"]'::jsonb;

COMMIT;
