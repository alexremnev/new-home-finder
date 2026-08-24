-- 0018_plan_limits.sql — two days, seven areas.
--
-- Apply after 0017.
--
-- Both numbers live in `plans` rather than in code, which is the whole reason this
-- is four lines instead of a deploy. 0006 put them there for exactly this.
--
-- Seven areas rather than five: the feed covers every London district, and somebody
-- deciding between the north and the south-east legitimately wants more than five.
-- Seven is where the alert volume starts to be the thing that annoys people rather
-- than the coverage — so the form warns at seven instead of only refusing an eighth.
--
-- Two days rather than seven: a trial is a demonstration, and this one demonstrates
-- itself within a day. A week of free alerts is a week in which the paid plan is
-- never considered.

BEGIN;

UPDATE plans SET max_districts = 7, duration_days = 2  WHERE key = 'trial';
UPDATE plans SET max_districts = 7                     WHERE key = 'paid';
UPDATE plans SET max_districts = 7                     WHERE key = 'free';

COMMIT;
