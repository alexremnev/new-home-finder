-- 0017_admin.sql — the admin console: who is in, who visited, what was comped.
--
-- Apply after 0016.
--
-- Three tables, and none of them holds anything the console could not do without.
-- An admin page is the one part of a system where "we might want it later" quietly
-- turns into a store of personal data nobody chose to give, so each table below has
-- to justify itself.

BEGIN;

-- ── who tried to log in ──────────────────────────────────────────────────
--
-- Not a nicety. Without it there is no rate limit: the console runs on serverless
-- functions with no shared memory, so a counter in a variable is a counter per
-- instance, which is no counter at all. This table is the only place a lockout can
-- live.
--
-- The address is stored as a hash, not an address. What the limit needs is "was
-- this the same caller", and a hash answers that; the address itself would be
-- personal data kept for no further purpose.
CREATE TABLE IF NOT EXISTS admin_logins (
    id         BIGSERIAL PRIMARY KEY,
    ip_hash    TEXT NOT NULL,
    ok         BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The lockout query's index: failures, by caller, recently.
CREATE INDEX IF NOT EXISTS admin_logins_recent
    ON admin_logins (ip_hash, created_at DESC) WHERE NOT ok;

-- ── what the admin did that changed somebody's account ───────────────────
--
-- Comped extensions are recorded here rather than in `payments`, deliberately. A
-- free month is not a payment, and putting it there would make every revenue figure
-- drawn from that table wrong — quietly, and in the flattering direction.
CREATE TABLE IF NOT EXISTS admin_actions (
    id         BIGSERIAL PRIMARY KEY,
    action     TEXT   NOT NULL,
    user_id    BIGINT REFERENCES users(id) ON DELETE SET NULL,
    detail     JSONB  NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT admin_actions_action_ck CHECK (action IN ('extend_plan'))
);

CREATE INDEX IF NOT EXISTS admin_actions_recent ON admin_actions (created_at DESC);

-- ── who visited the site ─────────────────────────────────────────────────
--
-- One row per visitor per day, so a count of rows is a count of people and not of
-- page loads.
--
-- `visitor_hash` is a hash of the caller's address and user agent together with a
-- salt that changes every day. The daily salt is the point: it makes the same
-- person on two days two unrelated rows, so the table cannot be turned into a
-- history of one person's visits even by whoever holds it. The cost is that "unique
-- visitors this month" is the sum of daily uniques rather than distinct people, and
-- that is the correct trade — the number is for judging whether the site is being
-- found, not for following anybody.
CREATE TABLE IF NOT EXISTS site_visits (
    day          DATE NOT NULL,
    visitor_hash TEXT NOT NULL,
    hits         INTEGER NOT NULL DEFAULT 1,
    first_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, visitor_hash)
);

CREATE INDEX IF NOT EXISTS site_visits_day ON site_visits (day DESC);

ALTER TABLE admin_logins  ENABLE ROW LEVEL SECURITY;
ALTER TABLE admin_actions ENABLE ROW LEVEL SECURITY;
ALTER TABLE site_visits   ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
    tbl    text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            FOREACH tbl IN ARRAY ARRAY['admin_logins', 'admin_actions', 'site_visits']
            LOOP
                EXECUTE format('REVOKE ALL ON %I FROM %I', tbl, target);
            END LOOP;
        END IF;
    END LOOP;
END $$;

COMMIT;
