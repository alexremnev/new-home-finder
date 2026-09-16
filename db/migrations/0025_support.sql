-- 0025_support.sql — complaints raised in the bot, answered by a person.
--
-- Apply after 0024.
--
-- ── why the ticket is its own state machine ───────────────────────────────
--
-- Asking for a complaint and then an email means two messages arrive after the
-- command, and a webhook sees each one alone. 0008 solved a similar problem with
-- `users.wizard_step` and 0019 took it away again, because a column that records
-- where somebody is in a conversation makes every other message ambiguous.
--
-- Here the state is the ticket, which has to exist anyway: `status` says what is
-- still missing, so nothing is stored to describe a conversation that has no
-- other purpose. When the ticket is complete the state is simply gone.
--
-- The partial unique index is what keeps that honest — one unfinished ticket per
-- address, so two /support commands cannot leave two drafts collecting one
-- person's messages between them.
--
-- ── why a command always wins ─────────────────────────────────────────────
--
-- An unfinished ticket swallows plain messages, which is the trap the wizard
-- fell into: somebody who changes their mind is stuck answering a question
-- nobody will ask again. The webhook abandons the draft the moment a message
-- starts with '/', so /stop, /current and /pay are never eaten.

BEGIN;

CREATE TABLE IF NOT EXISTS support_tickets (
    id           BIGSERIAL PRIMARY KEY,
    -- SET NULL rather than CASCADE, as everywhere else that records what
    -- happened: erasing an account does not unsay what it reported.
    user_id      BIGINT REFERENCES users(id) ON DELETE SET NULL,
    -- Where the complaint came from, so it can be answered even when the
    -- account is gone or was never created.
    channel      TEXT NOT NULL REFERENCES channels(key),
    address      TEXT NOT NULL,

    body         TEXT,
    email        TEXT,

    status       TEXT NOT NULL DEFAULT 'awaiting_body',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    submitted_at TIMESTAMPTZ,
    handled_at   TIMESTAMPTZ,
    handled_note TEXT,

    CONSTRAINT support_tickets_status_ck CHECK (
        status IN ('awaiting_body', 'awaiting_email', 'open', 'handled', 'abandoned')
    ),
    -- A submitted ticket has something in it. Without this a bug could file an
    -- empty complaint and the admin would have nothing to act on.
    CONSTRAINT support_tickets_body_ck CHECK (
        status IN ('awaiting_body', 'abandoned') OR body IS NOT NULL
    )
);

-- One unfinished ticket per address. See the header: this is what stops two
-- drafts from splitting one conversation between them.
CREATE UNIQUE INDEX IF NOT EXISTS support_tickets_one_draft
    ON support_tickets (channel, address)
    WHERE status IN ('awaiting_body', 'awaiting_email');

-- The admin's list: what is waiting, oldest promise first.
CREATE INDEX IF NOT EXISTS support_tickets_queue
    ON support_tickets (submitted_at) WHERE status = 'open';

CREATE INDEX IF NOT EXISTS support_tickets_recent
    ON support_tickets (created_at DESC);

ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    target text;
BEGIN
    FOREACH target IN ARRAY ARRAY['anon', 'authenticated']
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = target) THEN
            EXECUTE format('REVOKE ALL ON support_tickets FROM %I', target);
        END IF;
    END LOOP;
END $$;

COMMIT;
