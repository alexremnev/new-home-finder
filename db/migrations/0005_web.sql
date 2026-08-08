-- 0005_web.sql — what the web app needs, plus a correction to Rightmove's config.
--
-- Two unrelated things, both small, both blocking the same milestone.
--
-- 1. A start token. Consent has to be provable, and the only moment a Telegram
--    chat id can be learned is when the person messages the bot. So the form
--    creates a pending user with a one-time token, the link sends them to
--    t.me/<bot>?start=<token>, and the webhook exchanges the token for the chat
--    id. Without the token the webhook has no way to tell which subscription a
--    chat belongs to, and asking the person to paste something would lose most
--    of them.
--
--    The token expires. A link that is valid for ever is a standing invitation
--    for anyone who finds it to attach their own chat to someone else's filter.
--
-- 2. Rightmove discovery is per district after all. 0002 assumed the site
--    needed an internal location identifier and therefore searched London-wide;
--    it does not — the outward code is the URL slug
--    (/property-to-rent/SE16.html), verified against the live site. So
--    external_id holds the slug, and the config now says so. This changes the
--    cost of a run from one nationwide page to one page per enabled district.

BEGIN;

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS start_token      TEXT,
    ADD COLUMN IF NOT EXISTS token_expires_at TIMESTAMPTZ;

-- Partial, so that spent tokens (set back to NULL) do not collide with each other.
CREATE UNIQUE INDEX IF NOT EXISTS users_start_token
    ON users (start_token) WHERE start_token IS NOT NULL;

UPDATE sources
   SET config = jsonb_set(
           config,
           '{discovery,primary}',
           '{"kind": "search_page", "enabled": true,
             "url_template": "https://www.rightmove.co.uk/property-to-rent/{code}.html?sortType=6&index={index}",
             "scope_from": "card_address",
             "sort": "newest",
             "note": "the outward code is the url slug, so no location identifier is needed"}'::jsonb
       )
 WHERE key = 'rightmove';

COMMIT;
