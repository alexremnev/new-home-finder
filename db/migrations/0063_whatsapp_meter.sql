-- 0063_whatsapp_meter.sql — every WhatsApp message is billed, so every
-- WhatsApp message is counted.
--
-- Apply after 0062.
--
-- ── what changed outside this repository ──────────────────────────────────
--
-- 0038, 0039, 0048 and 0057 were all written against a price list where a
-- free-form reply inside the 24-hour window cost nothing and only a template
-- was charged. That is no longer how Meta bills: every message is paid for,
-- ours and the ones answering theirs, inside the window and out. Two things
-- built on the old rule are wrong under the new one.
--
-- **The allowance counted the wrong thing.** `alerts_used` counted rows in
-- `notifications`, and `notifications` only ever holds listing alerts —
-- `queue_notifications` is the one INSERT into it. The evening digest, the
-- window check-in, the four plan notices and every reply the web app sends
-- back were messages we paid for and that nothing counted anywhere. 0057 left
-- them out deliberately: "charging them to the person's allowance would bill
-- them for our own habits", which was right while they were free. With
-- nothing free left, the bound has to be on the thing the bill is on. Nine
-- hundred *messages* per subscriber per period, whatever caused each one.
--
-- **Nothing knew what we had spent.** Outside the listing alerts there was no
-- record that a WhatsApp message had ever been sent, so "how many went out
-- last month" had no answer — not per subscriber, not in total, not in the
-- dashboard. `notifications.cost_micros` existed for this and was never
-- written by any notifier and never read by anything.
--
-- ── why a ledger and not a counter column ─────────────────────────────────
--
-- The same reason 0055 and 0057 give for counting rather than tallying: a
-- number maintained by a worker is only as fresh as the worker, and this one
-- decides whether somebody is served. A row per message also answers the
-- questions a counter cannot — which kind of message the money went on, and
-- how much of it was our own doing rather than the subscriber's.
--
-- `wa_messages` is written at exactly one point per sender: `mark_sent` in
-- the worker for a listing, `record_wa_message` for everything else the
-- worker sends, and `sendWhatsApp` in the web app, which is the single choke
-- point every reply and receipt already goes through. A send that Meta
-- refused is not written: a refusal is not a message and is not billed.
--
-- ── why the names change ──────────────────────────────────────────────────
--
-- `alert_allowance`, `alerts_used`, `alerts_left`, `out_of_alerts` all said
-- "alert" when they now mean "message", and a column whose name denies what
-- it holds is how the next person reading it gets it wrong. Renamed rather
-- than kept for compatibility: nothing outside this repository reads them.
--
-- The output columns of a view cannot be renamed by CREATE OR REPLACE, hence
-- the drop and the recreate, and hence `queued_notifications` going with it —
-- it reads `user_entitlement` and so cannot outlive it.

BEGIN;

-- ── the ledger ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS wa_messages (
    id              BIGSERIAL PRIMARY KEY,
    -- SET NULL rather than CASCADE, and nullable for two reasons. Erasing a
    -- person must not erase what reaching them cost — the row stops naming
    -- anybody and still counts, the same trade `payments` makes. And a reply
    -- can go to a number with no account at all: somebody who writes to the
    -- bot before signing up is answered, and that answer is billed to us like
    -- any other.
    user_id         BIGINT REFERENCES users(id) ON DELETE SET NULL,
    kind            TEXT NOT NULL,
    -- The listing alert this message was, when it was one. UNIQUE, so the
    -- insert in `mark_sent` and the backfill at the bottom of this file can
    -- both run twice without a message being counted twice. NULLs are
    -- distinct in Postgres, so every message that is not an alert is exempt
    -- from it by itself.
    notification_id BIGINT UNIQUE REFERENCES notifications(id) ON DELETE SET NULL,
    provider_msg_id TEXT,
    -- Which of Meta's two rates this was charged at, in Meta's own words so
    -- that a month here can be reconciled against a month on the invoice.
    --
    --   service  free-form, inside the 24-hour window. Nearly everything: the
    --            queue holds anything whose window is shut, so a listing is
    --            only ever sent as one of these.
    --   utility  the approved template, which is the only thing that reaches
    --            a shut window and is therefore how a plan notice reaches
    --            somebody who has not written in for a month.
    --
    -- The two are priced differently, which is the whole reason this column
    -- exists: one price applied to both would misreport exactly the messages
    -- that cost the most.
    billed_as       TEXT NOT NULL DEFAULT 'service',
    -- What it cost, stamped at send time from `plan_settings` rather than
    -- read back through a join: the price changes, and a month already paid
    -- for must not be re-priced by the change.
    cost_micros     INTEGER NOT NULL DEFAULT 0,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Who caused the message, which is the question the money raises. Only
    -- `reply` is the subscriber's doing; the rest are ours.
    CONSTRAINT wa_messages_kind_ck CHECK (kind IN
        ('listing', 'digest', 'checkin', 'notice', 'reply', 'receipt')),
    CONSTRAINT wa_messages_billed_as_ck CHECK (billed_as IN ('service', 'utility'))
);

COMMENT ON TABLE wa_messages IS
    'One row per WhatsApp message Meta accepted from us, whatever sent it. '
    'The record of what WhatsApp costs and the thing the nine-hundred-message '
    'allowance is counted from — see user_entitlement. A refused send is not '
    'a row: it was not delivered and is not billed.';

COMMENT ON COLUMN wa_messages.kind IS
    'listing — an alert from the queue. digest — the evening summary. '
    'checkin — the question asked before the 24-hour window shuts. notice — '
    'a plan ending. reply — an answer to something they sent us. receipt — '
    'something the web app told them unprompted.';

COMMENT ON COLUMN wa_messages.billed_as IS
    'Meta''s rate this was charged at: service for free-form inside the '
    '24-hour window, utility for the approved template that is the only way '
    'to reach a shut one. Set from the payload the notifier actually sent, '
    'never assumed.';

-- Per subscriber and per period, which is the allowance query, and by date
-- alone, which is every total the dashboard asks for.
CREATE INDEX IF NOT EXISTS wa_messages_by_user
    ON wa_messages (user_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS wa_messages_recent
    ON wa_messages (sent_at DESC);

ALTER TABLE wa_messages ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON wa_messages FROM %I', target);
        END IF;
    END LOOP;
END $$;

-- ── what a message costs ──────────────────────────────────────────────────
--
-- Rows rather than constants, for the reason 0010 gives about the lapsed
-- plan: the price is somebody else's decision and it changes, and a number
-- compiled into the worker cannot be corrected without a deploy.
--
-- **In millionths of a US dollar, not of a pound.** Meta bills this account
-- in dollars, and converting here would mean inventing an exchange rate and
-- then quoting it as a cost for years. The dashboard says $ for the same
-- reason; the revenue beside it stays in pounds because that is the currency
-- it arrives in.
--
-- Two prices, because there are two rates and the expensive one is the rarer
-- one — see `wa_messages.billed_as`. Nought means "nobody has set it", not
-- "free", and the dashboard reports the count with no money against it
-- rather than a confident $0.00.

ALTER TABLE plan_settings
    ADD COLUMN IF NOT EXISTS wa_service_micros INTEGER NOT NULL DEFAULT 0;
ALTER TABLE plan_settings
    ADD COLUMN IF NOT EXISTS wa_template_micros INTEGER NOT NULL DEFAULT 0;

ALTER TABLE plan_settings DROP CONSTRAINT IF EXISTS plan_settings_price_ck;
ALTER TABLE plan_settings ADD CONSTRAINT plan_settings_price_ck
    CHECK (wa_service_micros >= 0 AND wa_template_micros >= 0);

COMMENT ON COLUMN plan_settings.wa_service_micros IS
    'What one free-form message inside the 24-hour window costs us, in '
    'millionths of a US dollar. Stamped onto every wa_messages row at send '
    'time, so a later change does not re-price a month already billed.';
COMMENT ON COLUMN plan_settings.wa_template_micros IS
    'The same for the approved template, which is the only thing that reaches '
    'a shut window. A different and higher rate, and one nobody has read off '
    'an invoice yet: 0 until somebody does, and the dashboard says which '
    'messages are therefore uncosted rather than counting them as free.';

-- $0.022 a message, which is the service rate on this number as of October
-- 2026. Stated here because it is known, and in a settings row because it is
-- Meta's to change.
--
-- The template rate is deliberately left at nought. It is not the same figure
-- — templates are priced per category and per country — and the honest answer
-- to "what is it" is "read it off the bill", which nobody has done yet.
UPDATE plan_settings SET wa_service_micros = 22000;

-- ── the allowance is on messages now ──────────────────────────────────────

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'plans' AND column_name = 'alert_allowance'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'plans' AND column_name = 'message_allowance'
    ) THEN
        ALTER TABLE plans RENAME COLUMN alert_allowance TO message_allowance;
    END IF;
END $$;

ALTER TABLE plans ADD COLUMN IF NOT EXISTS message_allowance INTEGER;

ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_allowance_ck;
ALTER TABLE plans ADD CONSTRAINT plans_allowance_ck
    CHECK (message_allowance IS NULL OR message_allowance > 0);

COMMENT ON COLUMN plans.message_allowance IS
    'How many WhatsApp messages this plan includes in one period, counting '
    'every message we or the subscriber cause. NULL means unmetered, which is '
    'every Telegram plan: there is nothing to meter when delivery is free. '
    'Reaching it ends the period like running out of days does.';

-- Nine hundred, unchanged as a number and changed in what it counts. It came
-- from thirty days at the thirty-a-day cap in 0055, and that cap is on
-- messages too — so the month and the day still say the same thing.
UPDATE plans SET message_allowance = 900 WHERE key = 'wa_month';

-- ── what we have already sent ─────────────────────────────────────────────
--
-- Every listing alert already delivered on WhatsApp becomes a ledger row, so
-- that nobody's counter restarts at nought today and nobody is handed a
-- second allowance inside a period they are halfway through. Idempotent
-- through `notification_id`, so re-applying this file changes nothing.
--
-- `cost_micros` stays 0 on these: what they cost is not recorded anywhere and
-- inventing it would put a number in the ledger that no invoice agrees with.
-- The count is honest; the money starts from here. `billed_as` takes its
-- default of `service`, which is what every delivered listing was — the queue
-- has never let one out of a shut window.
--
-- What cannot be backfilled is the digests, check-ins, notices and replies
-- already sent — nothing recorded them. So the first period after this
-- migration undercounts those by however many went out before it, which is
-- the smallest wrong number available and is bounded by one period.

INSERT INTO wa_messages (user_id, kind, notification_id, provider_msg_id, sent_at)
SELECT n.user_id, 'listing', n.id, n.provider_msg_id, n.sent_at
  FROM notifications n
 WHERE n.channel = 'whatsapp'
   AND n.status = 'sent'
   AND n.sent_at IS NOT NULL
ON CONFLICT (notification_id) DO NOTHING;

-- ── the one answer, counting the ledger ───────────────────────────────────

DROP VIEW IF EXISTS queued_notifications;
DROP VIEW IF EXISTS user_entitlement;

CREATE VIEW user_entitlement AS
SELECT u.id AS user_id,
       u.plan,
       u.plan_from,
       u.plan_until,
       p.display_name AS plan_display,
       -- A plan that costs money. "Paid" has to mean priced AND live, and
       -- conflating the two is how somebody whose month ended stayed exempt
       -- from the daily cap.
       p.price_pence > 0 AS priced,
       p.message_allowance,

       -- Every WhatsApp message in this period, whoever caused it: the
       -- listings, the digest, the check-in, the notices and the replies.
       -- Meta charges for all of them, so all of them are what the allowance
       -- is an allowance of.
       --
       -- Counted from `wa_messages` rather than kept as a counter column, for
       -- the reason 0055 gives: a number maintained by a worker is only as
       -- fresh as the worker, and this one decides whether somebody is
       -- served.
       coalesce(used.sent, 0) AS messages_used,
       CASE WHEN p.message_allowance IS NULL THEN NULL
            ELSE greatest(p.message_allowance - coalesce(used.sent, 0), 0)
       END AS messages_left,

       -- Out of days, out of messages, or neither.
       (u.plan_until IS NOT NULL AND u.plan_until <= now()) AS out_of_days,
       (
           p.message_allowance IS NOT NULL
           AND coalesce(used.sent, 0) >= p.message_allowance
       ) AS out_of_messages,

       (u.plan_until IS NULL OR u.plan_until > now())
       AND (
           p.message_allowance IS NULL
           OR coalesce(used.sent, 0) < p.message_allowance
       ) AS live,

       -- The share that follows from all of the above: the plan's own while
       -- it is live, and once it is not —
       --
       --   * nothing at all on WhatsApp. The lapsed tier is a fifth of what
       --     matches, and on a channel Meta bills per message that is a
       --     standing bill for somebody who has stopped paying. The free tier
       --     exists to show what the service does; it cannot be shown at our
       --     expense for ever.
       --   * the lapsed tier's share on Telegram, where delivery is free and
       --     a fifth of the listings is the best argument for the other four.
       CASE
           WHEN (u.plan_until IS NULL OR u.plan_until > now())
                AND (p.message_allowance IS NULL
                  OR coalesce(used.sent, 0) < p.message_allowance)
               THEN p.delivery_share
           WHEN uc.channel = 'whatsapp' THEN 0
           ELSE coalesce(lapsed.delivery_share, 0)
       END AS delivery_share,

       -- What the alerts fall back to when this plan ends, whether or not it
       -- has ended yet. The same rule as the last two branches above; what
       -- differs is that it does not ask whether the plan is still live. See
       -- 0058 for the notice this exists for.
       CASE
           WHEN uc.channel = 'whatsapp' THEN 0
           ELSE coalesce(lapsed.delivery_share, 0)
       END AS lapsed_share
  FROM users u
  JOIN plans p ON p.key = u.plan
  -- Which messenger they are on, because what a finished plan falls back to
  -- depends on what it costs to deliver. Exactly one channel per account is
  -- primary, so this does not multiply the rows.
  LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
  LEFT JOIN plan_settings ps ON ps.id
  LEFT JOIN plans lapsed ON lapsed.key = ps.lapsed_plan AND lapsed.enabled
  LEFT JOIN LATERAL (
      SELECT count(*) AS sent
        FROM wa_messages w
       WHERE w.user_id = u.id
         AND (u.plan_from IS NULL OR w.sent_at >= u.plan_from)
  ) AS used ON p.message_allowance IS NOT NULL;

COMMENT ON VIEW user_entitlement IS
    'Whether each account''s plan is live, why not when it is not, what share '
    'of matches it therefore receives now (delivery_share) and what that '
    'share becomes once the plan ends (lapsed_share). One row per user. '
    'Exists so that "is the plan live" has one definition: it has two halves '
    '— days and messages — and it was written out in nine places before 0057. '
    'The share is here for the same reason, and because it depends on the '
    'channel: a finished plan falls back to a fifth of the listings on '
    'Telegram and to nothing on WhatsApp, where every message is billed.';

-- ── the queue asks the same question ──────────────────────────────────────
--
-- Unchanged from 0057 but for the daily cap, which counted `notifications`
-- and now counts the ledger. Same reason as the allowance: the cap is there
-- to bound what a non-payer costs, the cost is per message, and a reply or a
-- digest is as billed as an alert. Counting only the alerts let somebody
-- chatting to the bot cost thirty messages a day on top of their thirty.

CREATE VIEW queued_notifications AS
SELECT n.id, n.user_id, n.channel, n.kind, n.listing_id, n.created_at, n.attempts,
       CASE
           WHEN n.channel <> 'whatsapp' THEN NULL

           WHEN n.created_at > now() - lim.photo_grace
                AND EXISTS (
                    SELECT 1 FROM source_messages m
                     WHERE m.listing_id = n.listing_id
                       AND 'photo' = ANY(m.media_kinds)
                       AND m.wa_media_checked_at IS NULL
                ) THEN 'photo'

           WHEN EXISTS (
                    SELECT 1 FROM user_channels uc
                     WHERE uc.user_id = n.user_id AND uc.channel = 'whatsapp'
                       AND (uc.last_inbound_at IS NULL
                         OR uc.last_inbound_at <= now() - lim.wa_window)
                ) THEN 'window shut'

           WHEN EXISTS (
                    SELECT 1 FROM user_channels uc
                     WHERE uc.user_id = n.user_id AND uc.channel = 'whatsapp'
                       AND uc.window_asked_at >= uc.last_inbound_at
                ) THEN 'check-in unanswered'

           -- Thirty messages a day for anybody not on a live paid plan.
           -- "Live" includes having messages left — see user_entitlement.
           WHEN NOT EXISTS (
                    SELECT 1 FROM user_entitlement e
                     WHERE e.user_id = n.user_id AND e.priced AND e.live
                )
                AND (
                    SELECT count(*) FROM wa_messages w
                     WHERE w.user_id = n.user_id
                       AND (w.sent_at AT TIME ZONE 'Europe/London')::date
                         = (now() AT TIME ZONE 'Europe/London')::date
                ) >= lim.wa_daily_cap THEN 'daily cap'

           ELSE NULL
       END AS held
  FROM notifications n
 CROSS JOIN delivery_limits lim
 WHERE n.status = 'queued';

COMMENT ON VIEW queued_notifications IS
    'Everything still queued, with `held` naming the reason it is not being '
    'sent, or NULL when nothing is stopping it. The worker claims the NULLs; '
    'the dashboard ages the NULLs and counts the rest. One definition, '
    'because a held message and a stuck one look identical from outside and '
    'only one of them is a fault.';

-- ── the history that outlives the ledger ──────────────────────────────────
--
-- `wa_messages` is purged on the same two-month rule as the delivery history
-- it mirrors, so without this "what did WhatsApp cost in March" stops having
-- an answer in May. Two columns on the daily rollup, which is already the
-- place every other number survives its raw rows in.

ALTER TABLE daily_stats
    ADD COLUMN IF NOT EXISTS wa_messages_sent INTEGER NOT NULL DEFAULT 0;
-- BIGINT: nine hundred messages times a few thousand subscribers times a
-- per-message price in micros passes a 32-bit integer inside a year.
ALTER TABLE daily_stats
    ADD COLUMN IF NOT EXISTS wa_cost_micros BIGINT NOT NULL DEFAULT 0;

COMMENT ON COLUMN daily_stats.wa_messages_sent IS
    'WhatsApp messages Meta accepted that day, every kind, across everybody. '
    'The permanent record: wa_messages itself is purged after two months.';
COMMENT ON COLUMN daily_stats.wa_cost_micros IS
    'What those messages cost, in millionths of a pound, summed from the '
    'price stamped on each one. 0 for any day before wa_message_micros was '
    'set, which is not the same as a free day.';

-- `notifications.cost_micros` is the column this replaces: it was never
-- written by any notifier and never read by anything. Left in place — the
-- ledger is where cost lives now, and saying so is cheaper than a migration
-- that drops a column somebody might have come to rely on.
COMMENT ON COLUMN notifications.cost_micros IS
    'Unused. What a message cost is recorded on wa_messages, per message, '
    'because not every message we pay for is a notification.';

COMMIT;
