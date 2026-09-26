-- 0044_run_host.sql — which machine a run came from.
--
-- Apply after 0043.
--
-- The scrape job runs from two places: a systemd timer on the server and a
-- scheduled task on a desk, because OpenRent answers the server 405 for
-- content pages. Nothing recorded which, so the run log could not answer the
-- one question that matters when the traffic bill doubles — whether both are
-- firing.
--
-- It was answerable only by arithmetic on the gaps between runs, which is how
-- it was in fact worked out: a spike at fifteen minutes plus a flat spread
-- underneath it is two schedules interleaving. That is a good deal of reasoning
-- to reach a fact the run could simply have written down.
--
-- The hostname is not personal data and the repository rule about this table —
-- no chat ids, phone numbers, email addresses — is untouched.

BEGIN;

ALTER TABLE job_runs
    ADD COLUMN IF NOT EXISTS host TEXT;

COMMENT ON COLUMN job_runs.host IS
    'The machine that ran it, from the operating system. NULL for runs recorded '
    'before this column existed.';

CREATE INDEX IF NOT EXISTS job_runs_host
    ON job_runs (host, started_at DESC);

COMMIT;
