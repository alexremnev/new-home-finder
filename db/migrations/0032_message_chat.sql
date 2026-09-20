-- 0032_message_chat.sql — which chat a message was read from.
--
-- Apply after 0031.
--
-- `external_id` is the message id inside one dialog, and 0009 says plainly that
-- it is not comparable between readers. That made the photo fetch pick rows by
-- `reader`, which turned the account that happened to store a message into the
-- only account that could ever fetch its photograph: if that session dies, its
-- messages keep their place in the queue forever and the alerts go out without
-- a picture.
--
-- With the chat recorded, a message id becomes comparable to anybody reading
-- the same chat, so any reader can finish another reader's work. It also closes
-- a quieter hole: one reader watching two chats produces colliding ids today,
-- because nothing said which chat an id belonged to.
--
-- Nullable: rows written before this have no answer, and guessing one would be
-- worse than admitting it. The queries treat a null chat as "only the reader
-- that stored it", which is exactly the old behaviour.

BEGIN;

ALTER TABLE source_messages
    ADD COLUMN IF NOT EXISTS chat TEXT;

-- The photo queue, which is read once per chat per run.
CREATE INDEX IF NOT EXISTS source_messages_photo_by_chat
    ON source_messages (source_key, chat, received_at DESC)
    WHERE wa_media_checked_at IS NULL;

COMMIT;
