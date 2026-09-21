-- 0036_source_sweeps.sql — which districts a source has been read through.
--
-- Apply after 0035.
--
-- The OpenRent sitemap carries no dates, so a listing seen for the first time
-- looks new whether it went up an hour ago or a month ago. On the first pass of
-- a district that is the whole standing market, and announcing it means sending
-- a new subscriber every flat that has been available for weeks.
--
-- "Have we finished reading this district" cannot be derived from the listings
-- themselves. Stored listings only say we have seen *some* of it: a district
-- with sixty listings and a budget of forty looks equally "known" after the
-- first run and after the second, and the second is where the leftover twenty
-- would be announced as new.
--
-- So it is recorded. A district is settled the moment a run finds nothing new
-- in it — at that point everything on the market there is stored, and anything
-- appearing later genuinely appeared later. Until then listings are stored and
-- matched but not announced.
--
-- The price is one extra run, half an hour, before a newly chosen district
-- starts alerting. That is the honest cost of a sitemap without dates.

BEGIN;

CREATE TABLE IF NOT EXISTS source_sweeps (
    source_key TEXT NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    district   TEXT NOT NULL,
    settled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_key, district)
);

ALTER TABLE source_sweeps ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON source_sweeps FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
