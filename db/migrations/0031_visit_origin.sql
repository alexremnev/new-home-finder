-- 0031_visit_origin.sql — where a visit came from and what it was read on.
--
-- Apply after 0030.
--
-- `site_visits` already counted people, one row per visitor per day. It could
-- not say where they were or what they held, which is the first question asked
-- of any traffic number.
--
-- Both columns are deliberately coarse. `country` is the two-letter code the
-- edge already knows from the address; `device` is one of three words guessed
-- from the user agent. Neither adds anything that could identify a person, and
-- the visitor hash still rotates daily, so nobody can be followed across days.
--
-- Nullable, because rows written before this exist and because neither fact is
-- always available: there is no country header when the site runs locally.

BEGIN;

ALTER TABLE site_visits
    ADD COLUMN IF NOT EXISTS country TEXT,
    ADD COLUMN IF NOT EXISTS device  TEXT;

ALTER TABLE site_visits
    DROP CONSTRAINT IF EXISTS site_visits_device_ck;

ALTER TABLE site_visits
    ADD CONSTRAINT site_visits_device_ck
    CHECK (device IS NULL OR device IN ('mobile', 'tablet', 'desktop'));

-- The two groupings the admin draws, both always inside one window of days.
CREATE INDEX IF NOT EXISTS site_visits_country ON site_visits (day DESC, country);
CREATE INDEX IF NOT EXISTS site_visits_device  ON site_visits (day DESC, device);

COMMIT;
