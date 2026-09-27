-- 0049_big_share_houses.sql — a twenty-one bedroom house is a real listing.
--
-- A scrape run died on this, taking every district with it:
--
--   ValidationError: 1 validation error for Listing
--   bedrooms Input should be less than or equal to 20 [input_value=21]
--
-- The ceiling was a guard against a parse error putting nonsense in the
-- column, and twenty looked generous for a flat. It is not generous for a
-- shared house: OpenRent's own url said `21-bed`, and large HMOs and student
-- houses go well past that. So the guard was refusing real stock.
--
-- Fifty instead. Still low enough to catch a parse that read a price or a
-- floor area as a bedroom count, which is what the constraint is for, and high
-- enough that no house anybody rents room by room reaches it.
--
-- The crash itself was a separate defect and is fixed in the worker: one
-- unusable listing is now counted and skipped rather than ending the run. This
-- migration only stops us throwing away listings somebody could rent — the
-- site's filter offers "5+" with no upper bound, so a subscriber on that
-- setting is entitled to see them.

ALTER TABLE listings DROP CONSTRAINT IF EXISTS listings_bedrooms_ck;
ALTER TABLE listings
    ADD CONSTRAINT listings_bedrooms_ck CHECK (bedrooms BETWEEN 0 AND 50);

COMMENT ON COLUMN listings.bedrooms IS
    'A studio is 0. Up to 50: a shared house can genuinely have 21 rooms, and '
    'the ceiling exists to catch a parse error, not to judge the property.';
