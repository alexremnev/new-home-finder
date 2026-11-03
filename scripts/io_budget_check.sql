-- io_budget_check.sql — where the Disk IO is going. Read-only; paste into
-- Supabase → SQL Editor. Run it before applying 0075 and again a day after, so
-- the change has a before and an after rather than a hope.
--
-- Nothing here writes, and nothing here needs a superuser.

-- ── 1. what the working set actually is ──────────────────────────────────
--
-- The number to compare against the page cache. The dashboard showed cache and
-- buffers at roughly 200MB; every table larger than that cannot be held, so a
-- sequential scan of it is a disk read every single time.

SELECT relname                                           AS table_name,
       pg_size_pretty(pg_total_relation_size(c.oid))      AS total,
       pg_size_pretty(pg_relation_size(c.oid))            AS heap,
       pg_size_pretty(pg_indexes_size(c.oid))             AS indexes,
       pg_size_pretty(pg_total_relation_size(c.reltoastrelid)) AS toast,
       c.reltuples::bigint                                AS approx_rows
  FROM pg_class c
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
 ORDER BY pg_total_relation_size(c.oid) DESC
 LIMIT 15;

-- ── 2. who is being sequentially scanned, and how much it costs ──────────
--
-- `seq_tup_read` is the one to read: a table with a high scan count but few
-- rows is cheap, and a table scanned a thousand times a day with 300k rows in
-- it is the whole problem. Divide by the uptime in days for a daily rate.
--
-- Expect `listings`, `notifications`, `source_messages` and `wa_messages` at
-- the top before 0075 and well down it afterwards.

SELECT relname                        AS table_name,
       seq_scan,
       seq_tup_read,
       idx_scan,
       CASE WHEN seq_scan > 0
            THEN (seq_tup_read / seq_scan)::bigint END AS rows_per_scan,
       n_tup_ins, n_tup_upd, n_tup_del, n_tup_hot_upd,
       n_live_tup, n_dead_tup
  FROM pg_stat_user_tables
 ORDER BY seq_tup_read DESC
 LIMIT 15;

-- ── 3. the same question from the buffer cache's side ────────────────────
--
-- `heap_blks_read` is blocks that came from disk; `heap_blks_hit` came from
-- shared_buffers. A hit ratio under about 0.99 on a hot table means the cache
-- is losing the fight, which is exactly what a repeated full scan does to it.

SELECT relname AS table_name,
       heap_blks_read, heap_blks_hit,
       round(heap_blks_hit::numeric
             / nullif(heap_blks_hit + heap_blks_read, 0), 4) AS hit_ratio,
       idx_blks_read, idx_blks_hit
  FROM pg_statio_user_tables
 ORDER BY heap_blks_read DESC
 LIMIT 15;

-- ── 4. HOT update rate, which is where write IO hides ────────────────────
--
-- Every sweep upserts every listing it sees to refresh `last_seen_at`, so
-- `listings` takes hundreds of updates every few minutes. An update is HOT —
-- cheap, no index maintenance — only when no indexed column changed AND the
-- page had room. `listings` has nine indexes and the default fillfactor of
-- 100, which leaves no room, so a low ratio here means every one of those
-- updates is rewriting nine index entries and feeding autovacuum.
--
-- Under about 0.8 is worth acting on: see the fillfactor note in the summary.

SELECT relname AS table_name,
       n_tup_upd,
       n_tup_hot_upd,
       round(n_tup_hot_upd::numeric / nullif(n_tup_upd, 0), 3) AS hot_ratio,
       n_dead_tup,
       autovacuum_count, autoanalyze_count,
       last_autovacuum
  FROM pg_stat_user_tables
 WHERE n_tup_upd > 0
 ORDER BY n_tup_upd DESC
 LIMIT 15;

-- ── 5. indexes nobody reads ──────────────────────────────────────────────
--
-- This schema has accumulated some sixty of them over 74 migrations. Each one
-- is maintained on every insert and every non-HOT update, and each one takes a
-- share of the page cache that the tables then do not get. An index with
-- `idx_scan = 0` after a few days of uptime is pure cost.
--
-- Do not drop anything on this list without checking it is not a UNIQUE
-- constraint doing real work — `indisunique` is here so that is visible.

SELECT s.relname        AS table_name,
       s.indexrelname   AS index_name,
       s.idx_scan,
       i.indisunique    AS is_unique,
       pg_size_pretty(pg_relation_size(s.indexrelid)) AS size
  FROM pg_stat_user_indexes s
  JOIN pg_index i ON i.indexrelid = s.indexrelid
 WHERE s.idx_scan < 50
 ORDER BY pg_relation_size(s.indexrelid) DESC
 LIMIT 25;

-- ── 6. how long these numbers have been accumulating ─────────────────────
--
-- Everything above is since this moment. Without it, "40 million rows read"
-- means nothing — it could be a morning or a year.

SELECT stats_reset,
       now() - stats_reset AS counting_for
  FROM pg_stat_database
 WHERE datname = current_database();

-- ── 7. the rollup's own plan, before and after ───────────────────────────
--
-- The direct proof. Run this against the OLD predicate and the NEW one and
-- compare `Seq Scan` against `Index Only Scan`, and the `shared read` counts
-- under BUFFERS.
--
-- Old shape — a cast, so nothing can be used:
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*) FROM listings l
 WHERE l.first_seen_at::date = current_date AND l.duplicate_of IS NULL;

-- New shape — a half-open range, which `listings_added_at` answers:
EXPLAIN (ANALYZE, BUFFERS, COSTS OFF)
SELECT count(*) FROM listings l
 WHERE l.first_seen_at >= current_date::timestamptz
   AND l.first_seen_at <  (current_date + 1)::timestamptz
   AND l.duplicate_of IS NULL;
