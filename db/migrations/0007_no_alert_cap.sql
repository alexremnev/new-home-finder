-- 0007_no_alert_cap.sql — remove the daily alert cap.
--
-- Every match is now delivered. The cap was there to protect against a noisy
-- filter drowning someone, but the protection people actually want is a narrower
-- filter, and they have that: districts, price, bedrooms, and the flags. A cap
-- silently withholds listings that matched — which is the one thing this service
-- exists not to do, and the person has no way to know it happened.
--
-- The columns are dropped rather than left in place. A limit that nothing reads
-- is a knob that looks adjustable and is not, and the next person to find it will
-- reasonably assume it works.
--
-- If a cap is ever wanted again it belongs on the subscription, not the plan:
-- "fewer messages" is a preference, not something to sell.

BEGIN;

ALTER TABLE subscriptions DROP COLUMN IF EXISTS max_alerts_per_day;
ALTER TABLE plans         DROP COLUMN IF EXISTS max_alerts_per_day;

COMMIT;
