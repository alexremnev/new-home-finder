-- 0075_rollup_without_the_scans.sql — indexes the rollup can actually use.
--
-- Apply after 0074.
--
-- ── the email this answers ────────────────────────────────────────────────
--
-- Supabase: "Your project is depleting its Disk IO Budget." The dashboard said
-- the rest of it — memory commitment in warning, swap sitting at 415MB, and
-- cache and buffers pinned at about 200MB on a box with roughly 460MB of RAM.
-- A page cache that small against a working set larger than it means nearly
-- every read is a disk read, and something was refilling it constantly.
--
-- That something was `rollup`. It runs every two minutes — 720 times a day —
-- and each run evaluated eleven correlated counts across a three-day
-- `generate_series`, so some thirty aggregates a run. Every one of them was
-- written as `sent_at::date = span.day`: a function of the column, which no
-- index can answer however many there are, so all thirty were sequential
-- scans of `notifications`, `source_messages`, `listings` and `wa_messages`.
-- Plus `district_days`, which scanned the whole of `listings` and grouped it.
--
-- Roughly 21,600 full table scans a day, to keep two admin panels fresh to the
-- minute that nobody reads to the minute. The query is rewritten in
-- `worker/pipeline/rollup.py` to compare half-open ranges instead; these are
-- the indexes that rewrite needs to be worth anything.
--
-- ── why each one, and why it is partial where it is ──────────────────────
--
-- Each index below exists for named counts in that job and for nothing else,
-- which is why most are partial: a rollup index that also had to carry the
-- rows the rollup never counts would be larger to keep and slower to read,
-- and the whole purpose here is a smaller working set.
--
-- `notifications` already has `notifications_queue`, which is partial on
-- `status = 'queued'` — the drain's work queue. The rollup asks about the
-- three terminal statuses instead, so it could never use it. These two cover
-- the three counts: 'sent' by `sent_at`, because that is when the delivery
-- happened, and the other two by `created_at`, because a message that failed
-- or was withheld has no `sent_at` to be counted under.
--
-- ── building these ───────────────────────────────────────────────────────
--
-- In one transaction, as every migration here is. These are small tables —
-- seconds, not minutes — but the build takes a SHARE lock, so a sweep writing
-- to `listings` waits for it. If that matters more than the atomicity, run the
-- CREATE INDEX lines on their own with CONCURRENTLY added and outside any
-- BEGIN, which is the one thing a transaction cannot contain.

BEGIN;

-- ── notifications: alerts_sent, alerts_failed, alerts_withheld ───────────

CREATE INDEX IF NOT EXISTS notifications_sent_at
    ON notifications (sent_at)
 WHERE status = 'sent';

COMMENT ON INDEX notifications_sent_at IS
    'The rollup''s `alerts_sent` count, by the day the message actually went '
    'out. Partial because a message not sent has no send to count.';

CREATE INDEX IF NOT EXISTS notifications_outcome_at
    ON notifications (created_at)
 WHERE status IN ('failed', 'skipped');

COMMENT ON INDEX notifications_outcome_at IS
    'The rollup''s `alerts_failed` and `alerts_withheld` counts. Both are by '
    '`created_at`: neither outcome has a `sent_at` to be filed under.';

-- ── source_messages: messages_stored ─────────────────────────────────────
--
-- `source_messages_unparseable` already covers `messages_unread`, once the
-- predicate is a range rather than a cast — it is partial on exactly that
-- status and ordered on exactly that column. Only the unfiltered count of
-- everything stored that day had nothing to read.

CREATE INDEX IF NOT EXISTS source_messages_stored_at
    ON source_messages (stored_at);

COMMENT ON INDEX source_messages_stored_at IS
    'The rollup''s `messages_stored` count. Not partial: it counts every '
    'message stored that day whatever the parser made of it.';

-- ── listings: listings_added, and the whole of district_days ─────────────
--
-- The one that matters most, because `listings` is the largest table here and
-- `district_days` read all of it every two minutes.
--
-- `listings_fresh` is partial on `status = 'active'` and so cannot serve
-- either: a listing delisted yesterday was still a listing added yesterday,
-- and dropping it from the count would make the history change behind us.
--
-- `postcode_district` is INCLUDEd rather than keyed. `district_days` groups by
-- it but never seeks on it, so carrying it as a payload lets the group-by read
-- the index alone and leave the heap — and the heap is where the wide columns
-- are, `raw` and `description` among them.

CREATE INDEX IF NOT EXISTS listings_added_at
    ON listings (first_seen_at)
 INCLUDE (postcode_district)
 WHERE duplicate_of IS NULL;

COMMENT ON INDEX listings_added_at IS
    'Originals by the day they first appeared: the rollup''s `listings_added` '
    'and every row of `district_days`. Partial on originals because a second '
    'portal''s copy is not stock the market produced.';

-- ── users and payments: signups, revenue_pence ───────────────────────────
--
-- Both small, and both were scanned 720 times a day anyway. `payments_user` is
-- keyed `(user_id, created_at)`, so a question about a day across all users
-- cannot use it.

CREATE INDEX IF NOT EXISTS users_created_at
    ON users (created_at);

CREATE INDEX IF NOT EXISTS payments_created_at
    ON payments (created_at);

-- `wa_messages` needs nothing: `wa_messages_recent` is already `(sent_at DESC)`
-- over the whole table, and both WhatsApp counts become range scans on it the
-- moment the cast is gone.

COMMIT;
