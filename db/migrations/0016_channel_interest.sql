-- 0016_channel_interest.sql — which channels people are asking for.
--
-- Apply after 0015.
--
-- ── why record this at all ───────────────────────────────────────────────
--
-- WhatsApp and email are both "coming soon", and both cost real work: WhatsApp
-- needs a provider and message templates approved by Meta, email needs a sender
-- domain and a reputation. Which one to build first should be decided by how many
-- people asked for it, and the only moment anybody is willing to say is the moment
-- they have just finished setting a filter up and are looking at where it can go.
--
-- One row per person per channel, so the count is people and not clicks. Pressing
-- again withdraws it: a vote that cannot be taken back is a trap, and somebody who
-- taps the wrong one should not be counted for ever.
--
-- No free text. A comment box would be a support inbox nobody is staffed to read,
-- and the question here is a count.

BEGIN;

CREATE TABLE IF NOT EXISTS channel_interest (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- Not a foreign key to `channels`: the whole point is to express interest in a
    -- channel that does not exist yet, and `channels` holds the ones that do.
    channel    TEXT   NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, channel),
    CONSTRAINT channel_interest_channel_ck
        CHECK (channel IN ('whatsapp', 'email', 'sms'))
);

CREATE INDEX IF NOT EXISTS channel_interest_by_channel
    ON channel_interest (channel, created_at DESC);

ALTER TABLE channel_interest ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON channel_interest FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
