-- 0028_message_photos.sql — the photograph the feed already sent us.
--
-- Apply after 0027.
--
-- ── why not the portal's picture ──────────────────────────────────────────
--
-- 0026 reads og:image from the listing page. Rightmove answers; Zoopla returns
-- 403 to everything, including the crawler user agents that every site lets
-- through, so it is an address block rather than a matter of manners. Half the
-- listings therefore had no picture and fell back to WhatsApp's own thumbnail,
-- which is small and blurred.
--
-- The feed has been sending the photograph all along — `media_kinds` records
-- that it was there — and we threw it away. Keeping it works for every portal,
-- present and future, and asks nothing of anybody's servers.
--
-- ── why a media id and not a url ──────────────────────────────────────────
--
-- WhatsApp will take an uploaded file and hand back an id, good for 30 days,
-- which is far longer than the minutes between a listing appearing and its
-- alert going out. The alternative — our own bucket with a public url — means
-- credentials, a policy, a bill, and republishing somebody else's photograph
-- from our own domain. Handing it to WhatsApp for one message does not.
--
-- The id lives on the message rather than the listing because that is whose
-- photograph it is; `source_messages.listing_id` already joins the two.

BEGIN;

ALTER TABLE source_messages ADD COLUMN IF NOT EXISTS wa_media_id TEXT;
ALTER TABLE source_messages ADD COLUMN IF NOT EXISTS wa_media_checked_at TIMESTAMPTZ;

COMMENT ON COLUMN source_messages.wa_media_id IS
    'What WhatsApp calls the photograph we uploaded from this message. Expires '
    'after about 30 days; an alert goes out in minutes, so that is not a '
    'lifetime anybody notices.';

-- Separate from the id for the same reason as 0026: "looked and there was no
-- photograph" has to be distinguishable from "not looked at", or every text-only
-- message is fetched again forever.
COMMENT ON COLUMN source_messages.wa_media_checked_at IS
    'When the photograph was last looked for, whether or not one was found.';

-- The work queue: messages that said they had a photograph and have not been
-- looked at, newest first, because an alert about to be sent matters more.
CREATE INDEX IF NOT EXISTS source_messages_photo_pending
    ON source_messages (received_at DESC)
    WHERE wa_media_checked_at IS NULL AND wa_media_id IS NULL;

COMMIT;
