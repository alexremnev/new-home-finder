// Everything the console shows, as SQL.
//
// One module, and it only reads — the single write the console can do lives in its
// own route, where it is audited. Keeping them apart means this file can be read as
// "what the admin can see" without also having to check what it changes.
//
// Every count is computed in Postgres rather than by fetching rows and counting
// them in JavaScript. Not for speed at this size, but because the alternative sends
// every subscriber's filter to the server that renders the page in order to produce
// a single number.

import { query } from "@/lib/db";

/** Days back, from the console's range control. */
export type Range = 1 | 7 | 30 | 90;

export type Overview = {
  subscribers: number;
  active_filters: number;
  sent_total: number;
  sent_today: number;
  queued: number;
  visitors_30d: number;
  revenue_pence: number;
};

export async function overview(): Promise<Overview> {
  const rows = await query<Overview>(
    `SELECT
       (SELECT count(*)::int FROM users WHERE status = 'active')          AS subscribers,
       (SELECT count(*)::int FROM subscriptions WHERE active)             AS active_filters,
       (SELECT count(*)::int FROM notifications WHERE status = 'sent')    AS sent_total,
       (SELECT count(*)::int FROM notifications
         WHERE status = 'sent' AND sent_at >= current_date)               AS sent_today,
       (SELECT count(*)::int FROM notifications WHERE status = 'queued')  AS queued,
       -- The sum of daily uniques, which is what a daily-salted hash can honestly
       -- give. Not distinct people over the month: see 0017 for why.
       (SELECT count(*)::int FROM site_visits
         WHERE day > current_date - 30)                                   AS visitors_30d,
       (SELECT coalesce(sum(amount_pence), 0)::int FROM payments)         AS revenue_pence`,
  );
  return (
    rows[0] ?? {
      subscribers: 0, active_filters: 0, sent_total: 0, sent_today: 0,
      queued: 0, visitors_30d: 0, revenue_pence: 0,
    }
  );
}

export type Slice = { label: string; value: number };

/**
 * Who is on which plan. Identity, so this one is drawn with categorical colour.
 *
 * Ordered by the plan's key and NOT by how many people are on it. Ordering by count
 * would make the colour follow the rank: the day trial overtakes free, the two swap
 * hues and every earlier screenshot becomes a lie. Alphabetical by key is stable
 * whatever the numbers do, which is the only property the ordering needs.
 */
export async function planMix(): Promise<Slice[]> {
  return query<Slice>(
    `SELECT coalesce(p.display_name, u.plan) AS label, count(*)::int AS value
       FROM users u
       LEFT JOIN plans p ON p.key = u.plan
      WHERE u.status = 'active'
      GROUP BY u.plan, p.display_name
      ORDER BY u.plan`,
  );
}

/**
 * Alerts actually delivered, per day.
 *
 * `generate_series` so a day with nothing sent is a zero rather than a gap. A line
 * that skips its empty days is a line that lies about its shape.
 */
export async function sentPerDay(days: Range): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series(current_date - ($1::int - 1), current_date, interval '1 day')::date AS day
     )
     SELECT to_char(span.day, 'YYYY-MM-DD') AS label,
            count(n.id)::int                AS value
       FROM span
       LEFT JOIN notifications n
              ON n.status = 'sent' AND n.sent_at::date = span.day
      GROUP BY span.day
      ORDER BY span.day`,
    [days],
  );
}

export async function visitsPerDay(days: Range): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series(current_date - ($1::int - 1), current_date, interval '1 day')::date AS day
     )
     SELECT to_char(span.day, 'YYYY-MM-DD') AS label,
            coalesce(count(v.visitor_hash), 0)::int AS value
       FROM span
       LEFT JOIN site_visits v ON v.day = span.day
      GROUP BY span.day
      ORDER BY span.day`,
    [days],
  );
}

/** Which districts the delivered alerts were in. One measure, so one colour. */
export async function byDistrict(days: Range, limit = 12): Promise<Slice[]> {
  return query<Slice>(
    `SELECT coalesce(l.postcode_district, '—') AS label, count(*)::int AS value
       FROM notifications n
       JOIN listings l ON l.id = n.listing_id
      WHERE n.status = 'sent'
        AND n.sent_at > now() - make_interval(days => $1::int)
      GROUP BY 1
      ORDER BY 2 DESC
      LIMIT $2`,
    [days, limit],
  );
}

/**
 * What they cost, in £250 bands.
 *
 * Bands rather than exact prices because the question is "what price of flat are we
 * actually sending", and 400 distinct prices answers it worse than 12 bands do.
 */
export async function byPrice(days: Range): Promise<Slice[]> {
  return query<Slice>(
    // Grouped by the number and ordered by the number, then cast for the label.
    // Grouping by the text and ordering by the expression is the natural way to
    // write this and Postgres rejects it — the expression is not in the grouping.
    `WITH banded AS (
       SELECT (floor(l.price_pcm / 250.0) * 250)::int AS band
         FROM notifications n
         JOIN listings l ON l.id = n.listing_id
        WHERE n.status = 'sent'
          AND n.sent_at > now() - make_interval(days => $1::int)
     )
     SELECT band::text AS label, count(*)::int AS value
       FROM banded
      GROUP BY band
      ORDER BY band`,
    [days],
  );
}

export type Subscriber = {
  user_id: number;
  status: string;
  plan: string;
  plan_display: string | null;
  plan_until: string | null;
  channel: string | null;
  criteria: Record<string, unknown> | null;
  sent: number;
  withheld: number;
  joined: string;
};

export async function subscribers(): Promise<Subscriber[]> {
  return query<Subscriber>(
    // Cast to text, like every other timestamp this module returns. Postgres hands
    // a TIMESTAMPTZ to the driver as a JS Date, so a field typed `string` here is a
    // Date at runtime and the first `.slice` on it throws — a mismatch TypeScript
    // cannot see, because the type is an assertion about a value it never inspects.
    `SELECT u.id AS user_id, u.status, u.plan, p.display_name AS plan_display,
            u.plan_until::text, u.created_at::text AS joined,
            uc.channel, s.criteria,
            (SELECT count(*)::int FROM notifications n
              WHERE n.user_id = u.id AND n.status = 'sent')                 AS sent,
            -- What the plan's share held back. Shown beside what was sent because
            -- one without the other says nothing about whether the tier is working.
            (SELECT count(*)::int FROM notifications n
              WHERE n.user_id = u.id AND n.status = 'skipped' AND n.error = 'share') AS withheld
       FROM users u
       LEFT JOIN plans p          ON p.key = u.plan
       LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
       LEFT JOIN subscriptions s  ON s.user_id = u.id AND s.active
      ORDER BY u.created_at DESC
      LIMIT 200`,
  );
}

export type Problem = {
  kind: string;
  detail: string;
  count: number;
  last_at: string | null;
};

/**
 * What is wrong, in one list.
 *
 * Four different questions, unioned, because an admin opening this page wants one
 * answer to "is anything broken" and not four panels to compare. The silence checks
 * matter most: a component that has stopped doing anything reports no errors at all,
 * which is exactly why it needs asking about separately.
 */
export async function problems(): Promise<Problem[]> {
  return query<Problem>(
    `SELECT 'run failed' AS kind,
            -- job_runs records the job and the error, and no source: a run is a
            -- job, and which source it touched is a property of its stages.
            job || coalesce(' · ' || left(error, 90), '') AS detail,
            count(*)::int AS count,
            max(started_at)::text AS last_at
       FROM job_runs
      WHERE status IN ('failed', 'degraded') AND started_at > now() - interval '48 hours'
      GROUP BY 1, 2

     UNION ALL
     -- The column here is ts, not created_at. Every other table uses created_at,
     -- which is exactly why this one is easy to get wrong.
     SELECT 'error logged',
            coalesce(stage || ': ', '') || left(message, 110),
            count(*)::int, max(ts)::text
       FROM job_events
      WHERE level IN ('warn', 'error') AND ts > now() - interval '48 hours'
      GROUP BY 1, 2

     UNION ALL
     SELECT 'unparseable', coalesce(left(parse_error, 110), 'no reason recorded'),
            count(*)::int, max(received_at)::text
       FROM source_messages
      WHERE status = 'unparseable' AND received_at > now() - interval '7 days'
      GROUP BY 1, 2

     UNION ALL
     -- Silence. Not an error anywhere, and the most likely thing to be wrong.
     --
     -- Measured on stored_at rather than received_at: the first is when we
     -- stored something, the second is when the feed posted it. A broken reader
     -- leaves stored_at old while the feed carries on, and that is the failure
     -- worth catching — the one nothing else reports.
     SELECT 'nothing read', 'no message stored in the last 6 hours', 1,
            max(stored_at)::text
       FROM source_messages
      HAVING max(stored_at) < now() - interval '6 hours'

     UNION ALL
     SELECT 'delivery stalled', 'queued and waiting', count(*)::int, min(created_at)::text
       FROM notifications
      WHERE status = 'queued' AND created_at < now() - interval '1 hour'
      HAVING count(*) > 0

      ORDER BY 4 DESC NULLS LAST
      LIMIT 40`,
  );
}

export type Payment = {
  id: number;
  user_id: number;
  plan: string;
  amount_pence: number;
  provider: string;
  granted_days: number | null;
  granted_by: string | null;
  created_at: string;
};

export async function recentPayments(): Promise<Payment[]> {
  return query<Payment>(
    `SELECT id, user_id, plan, amount_pence, provider, granted_days, granted_by,
            created_at::text
       FROM payments
      ORDER BY created_at DESC
      LIMIT 50`,
  );
}

export type Comp = {
  id: number;
  user_id: number | null;
  detail: Record<string, unknown>;
  created_at: string;
};

export async function recentComps(): Promise<Comp[]> {
  return query<Comp>(
    `SELECT id, user_id, detail, created_at::text
       FROM admin_actions
      WHERE action = 'extend_plan'
      ORDER BY created_at DESC
      LIMIT 20`,
  );
}

export type Run = {
  id: number;
  job: string;
  trigger: string;
  status: string;
  started_at: string;
  seconds: number | null;
  counters: Record<string, unknown>;
  error: string | null;
};

/**
 * The last runs of each job, side by side.
 *
 * Both jobs in one list and not two panels: the question is almost always "did both
 * of them run", and two panels means comparing two clocks. Sorted newest first, so
 * the top two rows are the answer.
 *
 * `counters` comes back whole rather than picked apart here. Which counters a job
 * emits is the job's business — `read`, `parsed`, `queued` for ingest, `sent` and
 * `seeded` for drain — and a query that named them would need editing every time a
 * stage learned to count something new.
 */
export async function recentRuns(limit = 20): Promise<Run[]> {
  return query<Run>(
    `SELECT id, job, trigger, status,
            started_at::text,
            round(extract(epoch FROM finished_at - started_at)::numeric, 1)::float8 AS seconds,
            counters, error
       FROM job_runs
      ORDER BY started_at DESC
      LIMIT $1`,
    [limit],
  );
}

export type JobHealth = {
  job: string;
  last_at: string | null;
  last_status: string | null;
  ok_24h: number;
  failed_24h: number;
};

/** One row per job: when it last ran, and how it has been getting on since. */
export async function jobHealth(): Promise<JobHealth[]> {
  return query<JobHealth>(
    `SELECT job,
            max(started_at)::text AS last_at,
            -- The status of the most recent run, not of the whole day: "is it
            -- working now" is the question, and a count of failures answers a
            -- different one.
            (array_agg(status ORDER BY started_at DESC))[1] AS last_status,
            count(*) FILTER (WHERE status = 'ok'
                               AND started_at > now() - interval '24 hours')::int AS ok_24h,
            count(*) FILTER (WHERE status IN ('failed', 'degraded')
                               AND started_at > now() - interval '24 hours')::int AS failed_24h
       FROM job_runs
      GROUP BY job
      ORDER BY job`,
  );
}

// ── the log ────────────────────────────────────────────────────────────────

export type Filters = {
  range: Range;
  job?: string;
  level?: string;
  q?: string;
};

export type Event = {
  id: number;
  ts: string;
  level: string;
  job: string;
  stage: string | null;
  source_key: string | null;
  message: string;
  ctx: Record<string, unknown>;
};

/**
 * The event log, filtered.
 *
 * `job_events` has been written since the first migration and nothing has ever read
 * it except a hand-typed query. That is the gap this closes: the worker has produced
 * a structured, queryable log all along, with a run id, a stage and a JSON context on
 * every line — it just had no window.
 *
 * Every filter is optional and applied as `($n IS NULL OR …)`. One statement handles
 * every combination, so there is no query built from strings and no chance of a
 * filter that silently does nothing because a branch was missed.
 *
 * The text search is `ILIKE` over the message, not a full-text index. At this volume
 * a sequential scan over two days of events is milliseconds, and an index would be a
 * thing to maintain for a search nobody runs twice.
 */
export async function events(filter: Filters, limit = 300): Promise<Event[]> {
  return query<Event>(
    `SELECT e.id, e.ts::text, e.level, r.job, e.stage, e.source_key, e.message, e.ctx
       FROM job_events e
       JOIN job_runs r ON r.id = e.run_id
      WHERE e.ts > now() - make_interval(days => $1::int)
        AND ($2::text IS NULL OR r.job = $2)
        AND ($3::text IS NULL OR e.level = $3)
        AND ($4::text IS NULL OR e.message ILIKE '%' || $4 || '%')
      ORDER BY e.ts DESC
      LIMIT $5`,
    [filter.range, filter.job ?? null, filter.level ?? null, filter.q ?? null, limit],
  );
}

/** How many of each level, for the filter bar's counts. */
export async function eventCounts(filter: Filters): Promise<Slice[]> {
  return query<Slice>(
    `SELECT e.level AS label, count(*)::int AS value
       FROM job_events e
       JOIN job_runs r ON r.id = e.run_id
      WHERE e.ts > now() - make_interval(days => $1::int)
        AND ($2::text IS NULL OR r.job = $2)
      GROUP BY 1
      ORDER BY 2 DESC`,
    [filter.range, filter.job ?? null],
  );
}

/** The jobs that have ever run, for the filter bar. Not a constant: a job added
 *  tomorrow should appear without an edit here. */
export async function knownJobs(): Promise<string[]> {
  const rows = await query<{ job: string }>(
    `SELECT DISTINCT job FROM job_runs ORDER BY job`,
  );
  return rows.map((r) => r.job);
}

// ── metrics worth a chart ──────────────────────────────────────────────────

/**
 * The share of messages the parser could not read, per day.
 *
 * A share and not two counts. The two numbers on one chart would be a second scale
 * away from a dual axis, and the question is not "how many arrived" — that is the
 * ingest chart — but "is the parser keeping up with the format". A rising line means
 * the feed changed and nobody noticed.
 */
export async function unparseableShare(days: Range): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series(current_date - ($1::int - 1), current_date, interval '1 day')::date AS day
     )
     SELECT to_char(span.day, 'YYYY-MM-DD') AS label,
            coalesce(
              round(
                100.0 * count(m.id) FILTER (WHERE m.status = 'unparseable')
                / nullif(count(m.id), 0)
              )::int,
              0
            ) AS value
       FROM span
       LEFT JOIN source_messages m ON m.stored_at::date = span.day
      GROUP BY span.day
      ORDER BY span.day`,
    [days],
  );
}

export type Delivery = {
  oldest_queued_mins: number | null;
  median_latency_secs: number | null;
  failed_24h: number;
  skipped_24h: number;
};

/**
 * Is delivery keeping up?
 *
 * The oldest queued item is the number that matters. An average queue length says
 * nothing — a queue of ten that turns over every minute is healthy and a queue of ten
 * that has not moved for an hour is broken, and only the age tells them apart.
 *
 * Latency is measured as sent minus created, which includes the wait in the queue.
 * That is the number a subscriber experiences: a listing that appeared twenty minutes
 * ago is twenty minutes stale however fast the send itself was.
 */
export async function delivery(): Promise<Delivery> {
  const rows = await query<Delivery>(
    `SELECT
       (SELECT round(extract(epoch FROM now() - min(created_at)) / 60)::int
          FROM notifications WHERE status = 'queued')            AS oldest_queued_mins,
       (SELECT round(
                 percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY extract(epoch FROM sent_at - created_at)
                 )
               )::int
          FROM notifications
         WHERE status = 'sent' AND sent_at > now() - interval '24 hours')
                                                                 AS median_latency_secs,
       (SELECT count(*)::int FROM notifications
         WHERE status = 'failed' AND created_at > now() - interval '24 hours')
                                                                 AS failed_24h,
       (SELECT count(*)::int FROM notifications
         WHERE status = 'skipped' AND created_at > now() - interval '24 hours')
                                                                 AS skipped_24h`,
  );
  return (
    rows[0] ?? {
      oldest_queued_mins: null, median_latency_secs: null, failed_24h: 0, skipped_24h: 0,
    }
  );
}
