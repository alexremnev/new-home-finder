-- 0046_free_share_twenty.sql — a fifth of what matches, not a tenth.
--
-- Apply after 0045.
--
-- Nothing in the code carried the number: every message reads it from
-- `plans.delivery_share` on the lapsed tier, which is why they all said 10%.
-- 0010 set it to 10 and it has never been revisited.
--
-- Changing the row changes the notices, the digest, the upgrade page and what
-- is actually delivered, all at once and with no deploy — which is the whole
-- reason it was a row rather than a constant.

BEGIN;

UPDATE plans SET delivery_share = 20 WHERE key = 'free';

COMMIT;
