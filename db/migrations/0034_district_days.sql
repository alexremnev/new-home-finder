-- 0034_district_days.sql — how many listings a district produces per day.
--
-- Apply after 0033.
--
-- `daily_stats.listings_added` counts the whole market per day, which answers
-- "is the feed alive" but not "is SE16 worth covering". That second question
-- decides which districts to enable, what a filter is worth, and whether a
-- subscriber with one area will ever hear anything — so it needs its own grain.
--
-- ── why a table and not a GROUP BY on demand ─────────────────────────────
--
-- The count is cheap today and stops being cheap: `listings` only grows, and
-- this page wants a month at a time, sorted, paged. More importantly a stored
-- count is a *record* — if a district is later disabled, or a source stops
-- covering it, the history of what it used to produce is still there. A live
-- GROUP BY would quietly rewrite the past.
--
-- ── the day boundary ─────────────────────────────────────────────────────
--
-- London, not UTC. Every other date in the admin is London time, and a day that
-- ends at 01:00 in summer would put an evening's listings on tomorrow's row.
--
-- Counted: every listing first seen that day in that district, with no other
-- criteria — the widest possible filter. It is the ceiling a real filter is
-- measured against, not what anybody actually receives.

BEGIN;

CREATE TABLE IF NOT EXISTS district_days (
    day         DATE NOT NULL,
    district    TEXT NOT NULL,
    listings    INTEGER NOT NULL DEFAULT 0,
    -- Today's row is rewritten on every run, so without this there is no way to
    -- tell a quiet district from a stalled rollup.
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, district)
);

-- The page reads one window at a time, ordered by the count.
CREATE INDEX IF NOT EXISTS district_days_day
    ON district_days (day DESC);
CREATE INDEX IF NOT EXISTS district_days_busiest
    ON district_days (day DESC, listings DESC);

-- Everything already known, so the page has something to show before the first
-- rollup run. The rollup itself only recomputes the last few days.
INSERT INTO district_days (day, district, listings)
SELECT (l.first_seen_at AT TIME ZONE 'Europe/London')::date AS day,
       l.postcode_district,
       count(*)
  FROM listings l
 WHERE l.postcode_district IS NOT NULL
   AND (l.first_seen_at AT TIME ZONE 'Europe/London')::date >= DATE '2026-09-18'
 GROUP BY 1, 2
ON CONFLICT (day, district) DO NOTHING;

ALTER TABLE district_days ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON district_days FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
