-- 0035_message_listing_index.sql — find the message behind a listing.
--
-- Apply after 0034.
--
-- Two things ask "which message produced this listing", and neither had an
-- index to do it with:
--
--   * `claim_queued` looks up the WhatsApp photograph for every notification it
--     claims, on every drain — which is every two minutes. Without this it is a
--     sequential scan of `source_messages` per notification.
--   * the source panels on the System page tell a feed listing from a scraped
--     one, and a listing came from the feed exactly when a message points at it.
--
-- Partial, because a message that produced nothing is of no interest here and
-- unparseable rows are a large share of the table.

BEGIN;

CREATE INDEX IF NOT EXISTS source_messages_listing
    ON source_messages (listing_id) WHERE listing_id IS NOT NULL;

COMMIT;
