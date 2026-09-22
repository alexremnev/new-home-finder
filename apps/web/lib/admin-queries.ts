import { query } from "@/lib/db";

export type Range = 1 | 7 | 30 | 90;

/**
 * The dashboard's chosen time range, in the two shapes the tables need.
 *
 * Built by `spanFrom` in app/admin/(dash)/span.ts and passed straight through,
 * so every page and every query means the same thing by "1w".
 *
 *   * `mins` / `endMins` — minutes before now, for the timestamp columns.
 *     `endMins` is 0 for every range that ends now, which is all of them except
 *     Yesterday. Both bounds are needed because Yesterday ends at midnight.
 *   * `fromDay` / `toDay` — inclusive London dates, for the tables keyed on a
 *     DATE: `site_visits.day` and `district_days.day`.
 */
export type Win = {
  mins: number;
  endMins: number;
  fromDay: string;
  toDay: string;
  days: number;
  hours: number;
};

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

export async function byDistrict(win: Win, limit = 12): Promise<Slice[]> {
  return query<Slice>(
    `SELECT coalesce(l.postcode_district, '—') AS label, count(*)::int AS value
       FROM notifications n
       JOIN listings l ON l.id = n.listing_id
      WHERE n.status = 'sent'
        AND n.sent_at >  now() - make_interval(mins => $1::int)
        AND n.sent_at <= now() - make_interval(mins => $2::int)
      GROUP BY 1
      ORDER BY 2 DESC
      LIMIT $3`,
    [Math.round(win.mins), Math.round(win.endMins), limit],
  );
}

export async function byPrice(win: Win): Promise<Slice[]> {
  return query<Slice>(

    `WITH banded AS (
       SELECT (floor(l.price_pcm / 250.0) * 250)::int AS band
         FROM notifications n
         JOIN listings l ON l.id = n.listing_id
        WHERE n.status = 'sent'
          AND n.sent_at >  now() - make_interval(mins => $1::int)
          AND n.sent_at <= now() - make_interval(mins => $2::int)
     )
     SELECT band::text AS label, count(*)::int AS value
       FROM banded
      GROUP BY band
      ORDER BY band`,
    [Math.round(win.mins), Math.round(win.endMins)],
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

export async function jobStates(win: Win): Promise<JobState[]> {
  return query<JobState>(
    `WITH expected AS (SELECT unnest($2::text[]) AS job),
     seen AS (
       SELECT DISTINCT job FROM job_runs
        WHERE started_at >  now() - make_interval(mins => $1::int)
          AND started_at <= now() - make_interval(mins => $3::int)
     ),
     all_jobs AS (SELECT job FROM expected UNION SELECT job FROM seen),
     windowed AS (
       SELECT job, status, started_at, finished_at, error
         FROM job_runs
        WHERE started_at >  now() - make_interval(mins => $1::int)
          AND started_at <= now() - make_interval(mins => $3::int)
     )
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
    [Math.round(win.mins), [...EXPECTED_JOBS], Math.round(win.endMins)],
  ).catch(() => []);
}

export type RunPoint = { label: string; ok: number; bad: number };

// One column per bucket: green for clean runs, red for the rest. Reading a
// timeline is how you tell "it broke once" from "it has been broken all day".
export async function runPoints(win: Win, minutes: number): Promise<RunPoint[]> {
  return query<RunPoint>(
    `WITH span AS (
       SELECT generate_series(
                date_trunc('hour', now() - make_interval(mins => $1::int)),
                now() - make_interval(mins => $3::int),
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
    [Math.round(win.mins), Math.round(minutes), Math.round(win.endMins)],
  ).catch(() => []);
}

export type LogPage = { rows: Event[]; total: number };

export async function logPage(
  win: Win,
  filter: { job?: string; level?: string; q?: string },
  page: number,
  perPage = 10,
): Promise<LogPage> {
  const args = [
    Math.round(win.mins),
    filter.job ?? null,
    filter.level ?? null,
    filter.q ?? null,
    Math.round(win.endMins),
  ];
  const where = `e.ts >  now() - make_interval(mins => $1::int)
        AND e.ts <= now() - make_interval(mins => $5::int)
        AND ($2::text IS NULL OR r.job = $2)
        AND ($3::text IS NULL OR e.level = $3)
        AND ($4::text IS NULL OR e.message ILIKE '%' || $4 || '%')`;

  const [rows, counted] = await Promise.all([
    query<Event>(
      `SELECT e.id, e.ts::text, e.level, r.job, e.stage, e.source_key, e.message, e.ctx
         FROM job_events e JOIN job_runs r ON r.id = e.run_id
        WHERE ${where}
        ORDER BY e.ts DESC
        LIMIT $6 OFFSET $7`,
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
  win: Win,
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
                  AND n.sent_at >  now() - make_interval(mins => $1::int)
                  AND n.sent_at <= now() - make_interval(mins => $2::int)) AS sent_window,
              (SELECT count(*)::int FROM notifications n
                WHERE n.user_id = u.id AND n.status = 'sent') AS sent_total,
              (SELECT count(*)::int FROM notifications n
                WHERE n.user_id = u.id AND n.status = 'failed'
                  AND n.created_at >  now() - make_interval(mins => $1::int)
                  AND n.created_at <= now() - make_interval(mins => $2::int)) AS failed_window,
              (SELECT max(n.sent_at)::text FROM notifications n
                WHERE n.user_id = u.id AND n.status = 'sent') AS last_sent,
              u.created_at::text AS created_at
         FROM users u
         LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
         LEFT JOIN subscriptions s  ON s.user_id = u.id AND s.active
        WHERE u.status <> 'erased'
        ORDER BY u.created_at DESC
        LIMIT $3 OFFSET $4`,
      [
        Math.round(win.mins),
        Math.round(win.endMins),
        perPage,
        Math.max(0, page - 1) * perPage,
      ],
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
  win: Win,
  minutes = 30,
): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series(
                date_trunc('hour', now() - make_interval(mins => $2::int)),
                now() - make_interval(mins => $4::int),
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
    [userId, Math.round(win.mins), Math.round(minutes), Math.round(win.endMins)],
  ).catch(() => []);
}

export type PaymentSummary = {
  taken_pence: number;
  payments: number;
  payers: number;
  refunds: number;
};

export async function paymentSummary(win: Win): Promise<PaymentSummary> {
  const rows = await query<PaymentSummary>(
    `SELECT coalesce(sum(amount_pence) FILTER (WHERE amount_pence > 0), 0)::int
              AS taken_pence,
            count(*) FILTER (WHERE amount_pence > 0)::int AS payments,
            count(DISTINCT user_id)::int                  AS payers,
            count(*) FILTER (WHERE amount_pence < 0)::int AS refunds
       FROM payments
      WHERE created_at >  now() - make_interval(mins => $1::int)
        AND created_at <= now() - make_interval(mins => $2::int)`,
    [Math.round(win.mins), Math.round(win.endMins)],
  ).catch(() => []);
  return rows[0] ?? { taken_pence: 0, payments: 0, payers: 0, refunds: 0 };
}

export async function paymentSeries(win: Win): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series($1::date, $2::date, interval '1 day')::date AS day
     )
     SELECT to_char(span.day, 'YYYY-MM-DD') AS label,
            coalesce(sum(p.amount_pence), 0)::int AS value
       FROM span
       LEFT JOIN payments p
              ON (p.created_at AT TIME ZONE 'Europe/London')::date = span.day
      GROUP BY span.day
      ORDER BY span.day`,
    [win.fromDay, win.toDay],
  ).catch(() => []);
}

export async function paymentsByPlan(win: Win): Promise<Slice[]> {
  return query<Slice>(
    `SELECT coalesce(pl.display_name, p.plan) AS label,
            sum(p.amount_pence)::int          AS value
       FROM payments p
       LEFT JOIN plans pl ON pl.key = p.plan
      WHERE p.created_at >  now() - make_interval(mins => $1::int)
        AND p.created_at <= now() - make_interval(mins => $2::int)
      GROUP BY 1 ORDER BY 2 DESC`,
    [Math.round(win.mins), Math.round(win.endMins)],
  ).catch(() => []);
}

export async function paymentsByProvider(win: Win): Promise<Slice[]> {
  return query<Slice>(
    `SELECT provider AS label, count(*)::int AS value
       FROM payments
      WHERE created_at >  now() - make_interval(mins => $1::int)
        AND created_at <= now() - make_interval(mins => $2::int)
      GROUP BY 1 ORDER BY 2 DESC`,
    [Math.round(win.mins), Math.round(win.endMins)],
  ).catch(() => []);
}

// Live rather than from daily_stats, because an hour window cannot be answered
// by rows that are one per day. `daily_stats` keeps its job — the hourly report
// reads it, and it outlives the raw rows — but the dashboard asks the source.

export async function alertPoints(win: Win, minutes: number): Promise<Slice[]> {
  return bucketed(
    `notifications`,
    `sent_at`,
    `status = 'sent'`,
    win,
    minutes,
  );
}

export async function intakePoints(win: Win, minutes: number): Promise<Slice[]> {
  return bucketed(`listings`, `first_seen_at`, `true`, win, minutes);
}

export async function messagePoints(win: Win, minutes: number): Promise<Slice[]> {
  return bucketed(`source_messages`, `stored_at`, `true`, win, minutes);
}

export async function unparseablePoints(win: Win, minutes: number): Promise<Slice[]> {
  return bucketed(
    `source_messages`,
    `stored_at`,
    `status = 'unparseable'`,
    win,
    minutes,
  );
}

// One shape for all four. The table, column and predicate are written here and
// never come from a request, so there is nothing for a caller to inject.
async function bucketed(
  table: "notifications" | "listings" | "source_messages" | "site_visits",
  column: "sent_at" | "first_seen_at" | "stored_at" | "first_at",
  predicate: string,
  win: Win,
  minutes: number,
): Promise<Slice[]> {
  return query<Slice>(
    `WITH span AS (
       SELECT generate_series(
                date_trunc('hour', now() - make_interval(mins => $1::int)),
                now() - make_interval(mins => $3::int),
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
    [Math.round(win.mins), Math.round(minutes), Math.round(win.endMins)],
  ).catch(() => []);
}

// ── who visited the site ──────────────────────────────────────────────────
//
// `site_visits` holds one row per visitor per day, and the hash that identifies
// them is salted with the day — so "unique over a week" cannot be asked. What
// these return is unique visitors per day, which is the honest number, and the
// total is the sum of those days rather than a count of people.

export type VisitDay = { day: string; visitors: number; hits: number };

export async function visitorsByDay(win: Win): Promise<VisitDay[]> {
  return query<VisitDay>(
    `SELECT day::text AS day,
            count(*)::int      AS visitors,
            sum(hits)::int     AS hits
       FROM site_visits
      WHERE day BETWEEN $1::date AND $2::date
      GROUP BY day
      ORDER BY day DESC`,
    [win.fromDay, win.toDay],
  ).catch(() => []);
}

export type VisitSlice = { name: string | null; visitors: number };

export async function visitorsByCountry(win: Win): Promise<VisitSlice[]> {
  return query<VisitSlice>(
    `SELECT country AS name, count(*)::int AS visitors
       FROM site_visits
      WHERE day BETWEEN $1::date AND $2::date
      GROUP BY country
      ORDER BY visitors DESC, name`,
    [win.fromDay, win.toDay],
  ).catch(() => []);
}

export async function visitorsByDevice(win: Win): Promise<VisitSlice[]> {
  return query<VisitSlice>(
    `SELECT device AS name, count(*)::int AS visitors
       FROM site_visits
      WHERE day BETWEEN $1::date AND $2::date
      GROUP BY device
      ORDER BY visitors DESC, name`,
    [win.fromDay, win.toDay],
  ).catch(() => []);
}

// ── what each district produces ───────────────────────────────────────────
//
// The rows come back per day and unaggregated, and the page does the windowing,
// sorting and paging in the browser. One fetch of a month of days is a few
// thousand small rows; the alternative was a server round trip for every range
// and every column heading, which is what made the page feel like it reloaded.
//
// Counted with no criteria beyond the district itself — the widest possible
// filter, so these are the ceiling a real filter is measured against rather
// than what anybody receives.

export type DistrictDay = { day: string; district: string; listings: number };

export async function districtDaily(win: Win): Promise<DistrictDay[]> {
  return query<DistrictDay>(
    `SELECT day::text AS day, district, listings
       FROM district_days
      WHERE day BETWEEN $1::date AND $2::date
      ORDER BY day`,
    [win.fromDay, win.toDay],
  ).catch(() => []);
}

export type RecipientDay = {
  day: string;
  user_id: number;
  sent: number;
  channel: string | null;
  plan: string | null;
  districts: string[] | null;
};

export async function recipientDaily(win: Win): Promise<RecipientDay[]> {
  // Per day, like the districts, so the page can answer every range without
  // asking again. `districts` is what the person actually subscribed to — the
  // question asked of every name in this list.
  return query<RecipientDay>(
    `SELECT (n.sent_at AT TIME ZONE 'Europe/London')::date::text AS day,
            n.user_id,
            count(*)::int AS sent,
            max(uc.channel) AS channel,
            max(coalesce(p.display_name, u.plan)) AS plan,
            (ARRAY(
               SELECT DISTINCT upper(area)
                 FROM subscriptions s
                 CROSS JOIN LATERAL jsonb_array_elements_text(
                     coalesce(s.criteria->'areas'->'postcode_districts', '[]'::jsonb)
                 ) AS area
                WHERE s.user_id = n.user_id AND s.active
             )) AS districts
       FROM notifications n
       JOIN users u ON u.id = n.user_id
       LEFT JOIN plans p ON p.key = u.plan
       LEFT JOIN user_channels uc ON uc.user_id = u.id AND uc.is_primary
      WHERE n.status = 'sent'
        AND (n.sent_at AT TIME ZONE 'Europe/London')::date
            BETWEEN $1::date AND $2::date
      GROUP BY day, n.user_id`,
    [win.fromDay, win.toDay],
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

export type Duplicates = {
  // Copies suppressed over the window, and how many listings arrived in total,
  // so the panel can state a share rather than a bare number nobody can size.
  copies: number;
  listings: number;
  // Which portal the copy came from, paired with the portal we kept. Ordered by
  // how often the pair occurs, because that is what says where the overlap is.
  pairs: { copy: string; kept: string; copies: number }[];
};

/**
 * How much of the intake was the same flat arriving twice.
 *
 * A copy is a listing whose `duplicate_of` was set at parse time: same
 * postcode, rent, bedrooms and bathrooms as an earlier listing first seen the
 * same London day, from a different portal. See 0040 for why the rule is what
 * it is, and why two from one portal are not copies.
 */
export async function duplicates(win: Win): Promise<Duplicates> {
  const [totals, pairs] = await Promise.all([
    query<{ copies: string | number; listings: string | number }>(
      `SELECT count(*) FILTER (WHERE duplicate_of IS NOT NULL) AS copies,
              count(*) AS listings
         FROM listings
        WHERE first_seen_at >  now() - make_interval(mins => $1::int)
          AND first_seen_at <= now() - make_interval(mins => $2::int)`,
      [Math.round(win.mins), Math.round(win.endMins)],
    ).catch(() => []),
    query<{ copy: string; kept: string; copies: string | number }>(
      `SELECT copy.source_key AS copy,
              kept.source_key AS kept,
              count(*) AS copies
         FROM listings copy
         JOIN listings kept ON kept.id = copy.duplicate_of
        WHERE copy.first_seen_at >  now() - make_interval(mins => $1::int)
          AND copy.first_seen_at <= now() - make_interval(mins => $2::int)
        GROUP BY 1, 2
        ORDER BY count(*) DESC, 1, 2
        LIMIT 8`,
      [Math.round(win.mins), Math.round(win.endMins)],
    ).catch(() => []),
  ]);

  return {
    copies: Number(totals[0]?.copies ?? 0),
    listings: Number(totals[0]?.listings ?? 0),
    pairs: pairs.map((one) => ({
      copy: one.copy,
      kept: one.kept,
      copies: Number(one.copies),
    })),
  };
}

// Visitors over time, bucketed as coarsely as the range deserves: by hour for
// today, and by day for a week. `first_at` is when somebody arrived, so one
// visitor lands in exactly one bucket — the row itself is one per person per
// day, which is why counting rows counts people.
export async function visitorPoints(win: Win, minutes: number): Promise<Slice[]> {
  return bucketed(`site_visits`, `first_at`, `true`, win, minutes);
}

export async function visitorsByBrowser(win: Win): Promise<VisitSlice[]> {
  return query<VisitSlice>(
    `SELECT browser AS name, count(*)::int AS visitors
       FROM site_visits
      WHERE day BETWEEN $1::date AND $2::date
      GROUP BY browser
      ORDER BY visitors DESC, name`,
    [win.fromDay, win.toDay],
  ).catch(() => []);
}

// How much the scraper has downloaded over a window.
//
// Read from `job_stages.counters`, which the stage already writes — the run log
// is per-stage and timestamped, so it answers "over this period" without a
// table of its own.
//
// The guard on `jsonb_typeof` is there so a counter that is somehow not a
// number cannot break the page with a cast error.
export async function scrapeBytes(win: Win, source: string): Promise<number> {
  const rows = await query<{ bytes: string | number | null }>(
    `SELECT coalesce(sum((counters->>'bytes')::bigint), 0) AS bytes
       FROM job_stages
      WHERE stage = 'scrape'
        AND source_key = $2
        AND jsonb_typeof(counters->'bytes') = 'number'
        AND started_at >  now() - make_interval(mins => $1::int)
        AND started_at <= now() - make_interval(mins => $3::int)`,
    [Math.round(win.mins), source, Math.round(win.endMins)],
  ).catch(() => []);
  return Number(rows[0]?.bytes ?? 0);
}
