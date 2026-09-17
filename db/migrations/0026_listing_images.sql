-- 0026_listing_images.sql — one picture per listing, so a channel that cannot
-- make a good preview can send the real thing.
--
-- Apply after 0025.
--
-- ── why this is needed at all ─────────────────────────────────────────────
--
-- Telegram is asked for a large link preview and renders the portal's own
-- og:image at nearly full size. WhatsApp has no such control: `preview_url`
-- makes WhatsApp generate a small, heavily compressed thumbnail, and outside the
-- 24-hour window a template carries no preview whatsoever — so most alerts would
-- arrive with no picture at all.
--
-- The fix is to stop relying on previews and send an image message, which needs
-- a url. `photo_count` was the only thing kept about pictures, and a count is
-- not a picture.
--
-- ── why two columns and not one ───────────────────────────────────────────
--
-- `image_checked_at` separates "we have not looked" from "we looked and there
-- was nothing". Without it every listing whose page has no og:image would be
-- refetched on every run, forever, and a portal would rightly notice.

BEGIN;

ALTER TABLE listings ADD COLUMN IF NOT EXISTS image_url TEXT;
ALTER TABLE listings ADD COLUMN IF NOT EXISTS image_checked_at TIMESTAMPTZ;

COMMENT ON COLUMN listings.image_url IS
    'The portal''s own og:image, read once from the listing page. Hotlinked '
    'rather than copied: it is their picture on their CDN, which is also what a '
    'link preview does.';

-- The work queue for the image step: listings nobody has looked at yet, newest
-- first, because an alert about to be sent matters more than one from March.
CREATE INDEX IF NOT EXISTS listings_image_pending
    ON listings (first_seen_at DESC) WHERE image_checked_at IS NULL;

COMMIT;
