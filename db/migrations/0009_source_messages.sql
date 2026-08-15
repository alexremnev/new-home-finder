-- 0009_source_messages.sql — raw inbound messages, and a cursor per reader.
--
-- Apply after 0008.
--
-- ── why raw messages are kept at all ──────────────────────────────────────
--
-- A source that is somebody else's Telegram bot sends prose, and prose changes
-- without notice: a reworded line, a moved field, a link that becomes a button.
-- The parser will therefore break, and the only question is what that costs.
--
-- Parsing on arrival and storing only `listings` makes it cost everything: the
-- message was read, the cursor moved past it, the parse failed, and the listing
-- does not exist anywhere. Keeping the raw message first makes the same failure
-- cost nothing — fix the parser, run it again over what accumulated.
--
-- It also turns "is the parser still working?" into a query rather than a guess:
-- a rising count of `unparseable` is the alarm, and silence in `new` means the
-- reader has stopped.
--
-- ── why two accounts need more than a second cursor ───────────────────────
--
-- A second Telegram account reading the same source sees the same listings as
-- different messages: message ids are per-dialog, so `(source, external_id)`
-- cannot recognise that account A's message 4711 and account B's message 903 are
-- one listing. Deduplicating them needs the content, which is why
-- `content_hash` exists and why the uniqueness that matters is on it.
--
-- The per-reader key is kept too, but for a different job: it makes one reader's
-- own re-read idempotent without depending on hashing.
--
-- ── why the cursor moves into the database ────────────────────────────────
--
-- tools/tg-mirror keeps its cursor in state.json, which is right for one tool on
-- one machine. Two readers on two hosts cannot share a file, and a rebuilt host
-- loses it. A row per (reader, source) survives both.

BEGIN;

-- The source itself. `listings.source_key` and `source_messages.source_key` both
-- reference `sources`, so a Telegram source has to be a row like any other —
-- which is also what lets it be disabled without a deploy.
INSERT INTO sources (key, display_name, enabled, min_items, config)
VALUES (
    'homescout',
    'HomeScout (Telegram)',
    true,
    -- No health floor. The other sources are search pages where "fewer than five
    -- results" means something is wrong; here a quiet hour is just a quiet hour,
    -- and a floor would keep tripping the circuit breaker on nothing.
    0,
    '{"kind": "telegram_bot", "handle": "HomeScoutUK_bot",
      "note": "read from a user account over MTProto; messages land in source_messages"}'::jsonb
)
ON CONFLICT (key) DO NOTHING;

-- ── raw inbound messages ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS source_messages (
    id           BIGSERIAL PRIMARY KEY,
    source_key   TEXT NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    -- Which account read it. Two accounts on one source is an expected shape, not
    -- an anomaly, so the reader is part of the data rather than a deployment
    -- detail — otherwise "why did this arrive twice" has no answer in the table.
    reader       TEXT NOT NULL,
    -- The message id in that reader's own dialog. Unique per reader and NOT
    -- comparable between readers; see the header.
    external_id  TEXT NOT NULL,
    -- When Telegram says it was sent, not when we stored it. The two differ by
    -- however long the reader was asleep, and matching cares about the first.
    received_at  TIMESTAMPTZ NOT NULL,
    stored_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    body         TEXT,
    -- Every URL the message carried, including the ones hidden behind a
    -- hyperlinked word or sitting on an inline button. For a listing source these
    -- are the point of the message, and `body` alone does not contain them.
    links        TEXT[] NOT NULL DEFAULT '{}',
    media_kinds  TEXT[] NOT NULL DEFAULT '{}',

    -- sha256 over the normalised body and the sorted links. This is the identity
    -- of a listing as far as this table is concerned.
    content_hash TEXT NOT NULL,

    status       TEXT NOT NULL DEFAULT 'new',
    parse_error  TEXT,
    parsed_at    TIMESTAMPTZ,
    -- SET NULL rather than CASCADE: if a listing is deleted, the message that
    -- produced it is still evidence of what arrived and when.
    listing_id   BIGINT REFERENCES listings(id) ON DELETE SET NULL,

    CONSTRAINT source_messages_status_ck
        CHECK (status IN ('new', 'parsed', 'unparseable', 'ignored'))
);

-- The dedup that matters, and the one that makes a second reader safe. A repeat
-- of byte-identical content from the same source is the same listing; treating it
-- as new would send it to everybody twice.
CREATE UNIQUE INDEX IF NOT EXISTS source_messages_content
    ON source_messages (source_key, content_hash);

-- One reader re-reading its own history changes nothing. Separate from the hash so
-- that an ingest run is idempotent even before the body has been normalised.
CREATE UNIQUE INDEX IF NOT EXISTS source_messages_reader
    ON source_messages (source_key, reader, external_id);

-- The parser's work queue.
CREATE INDEX IF NOT EXISTS source_messages_pending
    ON source_messages (received_at) WHERE status = 'new';

-- For the "has the parser stopped understanding things?" query.
CREATE INDEX IF NOT EXISTS source_messages_unparseable
    ON source_messages (stored_at DESC) WHERE status = 'unparseable';

-- ── where each reader got to ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ingest_cursors (
    reader           TEXT NOT NULL,
    source_key       TEXT NOT NULL REFERENCES sources(key) ON DELETE CASCADE,
    -- The highest message id already stored for this reader. Not a timestamp:
    -- Telegram ids are monotonic within a dialog, and `min_id` is what the API
    -- takes, so anything else would need translating on every run.
    last_external_id BIGINT NOT NULL DEFAULT 0,
    -- So a stalled reader is visible without reading logs.
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (reader, source_key)
);

-- ── row level security ────────────────────────────────────────────────────
--
-- Same reasoning as 0003, 0006 and 0008: Supabase publishes REST over everything
-- in `public`, reachable with the anon key that ships in front-end code.

ALTER TABLE source_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE ingest_cursors  ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format(
                'REVOKE ALL ON source_messages, ingest_cursors FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
