-- 0048_whatsapp_cost.sql — what a WhatsApp subscriber may cost.
--
-- Apply after 0047.
--
-- Meta bills per message. A filter over several busy districts can match a
-- hundred listings a day, and on a free or lapsed tier that is a bill for
-- somebody who has paid nothing and may never.
--
-- ── the two thresholds, and what each does ───────────────────────────────
--
--   30 in one London day   paying: an alert, delivery untouched.
--                          not paying: delivery stops for the rest of that day,
--                          and an alert.
--   500 in total           an alert either way. A non-payer cannot reach it —
--                          the daily stop arrives first — so in practice this
--                          is the figure that says a paying subscriber has
--                          become expensive.
--
-- Nothing is deleted and nothing is refused: the notifications stay queued and
-- are simply not claimed, the same way a message waiting for its photograph is
-- held. So the day rolls over and they go out, in order.
--
-- ── why this is not `max_alerts_per_day` again ───────────────────────────
--
-- 0007 dropped that column on purpose: it was a limit the *subscriber* chose,
-- and a cap chosen by the person receiving alerts silently withholds listings
-- that matched — the one thing this service exists not to do. This is a
-- different thing wearing a similar number: a cost control on one channel, for
-- people who are not paying for it, recorded and alerted rather than silent.
--
-- ── one alert per threshold, not one per run ─────────────────────────────
--
-- `drain` runs every two minutes. Without a record, crossing a threshold would
-- alert thirty times an hour. The daily row is keyed on the London day; the
-- lifetime row is unique per user by a partial index, so it fires once ever.

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_cost_alerts (
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT   NOT NULL,
    day        DATE   NOT NULL,
    sent       INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind, day),
    CONSTRAINT whatsapp_cost_alerts_kind_ck CHECK (kind IN ('daily', 'lifetime'))
);

-- The lifetime figure is passed once and never again, so its alert is too.
CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_cost_alerts_lifetime
    ON whatsapp_cost_alerts (user_id)
 WHERE kind = 'lifetime';

COMMENT ON TABLE whatsapp_cost_alerts IS
    'One row per alert already sent about a WhatsApp subscriber''s volume. '
    'Exists so that drain, which runs every two minutes, says it once.';

ALTER TABLE whatsapp_cost_alerts ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON whatsapp_cost_alerts FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
