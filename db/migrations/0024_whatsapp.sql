-- 0024_whatsapp.sql — WhatsApp as a second destination, one per subscription.
--
-- Apply after 0023.
--
-- ── what this deliberately does not do ────────────────────────────────────
--
-- It does not touch `notifications UNIQUE (user_id, listing_id)`. One
-- subscription has one destination, so that key still says exactly what it has
-- said since 0001: this person has been told about this flat. Delivering one
-- listing to two apps would have required widening it, which is the riskiest
-- change available in this schema and buys a feature almost nobody wants — the
-- second destination is nearly always a second person, and a second person is a
-- second subscription.
--
-- `is_primary` therefore keeps its meaning too, and needs no policing.
--
-- It also records nothing linking one messenger to another. The two are separate
-- accounts with separate plans, and somebody who signs up on both gets the free
-- trial on both. Recognising them would mean either asking for a phone number or
-- keeping a cookie, and neither is a fair price for policing two days.
--
BEGIN;

UPDATE channels SET enabled = true WHERE key = 'whatsapp';

-- The same short-lived token as `start`, for the same reason: it travels through
-- a chat app, and one left in a conversation should stop working.
ALTER TABLE user_tokens DROP CONSTRAINT IF EXISTS user_tokens_purpose_ck;
ALTER TABLE user_tokens ADD CONSTRAINT user_tokens_purpose_ck
    CHECK (purpose IN ('start', 'edit', 'upgrade', 'whatsapp'));

-- ── the 24-hour window ────────────────────────────────────────────────────

-- WhatsApp lets a business send a free-form message only within 24 hours of the
-- person's last message; outside it, only an approved template. Which of the two
-- a listing becomes is decided per recipient, so the fact has to be stored.
ALTER TABLE user_channels ADD COLUMN IF NOT EXISTS last_inbound_at TIMESTAMPTZ;

COMMENT ON COLUMN user_channels.last_inbound_at IS
    'When this address last messaged us. Inside 24 hours of it WhatsApp allows '
    'free-form text with a link preview, which is both richer and free; outside, '
    'only a template.';

-- ── which listings people actually open ───────────────────────────────────

-- A WhatsApp template button is a fixed prefix plus one variable, so the three
-- portals cannot be linked directly: every button goes through /l/<id> on our
-- own domain and redirects. The side effect is the first click data this project
-- has had, for either channel.
ALTER TABLE listings ADD COLUMN IF NOT EXISTS click_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS last_clicked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS listings_clicked
    ON listings (last_clicked_at DESC) WHERE last_clicked_at IS NOT NULL;

COMMIT;
