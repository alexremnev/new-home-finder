-- io_budget_verify.sql — did 0075 and the rollup rewrite actually work.
--
-- Run after applying 0075 and redeploying the worker. Sections 1 and 2 are
-- one-off and answer "is the index being used"; 3 is the real measurement and
-- reads the worker's own run log; 4 is the snapshot to compare against
-- tomorrow. Everything here is read-only except section 1, which is a VACUUM.
--
-- The companion is `io_budget_check.sql`, which ranks where the IO goes. This
-- one only checks the one change that has been made.

-- ── 1. make the index usable, which it is not yet ────────────────────────
--
-- Not optional, and the reason is easy to miss. An index-only scan may skip
-- the heap only for pages the visibility map marks all-visible, and the map is
-- maintained by VACUUM. On a table that has just had an index built and has
-- not been vacuumed since, the planner knows it would have to visit the heap
-- for most rows anyway — so it costs the plan accordingly and may still choose
-- the sequential scan. The index would then look like it had not helped.
--
-- ANALYZE as well as VACUUM: the planner has no statistics on a brand-new
-- index's correlation, and `listings` is append-ordered by `first_seen_at`,
-- which is exactly the property that makes a range on it cheap. It has to know
-- that to choose it.

VACUUM (ANALYZE) listings;

-- ── 2. the plan, which should no longer say Seq Scan ────────────────────
--
-- Before 0075 this read `Parallel Seq Scan on listings`, 10,671 buffers,
-- 105,278 rows examined to return 2,788, in 285ms.
--
-- Expect now: `Index Only Scan using listings_added_at`, buffers in the tens
-- rather than the ten thousands, and `Heap Fetches: 0` — that last line is the
-- one that says section 1 did its job. A non-zero Heap Fetches means the
-- visibility map is stale again, not that the index is wrong.

EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*) FROM listings l
 WHERE l.first_seen_at >= current_date::timestamptz
   AND l.first_seen_at <  (current_date + 1)::timestamptz
   AND l.duplicate_of IS NULL;

-- The `district_days` shape, which was the single most expensive statement in
-- the job: a full scan of `listings` plus a group-by, 720 times a day. This is
-- the whole of what that statement now reads.
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT (l.first_seen_at AT TIME ZONE 'Europe/London')::date, l.postcode_district,
       count(*)
  FROM listings l
 WHERE l.postcode_district IS NOT NULL
   AND l.duplicate_of IS NULL
   AND l.first_seen_at >= (((now() AT TIME ZONE 'Europe/London')::date
                            - make_interval(days => 2)) AT TIME ZONE 'Europe/London')
 GROUP BY 1, 2;

-- ── 3. the measurement that matters: the worker timing itself ───────────
--
-- Better evidence than any EXPLAIN, because it is the real job against the
-- real database with the real cache contention, and because the history is
-- already there — `purge` keeps 30 days of `job_runs`.
--
-- One row per hour. Look for the hour the deploy landed: `runs` should fall
-- from about 30 to 4, and `avg_ms` should fall by considerably more than that.
-- `total_ms` is the product and is the number that was emptying the budget.

SELECT date_trunc('hour', started_at)                            AS hour,
       count(*)                                                  AS runs,
       round(avg(extract(epoch FROM finished_at - started_at)) * 1000)::bigint AS avg_ms,
       round(max(extract(epoch FROM finished_at - started_at)) * 1000)::bigint AS max_ms,
       round(sum(extract(epoch FROM finished_at - started_at)) * 1000)::bigint AS total_ms
  FROM job_runs
 WHERE job = 'rollup'
   AND finished_at IS NOT NULL
   AND started_at > now() - interval '48 hours'
 GROUP BY 1
 ORDER BY 1 DESC;

-- Did it keep working, as opposed to merely getting faster. A rewritten query
-- that silently returns nothing is also very fast.
SELECT status, count(*)
  FROM job_runs
 WHERE job = 'rollup' AND started_at > now() - interval '6 hours'
 GROUP BY 1;

-- And that the numbers it writes did not move. The three days in the window
-- are recomputed every run, so if the boundary rewrite shifted a day, the
-- counts for yesterday and the day before would have changed with it — while
-- days 4 and older were written by the old code and cannot have.
SELECT day, alerts_sent, messages_stored, listings_added, visitors, signups,
       computed_at
  FROM daily_stats
 ORDER BY day DESC
 LIMIT 6;

-- ── 4. the snapshot to re-read tomorrow ─────────────────────────────────
--
-- These counters are cumulative since `stats_reset`, so a single reading says
-- little. Keep this output, run it again in 24 hours, and the difference is
-- the answer. Deliberately not `pg_stat_reset()`: that would also clear the
-- dead-tuple counts autovacuum uses to decide when to run, which on a database
-- with a bloat question open is the wrong thing to throw away.

SELECT now()                                   AS taken_at,
       (SELECT stats_reset FROM pg_stat_database
         WHERE datname = current_database())    AS counting_since,
       relname                                 AS table_name,
       seq_scan, seq_tup_read, idx_scan,
       n_tup_upd, n_tup_hot_upd,
       round(n_tup_hot_upd::numeric / nullif(n_tup_upd, 0), 3) AS hot_ratio,
       n_dead_tup, n_live_tup
  FROM pg_stat_user_tables
 WHERE relname IN ('listings', 'notifications', 'source_messages',
                   'wa_messages', 'listing_sightings', 'site_visits')
 ORDER BY seq_tup_read DESC;

-- Is the new index being read at all. `idx_scan` here should climb by roughly
-- four per rollup run — three days of `listings_added` plus `district_days`.
SELECT indexrelname AS index_name, idx_scan, idx_tup_read,
       pg_size_pretty(pg_relation_size(indexrelid)) AS size
  FROM pg_stat_user_indexes
 WHERE indexrelname IN ('listings_added_at', 'notifications_sent_at',
                        'notifications_outcome_at', 'source_messages_stored_at',
                        'users_created_at', 'payments_created_at')
 ORDER BY idx_scan DESC;
