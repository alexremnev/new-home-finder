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
    `SELECT u.id AS user_id, u.status, u.plan, p.display_name AS plan_display,
            u.plan_until, u.created_at AS joined,
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
            job || coalesce(' · ' || source_key, '') AS detail,
            count(*)::int AS count,
            max(started_at)::text AS last_at
       FROM job_runs
      WHERE status IN ('failed', 'degraded') AND started_at > now() - interval '48 hours'
      GROUP BY 1, 2

     UNION ALL
     SELECT 'error logged', left(message, 120), count(*)::int, max(created_at)::text
       FROM job_events
      WHERE level IN ('warn', 'error') AND created_at > now() - interval '48 hours'
      GROUP BY 1, 2

     UNION ALL
     SELECT 'unparseable', coalesce(left(parse_error, 120), 'no reason recorded'),
            count(*)::int, max(received_at)::text
       FROM source_messages
      WHERE status = 'unparseable' AND received_at > now() - interval '7 days'
      GROUP BY 1, 2

     UNION ALL
     -- Silence. Not an error anywhere, and the most likely thing to be wrong.
     SELECT 'nothing read', 'no message stored in the last 6 hours', 1,
            max(received_at)::text
       FROM source_messages
      HAVING max(received_at) < now() - interval '6 hours'

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
