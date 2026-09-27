-- 0050_swept_at.sql — two different questions about a district.
--
-- `source_sweeps.settled_at` answers "since when have we been watching this
-- district", and it is what decides whether a listing is news: anything that
-- appeared before we started looking is not.
--
-- The portal scrapers need a second, different answer: "when did we last read
-- this district". That is what decides how far back a newest-first search has
-- to be paged. The two were the same column to begin with, and the result was
-- a scraper that got slowly more expensive for ever — a district watched since
-- August would page back through August on every run, twenty pages at a time,
-- to rediscover listings it had already stored. Measured on Zoopla: a
-- three-day-old watermark already paged to the cap of five pages and fetched
-- 309KB for one district where one page and 63KB was the real need.
--
-- `settled_at` is fixed at the moment watching begins and never moves.
-- `swept_at` moves forward every run. With a twenty-minute schedule that
-- keeps a sweep at one page, which is what the newest-first ordering was for.
--
-- NULL means never swept, which is a first look: read one page, start
-- watching, announce nothing.

ALTER TABLE source_sweeps
    ADD COLUMN IF NOT EXISTS swept_at TIMESTAMPTZ;

COMMENT ON COLUMN source_sweeps.settled_at IS
    'When this district started being watched. Fixed. A listing is only news '
    'if the portal says it appeared after this.';
COMMENT ON COLUMN source_sweeps.swept_at IS
    'When this district was last read. Moves forward every run, and is how '
    'far back a newest-first search is paged. NULL means never.';

-- Existing rows have been read at least once, or they would not be here; not
-- knowing when is the same as never for paging purposes, and a single extra
-- page on the next run is the whole cost of that.
UPDATE source_sweeps SET swept_at = settled_at WHERE swept_at IS NULL;
