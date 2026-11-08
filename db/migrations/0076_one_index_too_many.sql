-- 0076_one_index_too_many.sql — drop the index 0075 should not have added.
--
-- Apply after 0075.
--
-- Housekeeping, not performance. 16 kB either way; this exists so the schema
-- does not quietly accumulate indexes nothing reads, which is the habit that
-- produced sixty of them over seventy-odd migrations.
--
-- ── what five days of `pg_stat_user_indexes` said ────────────────────────
--
-- 0075 added six indexes for the rollup's counts. Five earn their keep:
--
--   source_messages_stored_at   7281 scans
--   notifications_sent_at       4769
--   notifications_outcome_at    3382
--   listings_added_at           1784   -- the one that mattered
--   users_created_at              45
--
-- `users_created_at` is on this list deliberately. 45 is low and it was on the
-- list to drop, but low is not zero: the planner picks it occasionally and
-- seq-scans `users` the rest of the time, which is the right answer on a table
-- that small. It stays.
--
-- `payments_created_at` had zero, and will keep having zero. `payments` is
-- small enough that a sequential scan of it always wins, so no plan will ever
-- reach for this. It was added on the assumption that a predicate made
-- sargable wants an index to go with it, and for a table of this size that
-- does not follow.

BEGIN;

DROP INDEX IF EXISTS payments_created_at;

COMMIT;

-- ── what was considered here and deliberately not done ───────────────────
--
-- `ALTER TABLE listings SET (fillfactor = 85)`, to buy back HOT updates.
--
-- The HOT ratio on `listings` really is bad — 0.068, so 93% of updates rewrite
-- all eleven index entries rather than none — and fillfactor is the textbook
-- fix, because HOT needs room on the same page and a fillfactor of 100 leaves
-- none. It is not done, because the arithmetic on *this* machine says no:
--
--   gain  ~2,206 non-HOT updates a day x 11 indexes = ~24,000 index writes
--   cost  the heap goes from 83MB to ~98MB
--
-- and 15MB is 7% of a page cache that is only about 200MB to begin with. The
-- binding constraint here is cache, not write throughput: inflating the
-- hottest table in the database by 15% to save twenty-odd thousand small
-- writes is the wrong trade when every megabyte of cache is contested. The
-- ratio looks alarming and the absolute volume behind it does not — 2,206 row
-- rewrites a day is nothing next to the 303 million rows a day the rollup was
-- reading before 0075.
--
-- Revisit if `listings` ever stops being a significant fraction of the cache,
-- or if the instance gets more memory. Not before.
--
-- `source_messages` has a HOT ratio of 0.000, which fillfactor cannot help at
-- all: `parse` changes `status` and `listing_id`, both of which are indexed,
-- and an update that changes an indexed column can never be HOT however much
-- room the page has. That one is structural and the only lever is fewer
-- indexes on the table.
