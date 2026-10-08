import { query } from "@/lib/db";

export type Range = 1 | 7 | 30 | 90;

/**
 * The dashboard's chosen time range, in the two shapes the tables need.
 *
 * Built by `spanFrom` in app/admin/(dash)/span.ts and passed straight through,
 * so every page and every query means the same thing by "1w".
 *
 *   * `mins` / `endMins` — minutes before now, for the timestamp columns.
 *     `endMins` is 0 for a rolling window, which is every preset; both bounds
 *     are needed because an absolute range can also have ended in the past.
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

export async function byDistrict(
  win: Win,
  limit = 12,
  offset = 0,
): Promise<Slice[]> {
  return query<Slice>(
    `SELECT coalesce(l.postcode_district, '—') AS label, count(*)::int AS value
       FROM notifications n
       JOIN listings l ON l.id = n.listing_id
      WHERE n.status = 'sent'
        AND n.sent_at >  now() - make_interval(mins => $1::int)
        AND n.sent_at <= now() - make_interval(mins => $2::int)
      GROUP BY 1
      ORDER BY 2 DESC
      LIMIT $3 OFFSET $4`,
    [Math.round(win.mins), Math.round(win.endMins), limit, offset],
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

/**
 * The checks that look past a job's exit code, over the window the dashboard
 * is set to.
 *
 * Every check used to carry its own period — failures over 48 hours, silence
 * over 6, unparseable messages over a week — regardless of the picker. It read
 * as a bug: with "Past 30 minutes" chosen the panel still listed a run that
 * failed two days ago, and nothing on the row said why it was there. So all
 * five now answer the same question as the rest of the page, "what happened in
 * this range", and the two that are about a state rather than an event say so
 * in the only way a window allows:
 *
 *   * `nothing read` needs a window of at least six hours. On a shorter one it
 *     cannot tell a broken reader from a quiet feed — the channel goes minutes
 *     without a message at the best of times, and overnight it goes hours —
 *     and a check that cries wolf on every short window is worse than absent.
 *     Its timestamp is the last message ever stored, which is deliberately
 *     outside the window: that is the fact you need.
 *   * `delivery stalled` counts what was queued inside the window and has
 *     since sat for over an hour. Nothing queued in the past half hour can be
 *     an hour old, so a short window is empty here, and correctly so.
 */
export async function problems(win: Win): Promise<Problem[]> {
  return query<Problem>(
    `SELECT 'run failed' AS kind,
            -- job_runs records the job and the error, and no source: a run is a
            -- job, and which source it touched is a property of its stages.
            job || coalesce(' · ' || left(error, 90), '') AS detail,
            count(*)::int AS count,
            max(started_at)::text AS last_at
       FROM job_runs
      WHERE status IN ('failed', 'degraded')
        AND started_at >  now() - make_interval(mins => $1::int)
        AND started_at <= now() - make_interval(mins => $2::int)
      GROUP BY 1, 2

     UNION ALL
     -- One row per job, not one per distinct message.
     --
     -- Grouped by message it read as thirty-eight separate problems when it
     -- was one problem happening on thirty-eight runs, and the list is capped,
     -- so a single persistent fault pushed every other kind of problem off the
     -- panel. What you need to know is which job is unhappy and what it last
     -- said; the run log carries the rest.
     --
     -- The column here is ts, not created_at. Every other table uses
     -- created_at, which is exactly why this one is easy to get wrong.
     SELECT 'error logged',
            r.job
              || coalesce(' · ' || e.stage, '')
              || ' · ' || left(
                   (array_agg(e.message ORDER BY e.id DESC))[1], 110
                 )
              || CASE WHEN count(DISTINCT e.message) > 1
                      THEN ' (+' || (count(DISTINCT e.message) - 1) || ' other)'
                      ELSE '' END,
            count(*)::int, max(e.ts)::text
       FROM job_events e
       JOIN job_runs r ON r.id = e.run_id
      WHERE e.level IN ('warn', 'error')
        AND e.ts >  now() - make_interval(mins => $1::int)
        AND e.ts <= now() - make_interval(mins => $2::int)
      GROUP BY 1, r.job, e.stage

     UNION ALL
     SELECT 'unparseable', coalesce(left(parse_error, 110), 'no reason recorded'),
            count(*)::int, max(received_at)::text
       FROM source_messages
      WHERE status = 'unparseable'
        AND received_at >  now() - make_interval(mins => $1::int)
        AND received_at <= now() - make_interval(mins => $2::int)
      GROUP BY 1, 2

     UNION ALL
     -- Silence. Not an error anywhere, and the most likely thing to be wrong.
     --
     -- Measured on stored_at rather than received_at: the first is when we
     -- stored something, the second is when the feed posted it. A broken reader
     -- leaves stored_at old while the feed carries on, and that is the failure
     -- worth catching — the one nothing else reports.
     --
     -- The six hours below is a floor on the window, not on the silence: see
     -- the note above this query for why a half-hour window cannot ask this.
     -- The date beside this row is the last message ever stored, which is
     -- the one timestamp on the panel that sits outside the range. No point
     -- repeating it in the text: the when column already renders it, in
     -- London, which a to_char here would not have been.
     SELECT 'nothing read', 'nothing stored in this range',
            1, max(stored_at)::text
       FROM source_messages
      WHERE stored_at <= now() - make_interval(mins => $2::int)
     HAVING $1::int - $2::int >= 6 * 60
        AND max(stored_at) < now() - make_interval(mins => $1::int)

     UNION ALL
     -- held IS NULL, or this says "stalled" about a WhatsApp backlog that is
     -- waiting for a shut window to reopen, which is the system working and
     -- lasts up to two days. See 0055; the Held tile is where those are shown.
     SELECT 'delivery stalled', 'queued with nothing holding them',
            count(*)::int, min(created_at)::text
       FROM queued_notifications
      WHERE held IS NULL
        AND created_at <  now() - interval '1 hour'
        AND created_at >  now() - make_interval(mins => $1::int)
        AND created_at <= now() - make_interval(mins => $2::int)
     HAVING count(*) > 0

      ORDER BY 4 DESC NULLS LAST
      LIMIT 40`,
    [Math.round(win.mins), Math.round(win.endMins)],
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

export async function recentPayments(limit = 25, offset = 0): Promise<Payment[]> {
  return query<Payment>(
    `SELECT id, user_id, plan, amount_pence, provider, granted_days, granted_by,
            created_at::text
       FROM payments
      ORDER BY created_at DESC
      LIMIT $1 OFFSET $2`,
    [limit, offset],
  );
}

export type Comp = {
  id: number;
  user_id: number | null;
  detail: Record<string, unknown>;
  created_at: string;
};

export async function recentComps(limit = 10, offset = 0): Promise<Comp[]> {
  return query<Comp>(
    `SELECT id, user_id, detail, created_at::text
       FROM admin_actions
      WHERE action = 'extend_plan'
      ORDER BY created_at DESC
      LIMIT $1 OFFSET $2`,
    [limit, offset],
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

export async function recentRuns(limit = 20, offset = 0): Promise<Run[]> {
  return query<Run>(
    `SELECT id, job, trigger, status,
            started_at::text,
            round(extract(epoch FROM finished_at - started_at)::numeric, 1)::float8 AS seconds,
            counters, error
       FROM job_runs
      ORDER BY started_at DESC
      LIMIT $1 OFFSET $2`,
    [limit, offset],
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


// What the log filter offers. Bounded to a month, because a retired job name
// otherwise haunts the dropdown forever: `hot` was removed with the in-worker
// scheduler in migration 0021 and cannot run, but its old `job_runs` rows kept
// listing it as though it could.
export async function knownJobs(): Promise<string[]> {
  const rows = await query<{ job: string }>(
    `SELECT DISTINCT job FROM job_runs
      WHERE started_at > now() - interval '30 days'
      ORDER BY job`,
  );
  return rows.map((r) => r.job);
}


export type Delivery = {
  oldest_queued_mins: number | null;
  held_total: number;
  held_oldest_mins: number | null;
  held_why: string | null;
  median_latency_secs: number | null;
  failed_24h: number;
  skipped_24h: number;
};

// `oldest_queued_mins` asks one question: has delivery stopped? So it ages only
// what delivery could send this minute. A WhatsApp message waiting for a shut
// window, for an unanswered check-in, for a photograph or for midnight is held
// on purpose and can be held for two days — counting those made the tile red
// almost always, which is the same as having no tile.
//
// `held` is the other half of the same row, kept beside it so the dashboard
// never has to choose between "nothing is wrong" and "nothing is moving". The
// definition of held is `queued_notifications` in the database, which is also
// what the worker claims against — see 0055.
export async function delivery(): Promise<Delivery> {
  const rows = await query<Delivery>(
    // Referenced four times, so Postgres materialises it and works the reasons
    // out once. Inlined it would re-run the per-row subqueries for each column.
    `WITH queued AS (SELECT created_at, held FROM queued_notifications)
     SELECT
       (SELECT round(extract(epoch FROM now() - min(created_at)) / 60)::int
          FROM queued WHERE held IS NULL)                        AS oldest_queued_mins,
       (SELECT count(*)::int FROM queued WHERE held IS NOT NULL) AS held_total,
       (SELECT round(extract(epoch FROM now() - min(created_at)) / 60)::int
          FROM queued WHERE held IS NOT NULL)                    AS held_oldest_mins,
       (SELECT string_agg(reason, ' · ' ORDER BY reason)
          FROM (SELECT held || ' ×' || count(*) AS reason
                  FROM queued WHERE held IS NOT NULL
                 GROUP BY held) breakdown)                       AS held_why,
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
      oldest_queued_mins: null, held_total: 0, held_oldest_mins: null, held_why: null,
      median_latency_secs: null, failed_24h: 0, skipped_24h: 0,
    }
  );
}

export type Person = {
  user_id: number;
  status: string;
  plan: string;
  plan_display: string | null;
  plan_until: string | null;
  plan_from: string | null;
  // Why alerts stopped, when the date alone does not explain it: a WhatsApp
  // month ends at thirty days or at nine hundred alerts. Without these two the
  // commonest support question — "my subscription has weeks left and nothing
  // is arriving" — has no answer on this page.
  alert_allowance: number | null;
  alerts_used: number;
  plan_live: boolean;
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
            u.plan_until::text, u.plan_from::text,
            e.alert_allowance, e.alerts_used, e.live AS plan_live,
            u.created_at::text AS joined, u.consent_at::text,
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
       LEFT JOIN plans p             ON p.key = u.plan
       LEFT JOIN user_entitlement e  ON e.user_id = u.id
       LEFT JOIN user_channels uc    ON uc.user_id = u.id AND uc.is_primary
       LEFT JOIN subscriptions s     ON s.user_id = u.id AND s.active
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

export async function deliveredTo(
  userId: number,
  limit = 25,
  offset = 0,
): Promise<Delivered[]> {
  return query<Delivered>(
    `SELECT n.id, n.status, n.error, n.created_at::text, n.sent_at::text,
            l.price_pcm, l.bedrooms, l.postcode_district AS district, l.url
       FROM notifications n
       LEFT JOIN listings l ON l.id = n.listing_id
      WHERE n.user_id = $1
      ORDER BY n.created_at DESC
      LIMIT $2 OFFSET $3`,
    [userId, limit, offset],
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

export async function historyOf(
  userId: number,
  limit = 15,
  offset = 0,
): Promise<Touch[]> {
  return query<Touch>(
    `SELECT id, action, detail, created_at::text
       FROM admin_actions
      WHERE user_id = $1
      ORDER BY created_at DESC
      LIMIT $2 OFFSET $3`,
    [userId, limit, offset],
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
  //: Runs still marked 'running' long after they started.
  //:
  //: A process killed outright — a fatal cffi error, an OOM, a machine going
  //: to sleep — cannot come back to write its own result, so its row stays
  //: 'running' for ever and is counted in none of the three above. That is
  //: how a job that died eleven times showed 0 failed.
  stuck: number;
  median_secs: number | null;
  //: Whether the window is long enough for this job to have been expected.
  //:
  //: False only for a job whose timer fires less often than the window is
  //: long — `purge`, nightly, on anything under a day. Silence from one of
  //: those is not evidence of anything, so the banner ignores it and the tile
  //: says "not due" rather than "silent".
  due: boolean;
};

// Beyond this a run marked 'running' is not running. The longest job here
// finishes inside a couple of minutes; fifteen is generous enough that a slow
// sweep is never mislabelled.
export const STUCK_AFTER_MINUTES = 15;

// Every job a timer starts, with how often that timer fires, plus whatever
// else has run in the window. A job missing from `job_runs` is the interesting
// case — silence, not health — and a name that stopped existing should not
// haunt the page forever.
//
// One entry per portal reader, not one combined `portals` job. That is the
// point of the split: a job that has stopped shows as silent here, where
// combined it hid behind the two that still worked.
//
// ── why a period, and not just a list of names ───────────────────────────
//
// A job missing from the window is drawn as silent, and silence takes the
// whole banner to Degraded with it. That is right for a reader that ticks
// every few minutes and wrong for `purge`, which fires once at one in the
// morning: on any window shorter than a day it has legitimately not run, so
// listed flatly it made the dashboard permanently, pointlessly red — which is
// why it was left off the list altogether and then missing from the panel.
//
// With a period, absence only counts against a job on a window at least as
// long as its own: any window of a day contains 01:00, so a silent `purge`
// there is real, and on a half-hour window the tile says "not due" instead of
// dragging the banner down. Every job keeps its tile either way.
//
// The minutes are read off the timers in deploy/systemd — the gap each one
// leaves between fires, during the hours it fires at all. The readers pause
// overnight, which is a longer gap than any number here, so a window at four
// in the morning can still call one silent; that is as it was before this
// list grew a period, and a separate question from the one above.
export const SCHEDULED_JOBS: { job: string; everyMins: number }[] = [
  { job: "ingest", everyMins: 2 },
  { job: "drain", everyMins: 2 },
  { job: "rollup", everyMins: 2 },
  { job: "zoopla_london", everyMins: 5 },
  { job: "rightmove", everyMins: 10 },
  { job: "zoopla", everyMins: 20 },
  { job: "openrent", everyMins: 20 },
  { job: "report", everyMins: 60 },
  { job: "purge", everyMins: 24 * 60 },
];

// `portals` runs every reader in one go and nothing schedules it — it is there
// for a manual sweep. A tile for it therefore answers no question this panel
// asks: it appeared only because somebody had run it by hand, and said nothing
// about whether anything is working. Hidden here rather than left to age out,
// because what it was is the first thing it makes you ask. Runs of it are
// still in Last runs and the run log, which is where a run you started
// yourself belongs.
const UNSCHEDULED_JOBS = ["portals"];

export async function jobStates(win: Win): Promise<JobState[]> {
  const rows = await query<Omit<JobState, "due">>(
    `WITH expected AS (SELECT unnest($2::text[]) AS job),
     seen AS (
       SELECT DISTINCT job FROM job_runs
        WHERE started_at >  now() - make_interval(mins => $1::int)
          AND started_at <= now() - make_interval(mins => $3::int)
          AND job <> ALL($5::text[])
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
            count(w.*) FILTER (
              WHERE w.status = 'running'
                AND w.started_at < now() - make_interval(mins => $4::int)
            )::int AS stuck,
            round(
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY extract(epoch FROM w.finished_at - w.started_at)
              )
            )::int AS median_secs
       FROM all_jobs a
       LEFT JOIN windowed w ON w.job = a.job
      GROUP BY a.job
      ORDER BY a.job`,
    [
      Math.round(win.mins),
      SCHEDULED_JOBS.map((one) => one.job),
      Math.round(win.endMins),
      STUCK_AFTER_MINUTES,
      UNSCHEDULED_JOBS,
    ],
  ).catch(() => []);

  const length = win.mins - win.endMins;
  const period = new Map(SCHEDULED_JOBS.map((one) => [one.job, one.everyMins]));
  // A name not on the list got here by running, so it is due by construction:
  // nothing that could have been silent is excused by this default.
  return rows.map((row) => ({
    ...row,
    due: (period.get(row.job) ?? 0) <= length,
  }));
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

export type RunLine = {
  id: number;
  job: string;
  host: string | null;
  started_at: string;
  secs: number | null;
  status: string;
  trigger: string;
  counters: Record<string, unknown> | null;
  error: string | null;
  //: How many warn or error lines this run logged.
  notes: number;
  //: The worst of them, errors before warnings, most recent first.
  worst: string | null;
  worst_level: string | null;
  worst_stage: string | null;
};

export type RunLog = { rows: RunLine[]; total: number };

// One row per run, not one per log line.
//
// The event log is the right shape for reading a single run closely and the
// wrong shape for seeing how things are going: a district sweep writes a line
// per portal per problem, so a fault that persists fills the page with the
// same sentence and pushes everything else off it. One line that says "this
// job ran, here is how long it took, here is what it did, here is the worst
// thing it said" is what you actually want to scan.
//
// The detail is not lost — the message below is the worst line of the run, and
// the run id leads to the rest.
export async function runLog(
  win: Win,
  filter: { job?: string; bad?: boolean; q?: string },
  page: number,
  perPage = 12,
): Promise<RunLog> {
  const args = [
    Math.round(win.mins),
    filter.job ?? null,
    filter.bad ? true : null,
    filter.q ?? null,
    Math.round(win.endMins),
    STUCK_AFTER_MINUTES,
  ];

  // `bad` means the run is worth a look: it failed, it degraded, it logged
  // something, or it never came back at all. A run killed outright cannot
  // write its own result, so "still running long after it started" belongs
  // here too — that is the shape a crash leaves behind.
  const where = `r.started_at >  now() - make_interval(mins => $1::int)
        AND r.started_at <= now() - make_interval(mins => $5::int)
        AND ($2::text IS NULL OR r.job = $2)
        AND ($4::text IS NULL OR r.error ILIKE '%' || $4 || '%'
             OR EXISTS (SELECT 1 FROM job_events e
                         WHERE e.run_id = r.id
                           AND e.message ILIKE '%' || $4 || '%'))
        AND ($3::bool IS NULL
             OR r.status IN ('failed', 'degraded')
             OR (r.status = 'running'
                 AND r.started_at < now() - make_interval(mins => $6::int))
             OR EXISTS (SELECT 1 FROM job_events e
                         WHERE e.run_id = r.id
                           AND e.level IN ('warn', 'error')))`;

  const [rows, counted] = await Promise.all([
    query<RunLine>(
      `SELECT r.id,
              r.job,
              r.host,
              r.started_at::text,
              round(extract(epoch FROM r.finished_at - r.started_at))::int AS secs,
              r.status,
              r.trigger,
              r.counters,
              left(r.error, 300) AS error,
              coalesce(n.notes, 0)::int AS notes,
              left(n.message, 300) AS worst,
              n.level AS worst_level,
              n.stage AS worst_stage
         FROM job_runs r
         -- One pass over this run's complaints: how many, and the worst.
         -- Errors before warnings, then most recent — so a run that failed
         -- shows why rather than showing its last routine warning.
         LEFT JOIN LATERAL (
             SELECT count(*) OVER () AS notes, e.message, e.level, e.stage
               FROM job_events e
              WHERE e.run_id = r.id AND e.level IN ('warn', 'error')
              ORDER BY CASE e.level WHEN 'error' THEN 0 ELSE 1 END, e.id DESC
              LIMIT 1
         ) AS n ON true
        WHERE ${where}
        ORDER BY r.started_at DESC
        LIMIT $7 OFFSET $8`,
      [...args, perPage, Math.max(0, page - 1) * perPage],
    ).catch(() => []),
    query<{ total: number }>(
      `SELECT count(*)::int AS total FROM job_runs r WHERE ${where}`,
      args,
    ).catch(() => []),
  ]);
  return { rows, total: counted[0]?.total ?? 0 };
}

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

export const WA_DAILY_ALERT = 30;

export type SubscriberRow = {
  user_id: number;
  /** WhatsApp messages sent to them today, in London days. */
  wa_today: number;
  status: string;
  plan: string;
  plan_until: string | null;
  /** Whether the plan is live — days AND allowance. See 0057. */
  plan_live: boolean;
  alert_allowance: number | null;
  alerts_used: number;
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
              e.live AS plan_live, e.alert_allowance, e.alerts_used,
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
              -- WhatsApp today, on its own: Meta bills per message, and thirty
              -- in a day is the figure that stops delivery for somebody who is
              -- not paying. Counted in London days, like every other date here.
              (SELECT count(*)::int FROM notifications n
                WHERE n.user_id = u.id AND n.channel = 'whatsapp'
                  AND n.status = 'sent'
                  AND (n.sent_at AT TIME ZONE 'Europe/London')::date
                    = (now() AT TIME ZONE 'Europe/London')::date) AS wa_today,
              u.created_at::text AS created_at
         FROM users u
         LEFT JOIN user_entitlement e ON e.user_id = u.id
         LEFT JOIN user_channels uc   ON uc.user_id = u.id AND uc.is_primary
         LEFT JOIN subscriptions s    ON s.user_id = u.id AND s.active
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
//
// ── which rows the range covers ──────────────────────────────────────────
//
// `first_at`, the instant the visitor first appeared — the same column, and so
// the same arithmetic, as the chart above the tiles. These used to read whole
// London dates instead, which answered a different question from the one the
// picker asked: "past 1 day" is a 24-hour window and it touches two dates, so
// both were read whole, up to 48 hours of visitors went into a tile labelled
// one day, and the tiles disagreed with the chart beside them.
//
// Not `day`, even as a cheap prefilter on its index: that column is written
// from `toISOString()`, so it is a UTC date, and between midnight and 01:00
// London in summer it names the day before. A range inside that hour would
// have excluded exactly the rows it was asking for. The table holds one row per
// visitor per day, so scanning it is not something worth being clever about.
//
// `hits` is the one number the window cannot cut finely: it is a per-day
// counter on the visitor's row, so a visitor who arrived inside the window
// brings that whole day's page views with them.

/** The window, as the two clauses every visitor query filters by. */
const VISIT_WINDOW = `first_at >  now() - make_interval(mins => $1::int)
        AND first_at <= now() - make_interval(mins => $2::int)`;

function visitWindow(win: Win): [number, number] {
  return [Math.round(win.mins), Math.round(win.endMins)];
}

export type VisitDay = { day: string; visitors: number; hits: number };

export async function visitorsByDay(win: Win): Promise<VisitDay[]> {
  return query<VisitDay>(
    `SELECT day::text AS day,
            count(*)::int      AS visitors,
            sum(hits)::int     AS hits
       FROM site_visits
      WHERE ${VISIT_WINDOW}
      GROUP BY day
      ORDER BY day DESC`,
    visitWindow(win),
  ).catch(() => []);
}

export type VisitSlice = { name: string | null; visitors: number };

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

export type SourceSwitch = {
  key: string;
  display_name: string;
  enabled: boolean;
  announces: boolean;
};

// The two switches per source, because every number on the coverage panels
// means something different depending on them. `enabled` is whether we read a
// source; `announces` is whether what it finds may reach a subscriber. See
// 0060 — and in particular why a muted feed makes the alert-level comparison
// vacuous rather than green.
export async function sourceSwitches(): Promise<SourceSwitch[]> {
  return query<SourceSwitch>(
    `SELECT key, display_name, enabled, announces
       FROM sources ORDER BY key`,
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

// One shape for every "break the visits down by this column" panel. The column
// name is chosen from a fixed list here and never comes from a request, so
// there is nothing for a caller to inject.
type VisitFacet =
  | "source" | "referrer" | "campaign" | "city" | "region"
  | "os" | "language" | "country" | "device" | "browser";

export async function visitorsBy(
  win: Win,
  facet: VisitFacet,
  limit = 12,
  offset = 0,
): Promise<VisitSlice[]> {
  const columns: Record<VisitFacet, string> = {
    source: "source", referrer: "referrer", campaign: "campaign",
    city: "city", region: "region", os: "os", language: "language",
    country: "country", device: "device", browser: "browser",
  };
  const column = columns[facet];
  if (!column) return [];

  return query<VisitSlice>(
    `SELECT ${column} AS name, count(*)::int AS visitors
       FROM site_visits
      WHERE ${VISIT_WINDOW}
      GROUP BY ${column}
      ORDER BY visitors DESC, name
      LIMIT $3 OFFSET $4`,
    [...visitWindow(win), limit, offset],
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
export type ReaderTally = {
  reader: string;
  saw: number;
  got_first: number;
};

// How many listings each reader actually brought in, which is not the same as
// how many it stored.
//
// A scraper that finds a flat the Telegram feed posted a minute earlier stores
// nothing — the row is already there — so `stored` undercounts what it found.
// `saw` is everything it saw; `got_first` is what it got to before any other
// reader, and that is the honest measure of what each one contributes.
//
// Scoped to listings whose first sighting by anybody falls in the window, so
// that the ranking is over the whole race and not over the part of it that
// happens to fall inside the period being looked at.
export async function readerTally(win: Win): Promise<ReaderTally[]> {
  return query<ReaderTally>(
    `WITH inwin AS (
       SELECT listing_id
         FROM listing_sightings
        GROUP BY listing_id
       HAVING min(first_at) >  now() - make_interval(mins => $1::int)
          AND min(first_at) <= now() - make_interval(mins => $2::int)
     ),
     ranked AS (
       SELECT g.reader,
              row_number() OVER (
                PARTITION BY g.listing_id ORDER BY g.first_at, g.reader
              ) AS place
         FROM listing_sightings g
         JOIN inwin USING (listing_id)
     )
     SELECT reader,
            count(*)::int AS saw,
            count(*) FILTER (WHERE place = 1)::int AS got_first
       FROM ranked
      GROUP BY reader
      ORDER BY reader`,
    [Math.round(win.mins), Math.round(win.endMins)],
  ).catch(() => []);
}

export type ReaderOverlap = {
  portal: string;
  total: number;
  both: number;
  feed_only: number;
  scraper_only: number;
  feed_only_covered: number;
  scraper_first: number;
  feed_first: number;
  median_lead_secs: number | null;
};

// Feed against scraper, per portal, so the Telegram source can be retired on
// evidence. See 0052 for why this cannot be read off `listings`.
//
// Grouped by external_id rather than by listing id: the feed and a portal's
// own scraper upsert into one row, so the row cannot say which of them saw it
// first, and `listing_sightings` is where that is recorded. See 0052.
//
// `feed_only_covered` is the number that actually matters. A listing only the
// feed saw is uninteresting if it was in a district no subscriber has chosen —
// the scrapers only read subscribed districts, so that is coverage working as
// designed, not a miss. The ones in a *covered* district are real misses.
export async function readerOverlap(win: Win): Promise<ReaderOverlap[]> {
  return query<ReaderOverlap>(
    `WITH covered AS (
       SELECT DISTINCT upper(area) AS code
         FROM subscriptions s
         CROSS JOIN LATERAL jsonb_array_elements_text(
             coalesce(s.criteria->'areas'->'postcode_districts', '[]'::jsonb)
         ) AS area
        WHERE s.active
     ),
     fam AS (
       SELECT l.id,
              l.external_id,
              l.source_key AS portal,
              upper(l.postcode_district) AS district
         FROM listings l
        WHERE l.first_seen_at >  now() - make_interval(mins => $1::int)
          AND l.first_seen_at <= now() - make_interval(mins => $2::int)
     ),
     saw AS (
       SELECT f.portal,
              f.external_id,
              bool_or(f.district IN (SELECT code FROM covered)) AS in_covered,
              min(g.first_at) FILTER (WHERE g.reader =  'tg_feed') AS feed_at,
              min(g.first_at) FILTER (WHERE g.reader <> 'tg_feed') AS scraper_at
         FROM fam f
         LEFT JOIN listing_sightings g ON g.listing_id = f.id
        GROUP BY f.portal, f.external_id
     )
     SELECT portal,
            count(*)::int AS total,
            count(*) FILTER (
              WHERE feed_at IS NOT NULL AND scraper_at IS NOT NULL)::int AS both,
            count(*) FILTER (
              WHERE feed_at IS NOT NULL AND scraper_at IS NULL)::int AS feed_only,
            count(*) FILTER (
              WHERE feed_at IS NULL AND scraper_at IS NOT NULL)::int AS scraper_only,
            count(*) FILTER (
              WHERE feed_at IS NOT NULL AND scraper_at IS NULL
                AND in_covered)::int AS feed_only_covered,
            count(*) FILTER (
              WHERE scraper_at IS NOT NULL AND feed_at IS NOT NULL
                AND scraper_at < feed_at)::int AS scraper_first,
            count(*) FILTER (
              WHERE scraper_at IS NOT NULL AND feed_at IS NOT NULL
                AND feed_at < scraper_at)::int AS feed_first,
            -- Positive means the scraper was later. Only over the ones both
            -- saw, because a lead time needs two timestamps.
            round(
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY extract(epoch FROM scraper_at - feed_at)
              )
            )::int AS median_lead_secs
       FROM saw
      WHERE feed_at IS NOT NULL OR scraper_at IS NOT NULL
      GROUP BY portal
      ORDER BY portal`,
    [Math.round(win.mins), Math.round(win.endMins)],
  ).catch(() => []);
}

export type FeedReliance = {
  alerts: number;
  covered: number;
  feed_only: number;
  unrecorded: number;
  later: number;
  median_lag_secs: number | null;
};

// The same question as `readerOverlap`, asked about alerts instead of
// listings: of what subscribers were actually sent, how much would still have
// been sent with the Telegram feed switched off.
//
// Why both. `readerOverlap` counts listings, and a listing nobody was sent is
// a miss that cost nothing — most of a district's stock matches nobody's
// filter. An alert is the product. Scoping to `notifications` also makes the
// "was it a district we read" test unnecessary: an alert exists because it
// matched somebody's filter, so its district is subscribed by definition.
//
// What this cannot see: a scraper sighting means the listing was stored, not
// that it would have been announced. A district whose watch had just started
// stores without announcing — that is the whole of `source_sweeps` — and
// whether that was the state at the time is not reconstructable afterwards.
// So `covered` is an upper bound on what survives losing the feed, and
// `feed_only` is an exact count of what does not.
export async function feedReliance(win: Win): Promise<FeedReliance> {
  const rows = await query<FeedReliance>(
    `WITH alerted AS (
       SELECT n.id, n.listing_id
         FROM notifications n
        WHERE n.kind = 'new_listing' AND n.status = 'sent'
          AND n.sent_at >  now() - make_interval(mins => $1::int)
          AND n.sent_at <= now() - make_interval(mins => $2::int)
     ),
     whosaw AS (
       SELECT a.id,
              min(g.first_at) FILTER (WHERE g.reader =  'tg_feed') AS feed_at,
              min(g.first_at) FILTER (WHERE g.reader <> 'tg_feed') AS scraper_at
         FROM alerted a
         LEFT JOIN listing_sightings g ON g.listing_id = a.listing_id
        GROUP BY a.id
     )
     SELECT count(*)::int AS alerts,
            count(*) FILTER (WHERE scraper_at IS NOT NULL)::int AS covered,
            count(*) FILTER (
              WHERE scraper_at IS NULL AND feed_at IS NOT NULL)::int AS feed_only,
            -- Neither reader recorded a sighting: an alert for a listing
            -- stored before 0052 began recording. Not a miss, and not
            -- evidence either — said out loud so nobody reads it as cover.
            count(*) FILTER (
              WHERE scraper_at IS NULL AND feed_at IS NULL)::int AS unrecorded,
            count(*) FILTER (
              WHERE scraper_at IS NOT NULL AND feed_at IS NOT NULL
                AND scraper_at > feed_at)::int AS later,
            round(
              percentile_cont(0.5) WITHIN GROUP (
                ORDER BY extract(epoch FROM scraper_at - feed_at)
              )
            )::int AS median_lag_secs
       FROM whosaw`,
    [Math.round(win.mins), Math.round(win.endMins)],
  ).catch(() => []);

  return rows[0] ?? {
    alerts: 0, covered: 0, feed_only: 0, unrecorded: 0, later: 0,
    median_lag_secs: null,
  };
}

export type FeedOnly = {
  listing_id: number;
  source_key: string;
  district: string | null;
  url: string;
  price_pcm: number | null;
  first_seen_at: string;
  alerts: number;
};

// The misses themselves, newest first. A count says the scrapers are not ready
// yet; this says why — and the usual answer is one of a handful of causes
// (a district read after the listing appeared, a page cap, a refusal) that are
// only visible when you can open the listing that got away.
export async function feedOnlyListings(win: Win, limit = 12): Promise<FeedOnly[]> {
  return query<FeedOnly>(
    `WITH covered AS (
       SELECT DISTINCT upper(area) AS code
         FROM subscriptions s
         CROSS JOIN LATERAL jsonb_array_elements_text(
             coalesce(s.criteria->'areas'->'postcode_districts', '[]'::jsonb)
         ) AS area
        WHERE s.active
     )
     SELECT l.id AS listing_id, l.source_key,
            upper(l.postcode_district) AS district,
            l.url, l.price_pcm, l.first_seen_at::text,
            (SELECT count(*)::int FROM notifications n
              WHERE n.listing_id = l.id AND n.status = 'sent') AS alerts
       FROM listings l
      WHERE l.first_seen_at >  now() - make_interval(mins => $1::int)
        AND l.first_seen_at <= now() - make_interval(mins => $2::int)
        AND upper(l.postcode_district) IN (SELECT code FROM covered)
        AND EXISTS (SELECT 1 FROM listing_sightings g
                     WHERE g.listing_id = l.id AND g.reader = 'tg_feed')
        AND NOT EXISTS (SELECT 1 FROM listing_sightings g
                         WHERE g.listing_id = l.id AND g.reader <> 'tg_feed')
      ORDER BY l.first_seen_at DESC
      LIMIT $3`,
    [Math.round(win.mins), Math.round(win.endMins), limit],
  ).catch(() => []);
}

// When the comparison above started having anything to say. Nothing was
// backfilled, so a window reaching before this is reporting on a period when
// only some of the readers were recording — worth saying on the page rather
// than letting somebody read a misleading zero.
export async function sightingsSince(): Promise<string | null> {
  const rows = await query<{ first_at: string | null }>(
    `SELECT min(first_at)::text AS first_at FROM listing_sightings`,
  ).catch(() => []);
  return rows[0]?.first_at ?? null;
}

export type PortalRun = {
  source: string;
  runs: number;
  bad: number;
  bytes: number;
  proxy_bytes: number;
  requests: number;
  stored: number;
  announced: number;
  seen: number;
  caught_up: number;
  invalid: number;
  refused: number;
  partial: number;
  last_at: string | null;
  last_status: string | null;
};

// One row per portal reader, from the counters each scrape stage already
// writes. There is no table for this on purpose: `job_stages` is per stage and
// timestamped, so it answers "over this period" without anything to keep in
// step.
//
// `bytes` is what crossed the wire, from libcurl's own transfer counter, and
// `proxy_bytes` is the part of it that went through the residential proxy —
// which is the number a DataImpulse invoice should be checked against. Every
// cast is guarded by `jsonb_typeof` so that a counter which is somehow not a
// number cannot break the page.
export async function portalRuns(win: Win): Promise<PortalRun[]> {
  return query<PortalRun>(
    `WITH windowed AS (
       SELECT source_key, status, counters, started_at
         FROM job_stages
        WHERE stage = 'scrape'
          AND source_key IS NOT NULL
          AND started_at >  now() - make_interval(mins => $1::int)
          AND started_at <= now() - make_interval(mins => $2::int)
     )
     SELECT source_key AS source,
            count(*)::int AS runs,
            count(*) FILTER (WHERE status IN ('failed', 'degraded'))::int AS bad,
            coalesce(sum((counters->>'bytes')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'bytes') = 'number'), 0)
              AS bytes,
            coalesce(sum((counters->>'proxy_bytes')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'proxy_bytes') = 'number'), 0)
              AS proxy_bytes,
            coalesce(sum((counters->>'requests')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'requests') = 'number'), 0)::int
              AS requests,
            coalesce(sum((counters->>'stored')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'stored') = 'number'), 0)::int
              AS stored,
            coalesce(sum((counters->>'announced')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'announced') = 'number'), 0)::int
              AS announced,
            coalesce(sum((counters->>'seen')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'seen') = 'number'), 0)::int
              AS seen,
            -- Listings already in the database from another reader — the feed,
            -- nearly always — that this one has now caught up with. Zero here
            -- while the feed is still posting means the scraper is not
            -- re-examining what the feed got to first, which is the one way
            -- muting the feed can go wrong silently. See store.sighted_by.
            coalesce(sum((counters->>'caught_up')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'caught_up') = 'number'), 0)::int
              AS caught_up,
            coalesce(sum((counters->>'invalid')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'invalid') = 'number'), 0)::int
              AS invalid,
            coalesce(sum((counters->>'refused')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'refused') = 'number'), 0)::int
              AS refused,
            coalesce(sum((counters->>'district_partial')::bigint)
                     FILTER (WHERE jsonb_typeof(counters->'district_partial') = 'number'), 0)::int
              AS partial,
            max(started_at)::text AS last_at,
            (array_agg(status ORDER BY started_at DESC))[1] AS last_status
       FROM windowed
      GROUP BY source_key
      ORDER BY source_key`,
    [Math.round(win.mins), Math.round(win.endMins)],
  ).catch(() => []);
}

