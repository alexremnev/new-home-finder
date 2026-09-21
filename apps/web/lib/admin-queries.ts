import { query } from "@/lib/db";

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

export async function byPrice(days: Range): Promise<Slice[]> {
  return query<Slice>(

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


export type Problem = {
  kind: string;
  detail: string;
  count: number;
  last_at: string | null;
};

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


export async function knownJobs(): Promise<string[]> {
  const rows = await query<{ job: string }>(
    `SELECT DISTINCT job FROM job_runs ORDER BY job`,
  );
  return rows.map((r) => r.job);
}


export type Delivery = {
  oldest_queued_mins: number | null;
  median_latency_secs: number | null;
  failed_24h: number;
  skipped_24h: number;
};

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

export type Person = {
  user_id: number;
  status: string;
  plan: string;
  plan_display: string | null;
  plan_until: string | null;
  joined: string;
  consent_at: string | null;
  consent_source: string | null;
  stopped_at: string | null;
  payment_ref: string | null;
  channel: string | null;
  address: string | null;
  verified_at: string | null;
  subscription_id: number | null;
  criteria: Record<string, unknown> | null;
  backfill_from: string | null;
  seeded_at: string | null;
  sent: number;
  queued: number;
  failed: number;
  withheld: number;
  last_sent_at: string | null;
  first_sent_at: string | null;
  paid_total_pence: number;
  paid_count: number;
  last_paid_at: string | null;
  comped_days: number;
};

export async function person(userId: number): Promise<Person | null> {
  const rows = await query<Person>(
    `SELECT u.id AS user_id, u.status, u.plan, p.display_name AS plan_display,
            u.plan_until::text, u.created_at::text AS joined, u.consent_at::text,
            u.consent_source, u.stopped_at::text, u.payment_ref,
            uc.channel, uc.address, uc.verified_at::text,
            s.id AS subscription_id, s.criteria,
            s.backfill_from::text, s.seeded_at::text,
            (SELECT count(*)::int FROM notifications n
              WHERE n.user_id = u.id AND n.status = 'sent')            AS sent,
            (SELECT count(*)::int FROM notifications n
              WHERE n.user_id = u.id AND n.status = 'queued')          AS queued,
            (SELECT count(*)::int FROM notifications n
              WHERE n.user_id = u.id AND n.status = 'failed')          AS failed,
            (SELECT count(*)::int FROM notifications n
              WHERE n.user_id = u.id AND n.status = 'skipped'
                AND n.error = 'share')                                 AS withheld,
            (SELECT max(n.sent_at)::text FROM notifications n
              WHERE n.user_id = u.id AND n.status = 'sent')            AS last_sent_at,
            (SELECT min(n.sent_at)::text FROM notifications n
              WHERE n.user_id = u.id AND n.status = 'sent')            AS first_sent_at,
            (SELECT coalesce(sum(pm.amount_pence), 0)::int FROM payments pm
              WHERE pm.user_id = u.id)                                 AS paid_total_pence,
            (SELECT count(*)::int FROM payments pm
              WHERE pm.user_id = u.id)                                 AS paid_count,
            (SELECT max(pm.created_at)::text FROM payments pm
              WHERE pm.user_id = u.id)                                 AS last_paid_at,
            (SELECT coalesce(sum((a.detail->>'days')::int), 0)::int
               FROM admin_actions a
              WHERE a.user_id = u.id AND a.action = 'extend_plan')      AS comped_days
       FROM users u
       LEFT JOIN plans p          ON p.key = u.plan
       LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
       LEFT JOIN subscriptions s  ON s.user_id = u.id AND s.active
      WHERE u.id = $1`,
    [userId],
  );
  return rows[0] ?? null;
}

export type Delivered = {
  id: number;
  status: string;
  error: string | null;
  created_at: string;
  sent_at: string | null;
  price_pcm: number | null;
  bedrooms: number | null;
  district: string | null;
  url: string | null;
};

export async function deliveredTo(userId: number, limit = 50): Promise<Delivered[]> {
  return query<Delivered>(
    `SELECT n.id, n.status, n.error, n.created_at::text, n.sent_at::text,
            l.price_pcm, l.bedrooms, l.postcode_district AS district, l.url
       FROM notifications n
       LEFT JOIN listings l ON l.id = n.listing_id
      WHERE n.user_id = $1
      ORDER BY n.created_at DESC
      LIMIT $2`,
    [userId, limit],
  );
}

export type PaidBy = {
  id: number;
  plan: string;
  amount_pence: number;
  provider: string;
  provider_ref: string | null;
  granted_days: number | null;
  created_at: string;
};

export async function paymentsBy(userId: number): Promise<PaidBy[]> {
  return query<PaidBy>(
    `SELECT id, plan, amount_pence, provider, provider_ref, granted_days,
            created_at::text
       FROM payments WHERE user_id = $1 ORDER BY created_at DESC`,
    [userId],
  );
}

export type Touch = {
  id: number;
  action: string;
  detail: Record<string, unknown>;
  created_at: string;
};

export async function historyOf(userId: number): Promise<Touch[]> {
  return query<Touch>(
    `SELECT id, action, detail, created_at::text
       FROM admin_actions WHERE user_id = $1 ORDER BY created_at DESC LIMIT 50`,
    [userId],
  );
}

export async function wantedBy(userId: number): Promise<string[]> {
  const rows = await query<{ channel: string }>(
    `SELECT channel FROM channel_interest WHERE user_id = $1 ORDER BY channel`,
    [userId],
  );
  return rows.map((r) => r.channel);
}

export async function sellablePlans(): Promise<{ key: string; display_name: string }[]> {
  return query<{ key: string; display_name: string }>(
    `SELECT key, display_name FROM plans ORDER BY price_pence, key`,
  );
}

export async function fromRollup(days: Range): Promise<{
  day: string;
  alerts_sent: number;
  alerts_withheld: number;
  visitors: number;
  listings_added: number;
  messages_stored: number;
  revenue_pence: number;
  computed_at: string | null;
}[]> {
  return query(
    `WITH span AS (
       SELECT generate_series(current_date - ($1::int - 1), current_date, interval '1 day')::date AS day
     )
     SELECT to_char(span.day, 'YYYY-MM-DD') AS day,
            coalesce(d.alerts_sent, 0)      AS alerts_sent,
            coalesce(d.alerts_withheld, 0)  AS alerts_withheld,
            coalesce(d.visitors, 0)         AS visitors,
            coalesce(d.listings_added, 0)   AS listings_added,
            coalesce(d.messages_stored, 0)  AS messages_stored,
            coalesce(d.revenue_pence, 0)    AS revenue_pence,
            d.computed_at::text
       FROM span LEFT JOIN daily_stats d ON d.day = span.day
      ORDER BY span.day`,
    [days],
  );
}

// ── the dashboard, on an hours window rather than whole days ──────────────
//
// Every query below takes hours, because "the last hour" is the question a
// dashboard is opened with and `make_interval(days => …)` cannot express it.

export type JobState = {
  job: string;
  last_at: string | null;
  last_status: string | null;
  last_error: string | null;
  runs: number;
  ok: number;
  bad: number;
  skipped: number;
  median_secs: number | null;
};

// Every job that has run in the window, plus the ones that should have. A job
// missing from job_runs is the interesting case — silence, not health — and a
// name that stopped existing should not haunt the page forever.
export const EXPECTED_JOBS = ["ingest", "scrape", "drain", "rollup", "report"] as const;

export async function jobStates(hours: number): Promise<JobState[]> {
  return query<JobState>(
    `WITH expected AS (SELECT unnest($2::text[]) AS job),
     seen AS (
       SELECT DISTINCT job FROM job_runs
        WHERE started_at > now() - make_interval(hours => $1::int)
     ),
     all_jobs AS (SELECT job FROM expected UNION SELECT job FROM seen),
     windowed AS (
       SELECT job, status, started_at, finished_at, error
         FROM job_runs
        WHERE started_at > now() - make_interval(hours => $1::int)
     ),
     SELECT a.job,
            -- Scalar subqueries rather than a CTE join: the cast has to be
            -- visible in the select list, and the index on started_at makes
            -- three of them cheap.
            (SELECT max(started_at)::text FROM job_runs j WHERE j.job = a.job)
              AS last_at,
            (SELECT j.status FROM job_runs j WHERE j.job = a.job
              ORDER BY j.started_at DESC LIMIT 1) AS last_status,
            (SELECT j.error FROM job_runs j WHERE j.job = a.job
              ORDER BY j.started_at DESC LIMIT 1) AS last_error,
            count(w.*)::int    AS runs,
            count(w.*) FILTER (WHERE w.status = 'ok')::int AS ok,
            count(w.*) FILTER (WHERE w.status IN ('failed', 'degraded'))::int AS bad,
            count(w.*) FILTER (WHERE w.status = 'skipped_locked')::int AS skipped,
            round(
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY extract(epoch FROM w.finished_at - w.started_at)
              )
            )::int AS median_secs
       FROM all_jobs a
       LEFT JOIN windowed w ON w.job = a.job
      GROUP BY a.job
      ORDER BY a.job`,
    [Math.round(hours), [...EXPECTED_JOBS]],
  ).catch(() => []);
}

export type RunPoint = { label: string; ok: number; bad: number };

// One column per bucket: green for clean runs, red for the rest. Reading a
// timeline is how you tell "it broke once" from "it has been broken all day".
export async function runPoints(hours: number, minutes: number): Promise<RunPoint[]> {
  return query<RunPoint>(
    `WITH span AS (
       SELECT generate_series(
                date_trunc('hour', now() - make_interval(hours => $1::int)),
                now(),
                make_interval(mins => $2::int)
              ) AS bucket
     )
     SELECT to_char(span.bucket AT TIME ZONE 'Europe/London', 'YYYY-MM-DD HH24:MI') AS label,
            count(r.*) FILTER (WHERE r.status = 'ok')::int AS ok,
            count(r.*) FILTER (WHERE r.status IN ('failed', 'degraded'))::int AS bad
       FROM span
       LEFT JOIN job_runs r
              ON r.started_at >= span.bucket
             AND r.started_at <  span.bucket + make_interval(mins => $2::int)
      GROUP BY span.bucket
      ORDER BY span.bucket`,
    [Math.round(hours), Math.round(minutes)],
  ).catch(() => []);
}

export type LogPage = { rows: Event[]; total: number };

export async function logPage(
  hours: number,
  filter: { job?: string; level?: string; q?: string },
  page: number,
  perPage = 10,
): Promise<LogPage> {
  const args = [
    Math.round(hours),
    filter.job ?? null,
    filter.level ?? null,
    filter.q ?? null,
  ];
  const where = `e.ts > now() - make_interval(hours => $1::int)
        AND ($2::text IS NULL OR r.job = $2)
        AND ($3::text IS NULL OR e.level = $3)
        AND ($4::text IS NULL OR e.message ILIKE '%' || $4 || '%')`;

  const [rows, counted] = await Promise.all([
    query<Event>(
      `SELECT e.id, e.ts::text, e.level, r.job, e.stage, e.source_key, e.message, e.ctx
         FROM job_events e JOIN job_runs r ON r.id = e.run_id
        WHERE ${where}
        ORDER BY e.ts DESC
        LIMIT $5 OFFSET $6`,
      [...args, perPage, Math.max(0, page - 1) * perPage],
    ).catch(() => []),
    query<{ total: number }>(
      `SELECT count(*)::int AS total
         FROM job_events e JOIN job_runs r ON r.id = e.run_id
        WHERE ${where}`,
      args,
    ).catch(() => []),
  ]);
  return { rows, total: counted[0]?.total ?? 0 };
}

export type SubscriberRow = {
  user_id: number;
  status: string;
  plan: string;
  plan_until: string | null;
  channel: string | null;
  last_inbound_at: string | null;
  districts: string | null;
  sent_window: number;
  sent_total: number;
  failed_window: number;
  last_sent: string | null;
  created_at: string;
};

export async function subscriberPage(
  hours: number,
  page: number,
  perPage = 20,
): Promise<{ rows: SubscriberRow[]; total: number }> {
  const [rows, counted] = await Promise.all([
    query<SubscriberRow>(
      `SELECT u.id AS user_id, u.status, u.plan,
              u.plan_until::text AS plan_until,
              uc.channel,
              uc.last_inbound_at::text AS last_inbound_at,
              s.label AS districts,
              (SELECT count(*)::int FROM notifications n
                WHERE n.user_id = u.id AND n.status = 'sent'
                  AND n.sent_at > now() - make_interval(hours => $1::int)) AS sent_window,
              (SELECT count(*)::int FROM notifications n
                WHERE n.user_id = u.id AND n.status = 'sent') AS sent_total,
              (SELECT count(*)::int FROM notifications n
                WHERE n.user_id = u.id AND n.status = 'failed'
                  AND n.created_at > now() - make_interval(hours => $1::int)) AS failed_window,
              (SELECT max(n.sent_at)::text FROM notifications n
                WHERE n.user_id = u.id AND n.status = 'sent') AS last_sent,
              u.created_at::text AS created_at
         FROM users u
         LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
         LEFT JOIN subscriptions s  ON s.user_id = u.id AND s.active
        WHERE u.status <> 'erased'
        ORDER BY u.created_at DESC
        LIMIT $2 OFFSET $3`,
      [Math.round(hours), perPage, Math.max(0, page - 1) * perPage],
    ).catch(() => []),
    query<{ total: number }>(
      `SELECT count(*)::int AS total FROM users WHERE status <> 'erased'`,
    ).catch(() => []),
  ]);
  return { rows, total: counted[0]?.total ?? 0 };
}

// Half-hour buckets, as asked: fine enough to show when the alerts actually
// arrive and coarse enough that a week still fits on one line.
export async function alertBuckets(
  userId: number,
  hours: number,
  minutes = 30,
): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series(
                date_trunc('hour', now() - make_interval(hours => $2::int)),
                now(),
                make_interval(mins => $3::int)
              ) AS bucket
     )
     SELECT to_char(span.bucket AT TIME ZONE 'Europe/London', 'YYYY-MM-DD HH24:MI') AS label,
            count(n.*)::int AS value
       FROM span
       LEFT JOIN notifications n
              ON n.user_id = $1
             AND n.status = 'sent'
             AND n.sent_at >= span.bucket
             AND n.sent_at <  span.bucket + make_interval(mins => $3::int)
      GROUP BY span.bucket
      ORDER BY span.bucket`,
    [userId, Math.round(hours), Math.round(minutes)],
  ).catch(() => []);
}

export type PaymentSummary = {
  taken_pence: number;
  payments: number;
  payers: number;
  refunds: number;
};

export async function paymentSummary(days: number): Promise<PaymentSummary> {
  const rows = await query<PaymentSummary>(
    `SELECT coalesce(sum(amount_pence) FILTER (WHERE amount_pence > 0), 0)::int
              AS taken_pence,
            count(*) FILTER (WHERE amount_pence > 0)::int AS payments,
            count(DISTINCT user_id)::int                  AS payers,
            count(*) FILTER (WHERE amount_pence < 0)::int AS refunds
       FROM payments
      WHERE created_at > now() - make_interval(days => $1::int)`,
    [Math.round(days)],
  ).catch(() => []);
  return rows[0] ?? { taken_pence: 0, payments: 0, payers: 0, refunds: 0 };
}

export async function paymentSeries(days: number): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series(
              (now() AT TIME ZONE 'Europe/London')::date - ($1::int - 1),
              (now() AT TIME ZONE 'Europe/London')::date,
              interval '1 day'
            )::date AS day
     )
     SELECT to_char(span.day, 'YYYY-MM-DD') AS label,
            coalesce(sum(p.amount_pence), 0)::int AS value
       FROM span
       LEFT JOIN payments p
              ON (p.created_at AT TIME ZONE 'Europe/London')::date = span.day
      GROUP BY span.day
      ORDER BY span.day`,
    [Math.round(days)],
  ).catch(() => []);
}

export async function paymentsByPlan(days: number): Promise<Slice[]> {
  return query<Slice>(
    `SELECT coalesce(pl.display_name, p.plan) AS label,
            sum(p.amount_pence)::int          AS value
       FROM payments p
       LEFT JOIN plans pl ON pl.key = p.plan
      WHERE p.created_at > now() - make_interval(days => $1::int)
      GROUP BY 1 ORDER BY 2 DESC`,
    [Math.round(days)],
  ).catch(() => []);
}

export async function paymentsByProvider(days: number): Promise<Slice[]> {
  return query<Slice>(
    `SELECT provider AS label, count(*)::int AS value
       FROM payments
      WHERE created_at > now() - make_interval(days => $1::int)
      GROUP BY 1 ORDER BY 2 DESC`,
    [Math.round(days)],
  ).catch(() => []);
}

// Live rather than from daily_stats, because an hour window cannot be answered
// by rows that are one per day. `daily_stats` keeps its job — the hourly report
// reads it, and it outlives the raw rows — but the dashboard asks the source.

export async function alertPoints(hours: number, minutes: number): Promise<Slice[]> {
  return bucketed(
    `notifications`,
    `sent_at`,
    `status = 'sent'`,
    hours,
    minutes,
  );
}

export async function intakePoints(hours: number, minutes: number): Promise<Slice[]> {
  return bucketed(`listings`, `first_seen_at`, `true`, hours, minutes);
}

export async function messagePoints(hours: number, minutes: number): Promise<Slice[]> {
  return bucketed(`source_messages`, `stored_at`, `true`, hours, minutes);
}

export async function unparseablePoints(hours: number, minutes: number): Promise<Slice[]> {
  return bucketed(
    `source_messages`,
    `stored_at`,
    `status = 'unparseable'`,
    hours,
    minutes,
  );
}

// One shape for all four. The table, column and predicate are written here and
// never come from a request, so there is nothing for a caller to inject.
async function bucketed(
  table: "notifications" | "listings" | "source_messages",
  column: "sent_at" | "first_seen_at" | "stored_at",
  predicate: string,
  hours: number,
  minutes: number,
): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series(
                date_trunc('hour', now() - make_interval(hours => $1::int)),
                now(),
                make_interval(mins => $2::int)
              ) AS bucket
     )
     SELECT to_char(span.bucket AT TIME ZONE 'Europe/London', 'YYYY-MM-DD HH24:MI') AS label,
            count(t.*)::int AS value
       FROM span
       LEFT JOIN ${table} t
              ON ${predicate}
             AND t.${column} >= span.bucket
             AND t.${column} <  span.bucket + make_interval(mins => $2::int)
      GROUP BY span.bucket
      ORDER BY span.bucket`,
    [Math.round(hours), Math.round(minutes)],
  ).catch(() => []);
}

// ── who visited the site ──────────────────────────────────────────────────
//
// `site_visits` holds one row per visitor per day, and the hash that identifies
// them is salted with the day — so "unique over a week" cannot be asked. What
// these return is unique visitors per day, which is the honest number, and the
// total is the sum of those days rather than a count of people.

export type VisitDay = { day: string; visitors: number; hits: number };

export async function visitorsByDay(days: number): Promise<VisitDay[]> {
  return query<VisitDay>(
    `SELECT day::text AS day,
            count(*)::int      AS visitors,
            sum(hits)::int     AS hits
       FROM site_visits
      WHERE day > (now() AT TIME ZONE 'Europe/London')::date - make_interval(days => $1::int)
      GROUP BY day
      ORDER BY day DESC`,
    [days],
  ).catch(() => []);
}

export type VisitSlice = { name: string | null; visitors: number };

export async function visitorsByCountry(days: number): Promise<VisitSlice[]> {
  return query<VisitSlice>(
    `SELECT country AS name, count(*)::int AS visitors
       FROM site_visits
      WHERE day > (now() AT TIME ZONE 'Europe/London')::date - make_interval(days => $1::int)
      GROUP BY country
      ORDER BY visitors DESC, name`,
    [days],
  ).catch(() => []);
}

export async function visitorsByDevice(days: number): Promise<VisitSlice[]> {
  return query<VisitSlice>(
    `SELECT device AS name, count(*)::int AS visitors
       FROM site_visits
      WHERE day > (now() AT TIME ZONE 'Europe/London')::date - make_interval(days => $1::int)
      GROUP BY device
      ORDER BY visitors DESC, name`,
    [days],
  ).catch(() => []);
}

// ── what each district produces ───────────────────────────────────────────
//
// Read from `district_days`, which the rollup keeps. Counted with no criteria
// beyond the district itself — the widest possible filter, so these are the
// ceiling a real filter is measured against rather than what anybody receives.

export type DistrictRow = {
  district: string;
  listings: number;
  days: number;
  max_day: number;
  // The true minimum for the window, which is zero for any district that was
  // silent on one of its days — a row only exists for a day that produced
  // something, so the smallest stored value would otherwise report the
  // quietest day the district *appeared* on and quietly overstate it.
  min_day: number;
};

// Sorting by the average would be sorting by the total: every district is
// divided by the same number of days, so the order is identical. The other two
// are genuinely different questions — which district spikes, and which one
// never goes quiet.
const SORTS = {
  max: "sum(listings) DESC",
  min: "sum(listings) ASC",
  peak: "max(listings) DESC",
  reliable: "min_day DESC",
} as const;
export type DistrictSort = keyof typeof SORTS;

const WINDOW_START =
  "(now() AT TIME ZONE 'Europe/London')::date - make_interval(days => $1::int)";

export async function districtDays(
  days: number,
  sort: DistrictSort,
  limit: number,
  offset: number,
): Promise<DistrictRow[]> {
  // Interpolated, not a parameter: it is an ordering rather than a value, and
  // it can only ever be one of the four from the map above.
  const order = SORTS[sort];
  return query<DistrictRow>(
    `WITH span AS (
        SELECT count(DISTINCT day)::int AS covered
          FROM district_days WHERE day > ${WINDOW_START}
     )
     SELECT district,
            sum(listings)::int AS listings,
            count(*)::int      AS days,
            max(listings)::int AS max_day,
            -- The rule lives here rather than in the page, so the table and
            -- every top five agree on what a minimum is.
            CASE WHEN count(*) = (SELECT covered FROM span)
                 THEN min(listings)::int ELSE 0 END AS min_day
       FROM district_days
      WHERE day > ${WINDOW_START}
      GROUP BY district
      ORDER BY ${order}, district
      LIMIT $2 OFFSET $3`,
    [days, limit, offset],
  ).catch(() => []);
}

export type DistrictWindow = {
  districts: number;
  listings: number;
  days_covered: number;
  busiest: string | null;
  computed_at: string | null;
};

export async function districtWindow(days: number): Promise<DistrictWindow> {
  const rows = await query<DistrictWindow>(
    `SELECT count(DISTINCT district)::int AS districts,
            coalesce(sum(listings), 0)::int AS listings,
            -- Days that actually have rows, not the width of the window: the
            -- history starts when the table was filled, so dividing by 30 on
            -- day three would understate every district by a factor of ten.
            count(DISTINCT day)::int AS days_covered,
            (SELECT d2.district FROM district_days d2
              WHERE d2.day > (now() AT TIME ZONE 'Europe/London')::date
                             - make_interval(days => $1::int)
              GROUP BY d2.district ORDER BY sum(d2.listings) DESC LIMIT 1) AS busiest,
            max(computed_at)::text AS computed_at
       FROM district_days
      WHERE day > (now() AT TIME ZONE 'Europe/London')::date
                  - make_interval(days => $1::int)`,
    [days],
  ).catch(() => []);
  return (
    rows[0] ?? {
      districts: 0, listings: 0, days_covered: 0, busiest: null, computed_at: null,
    }
  );
}

// One row per day for the chosen district, or for everything when none is named.
export async function districtTrend(
  days: number,
  district: string | null,
): Promise<{ day: string; listings: number }[]> {
  return query<{ day: string; listings: number }>(
    `SELECT day::text AS day, sum(listings)::int AS listings
       FROM district_days
      WHERE day > (now() AT TIME ZONE 'Europe/London')::date
                  - make_interval(days => $1::int)
        AND ($2::text IS NULL OR district = $2)
      GROUP BY day
      ORDER BY day`,
    [days, district],
  ).catch(() => []);
}

export type TopRecipient = {
  user_id: number;
  sent: number;
  channel: string | null;
  plan: string | null;
};

// Who receives the most. Counted on `sent_at`, so it is what actually went out
// rather than what was queued — and on WhatsApp, from October, this is also the
// list of who costs the most.
export async function topRecipients(
  days: number,
  limit: number,
): Promise<TopRecipient[]> {
  return query<TopRecipient>(
    `SELECT n.user_id,
            count(*)::int AS sent,
            uc.channel,
            coalesce(p.display_name, u.plan) AS plan
       FROM notifications n
       JOIN users u ON u.id = n.user_id
       LEFT JOIN plans p ON p.key = u.plan
       LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
      WHERE n.status = 'sent'
        AND (n.sent_at AT TIME ZONE 'Europe/London')::date
            > (now() AT TIME ZONE 'Europe/London')::date
              - make_interval(days => $1::int)
      GROUP BY n.user_id, uc.channel, p.display_name, u.plan
      ORDER BY count(*) DESC, n.user_id
      LIMIT $2`,
    [days, limit],
  ).catch(() => []);
}

// ── which source is actually producing ────────────────────────────────────
//
// A listing came from the Telegram feed exactly when a `source_messages` row
// points at it, and from a scraper when none does. Both write the portal into
// `listings.source_key`, so that column says *which site* and this says *how it
// reached us* — the two questions the panels answer.
//
// Bounded to 30 days on purpose: the panels ask "is this working now", and an
// unbounded max() over every listing is a scan for an answer nobody reads.

export type SourceFeed = {
  portal: string;
  from_feed: boolean;
  hour: number;
  day: number;
  week: number;
  newest: string | null;
};

export async function sourceFeeds(): Promise<SourceFeed[]> {
  return query<SourceFeed>(
    `WITH seen AS (
        SELECT l.source_key,
               l.first_seen_at,
               EXISTS (
                 SELECT 1 FROM source_messages m WHERE m.listing_id = l.id
               ) AS from_feed
          FROM listings l
         WHERE l.first_seen_at > now() - interval '30 days'
     )
     SELECT source_key AS portal,
            from_feed,
            count(*) FILTER (WHERE first_seen_at > now() - interval '1 hour')::int  AS hour,
            count(*) FILTER (WHERE first_seen_at > now() - interval '24 hours')::int AS day,
            count(*) FILTER (WHERE first_seen_at > now() - interval '7 days')::int   AS week,
            max(first_seen_at)::text AS newest
       FROM seen
      GROUP BY source_key, from_feed`,
  ).catch(() => []);
}
