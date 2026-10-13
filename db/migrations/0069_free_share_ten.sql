-- 0069_free_share_ten.sql — a tenth of what matches, not a fifth.
--
-- Apply after 0068.
--
-- 0046 moved the lapsed tier from a tenth to a fifth, and said why the number
-- lives in a row: "changing the row changes the notices, the digest, the
-- upgrade page and what is actually delivered, all at once and with no
-- deploy". That still holds, and this file is it being used — a fifth of a
-- wide filter's matches turned out to be enough of the service to sit on, and
-- the free tier exists to argue for the paid one rather than to replace it.
--
-- Telegram only, in effect. On WhatsApp `user_entitlement` already answers
-- nought for a finished plan whatever this row says, because Meta bills per
-- message — see 0058 and 0065.

BEGIN;

UPDATE plans SET delivery_share = 10 WHERE key = 'free';

-- ── and the two comments that quoted the old number ──────────────────────
--
-- Both named the share rather than pointing at it, which is the mistake 0046
-- warned about from the other direction: a sentence repeating the number is a
-- sentence that will one day be wrong, and both of these were. Re-worded to
-- name the tier instead, so the next change to the row is the whole change.

COMMENT ON VIEW user_entitlement IS
    'Whether each account''s plan is live, why not when it is not, what share '
    'of matches it therefore receives now (delivery_share) and what that '
    'share becomes once the plan ends (lapsed_share). One row per user. '
    'Exists so that "is the plan live" has one definition: it has two halves '
    '— days and messages — and it was written out in nine places before 0057. '
    'The share is here for the same reason, and because it depends on the '
    'channel: a finished plan falls back to the lapsed tier''s share of the '
    'listings on Telegram and to nothing on WhatsApp, where every message is '
    'billed.';

COMMENT ON VIEW queued_notifications IS
    'Everything still queued, with `held` naming the reason it is not being '
    'sent, or NULL when nothing is stopping it. The worker claims the NULLs; '
    'the dashboard ages the NULLs and counts the rest. One definition, '
    'because a held message and a stuck one look identical from outside and '
    'only one of them is a fault. On WhatsApp the first question is whether '
    'the plan is live at all: a finished trial or month receives nothing '
    'there, backlog included, however open its 24-hour window is — see 0058 '
    'for why nothing rather than the lapsed share.';

COMMIT;
