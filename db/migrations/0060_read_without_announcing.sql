-- 0060_read_without_announcing.sql — a source we keep reading and stop sending.
--
-- Apply after 0059.
--
-- ── the two questions that were one column ────────────────────────────────
--
-- `sources.enabled` has been answering both of "do we read this" and "does
-- what it finds reach a subscriber", because until now no source has wanted
-- different answers. The Telegram feed is about to: its subscription ends, and
-- the fortnight before that is the only chance to establish on evidence
-- whether the scrapers find everything it finds.
--
-- Turning the feed off outright throws that chance away — the feed is the only
-- independent witness we have, and `listing_sightings` compares it against the
-- scrapers for exactly this purpose (0052). Leaving it on sends from a source
-- we are about to lose, which hides the gap we are trying to measure: every
-- flat the feed announces is a flat we never find out whether a scraper would
-- have announced.
--
-- So: keep reading it, stop sending from it. `announces` is that, and it is
-- separate from `enabled` because they are separate questions — one is about
-- cost and politeness towards a site, the other is about what a subscriber
-- receives.
--
-- ── why a column and not an environment variable ─────────────────────────
--
-- The same reason `enabled` is one, and `run.py` says it out loud about that:
-- switching a source off should be one UPDATE and no deploy. Over the next
-- fortnight this is a switch we may want back in a hurry — the honest
-- expectation is that the scrapers have a hole in them somewhere and that the
-- first week is spent finding it — and "ssh in and restart the worker" is not
-- what you want to be doing at the point you notice.
--
-- ── what this migration does NOT do ──────────────────────────────────────
--
-- It does not flip the feed. Every existing source keeps announcing, so
-- applying this changes nothing that a subscriber can see. Muting the feed is
-- a judgement about whether the scrapers are ready, which is read off the
-- `caught_up` counter on the scrape stages and the Coverage panel, and it is
-- one statement when it is:
--
--     UPDATE sources SET announces = false WHERE key = 'tg_feed';
--
-- and the same statement with `true` to undo it.

BEGIN;

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS announces BOOLEAN NOT NULL DEFAULT true;

COMMENT ON COLUMN sources.announces IS
    'Whether what this source finds may be queued for a subscriber. False '
    'means keep reading and storing it, but tell nobody — which is what a '
    'source being kept only as a check on the others looks like. Separate '
    'from `enabled`, which governs whether it is read at all.';

COMMIT;
