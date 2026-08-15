-- 0013_daily_digest.sql — telling people what their plan withheld.
--
-- Apply after 0012.
--
-- ── why this table has to exist for the free tier to be honest ───────────
--
-- 0010 gave the free tier a share of its matches and recorded the rest as `skipped`
-- rows. That is only half of what 0007 demanded when it removed the old cap: "a cap
-- silently withholds listings that matched — which is the one thing this service
-- exists not to do, and the person has no way to know it happened."
--
-- Recording the withheld match answers *our* question. This table answers theirs: a
-- message, once a day, saying how many they did not get. Without it the share is
-- exactly the silent cap 0007 refused, only better bookkept.
--
-- ── why a row per day per person ─────────────────────────────────────────
--
-- The INSERT is the claim, as in `plan_notices`: the primary key means a second
-- drain in the same day inserts nothing and therefore sends nothing. No flag to
-- read, and no window between deciding to send and recording that we did.
--
-- `day` is a date rather than a timestamp so "once a day" needs no arithmetic and
-- cannot drift with the hour the worker happens to run at.

BEGIN;

CREATE TABLE IF NOT EXISTS daily_digests (
    user_id  BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day      DATE   NOT NULL,
    -- What the message said. Kept so a complaint about the number has an answer,
    -- and so the effect on upgrades can be looked at later.
    withheld INTEGER NOT NULL,
    sent_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, day)
);

CREATE INDEX IF NOT EXISTS daily_digests_recent ON daily_digests (sent_at DESC);

ALTER TABLE daily_digests ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON daily_digests FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
