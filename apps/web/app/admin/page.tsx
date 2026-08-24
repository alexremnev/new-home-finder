// The console.
//
// A server component, and that is a security decision rather than a performance
// one: every subscriber's filter is on this page, and in a client component the
// data would be serialised into the page's JavaScript payload. Here it is rendered
// to HTML and the numbers never exist in the browser as data.
//
// ── what it answers, in the order somebody asks ──────────────────────────────
//
//   1. Is anything broken?  — first, because if it is, nothing else matters.
//   2. How big is this?     — the tiles.
//   3. What is happening?   — the charts, over a range you choose.
//   4. Who are they?        — the table, with the one action there is.
//
// Problems go at the top even though they are usually empty. A dashboard that puts
// its warnings under three screens of charts is a dashboard where warnings are
// found late.

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/admin-session";
import {
  byDistrict, byPrice, delivery, eventCounts, events, jobHealth, knownJobs, overview,
  planMix, problems, recentComps, recentPayments, recentRuns, sentPerDay, subscribers,
  unparseableShare, visitsPerDay,
  type Filters, type Range,
} from "@/lib/admin-queries";
import { describeCriteria, type Criteria } from "@/lib/criteria";

import { Bars, Columns, PlanMix, Stat, TimeSeries } from "./charts";
import { FilterBar } from "./filters";
import { Live } from "./live";

export const dynamic = "force-dynamic";

const pounds = (pence: number) =>
  "£" + (pence / 100).toLocaleString("en-GB", { maximumFractionDigits: 0 });
const comma = (n: number) => n.toLocaleString("en-GB");

/** Log levels to the status palette. Reserved colours, so a level never wears a
 *  series hue and a series never wears a level's. */
const LEVELS: Record<string, string> = {
  error: "critical",
  warn: "warning",
  info: "good",
  debug: "warning",
};

/** How a problem reads: an icon and a word, never colour alone. */
const SEVERITY: Record<string, { icon: string; word: string; className: string }> = {
  "run failed": { icon: "✕", word: "Failed", className: "critical" },
  "error logged": { icon: "!", word: "Error", className: "serious" },
  "nothing read": { icon: "!", word: "Silent", className: "critical" },
  "delivery stalled": { icon: "!", word: "Stalled", className: "serious" },
  unparseable: { icon: "~", word: "Skipped", className: "warning" },
};

export default async function AdminPage({
  searchParams,
}: {
  searchParams: Promise<{
    range?: string;
    done?: string;
    job?: string;
    level?: string;
    q?: string;
  }>;
}) {
  // Checked here as well as in middleware, and the duplication is the point. This
  // page renders every subscriber's filter; if the matcher is ever edited wrongly
  // the middleware silently stops running, and nothing about the page would look
  // different. Two independent checks means one mistake is not an open door.
  const jar = await cookies();
  if (!(await sessionIsValid(jar.get(SESSION_COOKIE)?.value))) redirect("/admin/login");

  const params = await searchParams;
  // Every filter comes from the URL and is validated here rather than trusted. A
  // range of "999" or a level of "'; DROP" is not an error to report — it is a
  // parameter somebody typed, and the honest answer is the default view.
  const days = ([1, 7, 30, 90] as const).includes(Number(params.range) as Range)
    ? (Number(params.range) as Range)
    : (7 as Range);
  const level = ["debug", "info", "warn", "error"].includes(params.level ?? "")
    ? params.level
    : undefined;
  const filters: Filters = {
    range: days,
    job: params.job?.slice(0, 40) || undefined,
    level,
    q: params.q?.slice(0, 80) || undefined,
  };

  // One round of queries, in parallel. They are independent and the page cannot
  // render until all of them are in, so waiting for them one at a time would be
  // the sum of their latencies for no benefit.
  const [
    stats, plans, sent, visits, districts, prices, people, faults, payments, comps,
    runs, health, log, levels, jobs, feedHealth, sending,
  ] = await Promise.all([
    overview(), planMix(), sentPerDay(days), visitsPerDay(days),
    byDistrict(days), byPrice(days), subscribers(), problems(),
    recentPayments(), recentComps(), recentRuns(), jobHealth(),
    events(filters), eventCounts(filters), knownJobs(), unparseableShare(days),
    delivery(),
  ]);

  return (
    <div className="admin">
      <header className="admin-head">
        <div>
          <h1>Console</h1>
          <p className="hint">
            {comma(stats.subscribers)} subscribers · {comma(stats.sent_total)} alerts
            delivered all time
          </p>
        </div>
        <div className="admin-head-right">
          <Live />
          <form method="post" action="/api/admin/logout">
            <button type="submit" className="ghost">Sign out</button>
          </form>
        </div>
      </header>

      {/* One bar, above everything, because these filters apply to everything. A
          filter beside one chart reads as belonging to that chart. */}
      <FilterBar chosen={filters} jobs={jobs} levels={levels} />

      {params.done === "extended" && (
        <p className="note">Plan extended. The new date is in the table below.</p>
      )}

      {/* ── 1. anything broken ─────────────────────────────────────────── */}
      <section>
        <h2>Problems</h2>
        {faults.length === 0 ? (
          <p className="ok-line">
            <span className="badge good">✓ Clear</span> Nothing failing, nothing
            silent, nothing stuck in the queue.
          </p>
        ) : (
          <table className="grid">
            <thead>
              <tr><th>What</th><th>Detail</th><th className="num">Count</th><th>Last seen</th></tr>
            </thead>
            <tbody>
              {faults.map((fault, i) => {
                const how = SEVERITY[fault.kind] ?? {
                  icon: "!", word: fault.kind, className: "warning",
                };
                return (
                  <tr key={`${fault.kind}-${i}`}>
                    <td>
                      <span className={`badge ${how.className}`}>
                        {how.icon} {how.word}
                      </span>
                    </td>
                    <td className="wrap">{fault.detail}</td>
                    <td className="num">{comma(fault.count)}</td>
                    <td className="muted">{fault.last_at?.slice(0, 16) ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {/* ── 2. did the jobs run ────────────────────────────────────────── */}
      <section>
        <h2>Jobs</h2>
        {/* Both jobs in one place, because the question is almost always "did both
            of them run" and two panels means comparing two clocks. */}
        <div className="stats">
          {health.map((job) => (
            <Stat
              key={job.job}
              label={job.job}
              value={job.last_status === "ok" ? "OK" : (job.last_status ?? "never")}
              note={
                job.last_at
                  ? `${job.last_at.slice(11, 16)} · ${job.ok_24h} ok, ${job.failed_24h} bad in 24h`
                  : "has never run"
              }
            />
          ))}
        </div>

        <table className="grid" style={{ marginTop: "1.25rem" }}>
          <thead>
            <tr>
              <th>Started</th><th>Job</th><th>How</th><th>Result</th>
              <th className="num">Secs</th><th>What it did</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => {
              const bad = run.status === "failed" || run.status === "degraded";
              return (
                <tr key={run.id}>
                  <td className="muted">{run.started_at.slice(5, 16)}</td>
                  <td><strong>{run.job}</strong></td>
                  <td className="muted">{run.trigger}</td>
                  <td>
                    <span className={`badge ${bad ? "critical" : "good"}`}>
                      {bad ? "✕" : "✓"} {run.status}
                    </span>
                  </td>
                  <td className="num muted">{run.seconds ?? "—"}</td>
                  {/* The counters as the job emitted them. Not picked apart here:
                      which ones a job reports is the job's business, and naming them
                      would mean editing this every time a stage learns to count
                      something. */}
                  <td className="wrap muted counters">
                    {run.error
                      ? run.error.slice(0, 120)
                      : Object.entries(run.counters ?? {})
                          .map(([name, value]) => `${name}=${value}`)
                          .join("  ") || "nothing to do"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {runs.length === 0 && <p className="hint">No runs recorded yet.</p>}
      </section>

      {/* ── 3. how big ─────────────────────────────────────────────────── */}
      <section>
        <div className="stats">
          <Stat label="Subscribers" value={comma(stats.subscribers)}
                note={`${comma(stats.active_filters)} active filters`} />
          <Stat label="Alerts sent today" value={comma(stats.sent_today)}
                note={`${comma(stats.queued)} waiting in the queue`} />
          <Stat label="Visitors, 30 days" value={comma(stats.visitors_30d)}
                note="sum of daily uniques" />
          <Stat label="Taken" value={pounds(stats.revenue_pence)}
                note={`${payments.length} payments recorded`} />
        </div>
      </section>

      {/* ── 4. what is happening ───────────────────────────────────────── */}
      <section>
        <div className="stats" style={{ marginBottom: "1.5rem" }}>
          <Stat
            label="Oldest thing waiting"
            value={
              sending.oldest_queued_mins === null
                ? "nothing"
                : `${comma(sending.oldest_queued_mins)} min`
            }
            note="a queue that stops moving looks like a small one"
          />
          <Stat
            label="Typical delay"
            value={
              sending.median_latency_secs === null
                ? "—"
                : `${comma(sending.median_latency_secs)}s`
            }
            note="queued to delivered, median, 24h"
          />
          <Stat label="Failed sends, 24h" value={comma(sending.failed_24h)} />
          <Stat label="Held back, 24h" value={comma(sending.skipped_24h)}
                note="free tier's share" />
        </div>

        <TimeSeries data={sent} title={`Alerts delivered · last ${days} day${days === 1 ? "" : "s"}`} />
        <TimeSeries data={feedHealth} unit="%"
                    title="Messages the parser could not read, % of the day's messages" />
        <TimeSeries data={visits} title={`Unique visitors · last ${days} day${days === 1 ? "" : "s"}`} />
        <Bars data={districts} title="Alerts by district" />
        <Columns data={prices} title="Alerts by rent, in £250 bands"
                 format={(n) => "£" + comma(n)} />
        <PlanMix data={plans} title="Who is on which plan" />
      </section>

      {/* ── 5. who they are ────────────────────────────────────────────── */}
      <section>
        <h2>Subscribers</h2>
        <table className="grid">
          <thead>
            <tr>
              <th>#</th><th>Plan</th><th>Until</th><th>Filter</th>
              <th className="num">Sent</th><th className="num">Held</th><th>Extend</th>
            </tr>
          </thead>
          <tbody>
            {people.map((person) => (
              <tr key={person.user_id}>
                <td className="muted">{person.user_id}</td>
                <td>
                  {person.plan_display ?? person.plan}
                  {person.status !== "active" && (
                    <span className="badge warning"> {person.status}</span>
                  )}
                </td>
                <td className="muted">{person.plan_until?.slice(0, 10) ?? "—"}</td>
                <td className="wrap criteria">
                  {person.criteria
                    ? describeCriteria(person.criteria as Criteria)
                    : "no active filter"}
                </td>
                <td className="num">{comma(person.sent)}</td>
                <td className="num muted">{comma(person.withheld)}</td>
                <td>
                  {/* A form per row rather than one form with a user picker: the
                      row already says who, and a picker is a chance to extend the
                      wrong account. */}
                  <form method="post" action="/api/admin/extend" className="inline-form">
                    <input type="hidden" name="user_id" value={person.user_id} />
                    <input type="number" name="days" min={1} max={365}
                           defaultValue={14} aria-label="Days" />
                    <input type="text" name="reason" placeholder="why"
                           aria-label="Reason" maxLength={200} />
                    <button type="submit" className="ghost">Extend</button>
                  </form>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {people.length === 0 && <p className="hint">Nobody yet.</p>}
      </section>

      <section>
        <h2>Payments</h2>
        {payments.length === 0 ? (
          <p className="hint">No payments recorded.</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>When</th><th>#</th><th>Plan</th><th className="num">Amount</th>
                <th>How</th><th className="num">Days</th>
              </tr>
            </thead>
            <tbody>
              {payments.map((payment) => (
                <tr key={payment.id}>
                  <td className="muted">{payment.created_at.slice(0, 16)}</td>
                  <td className="muted">{payment.user_id}</td>
                  <td>{payment.plan}</td>
                  <td className="num">{pounds(payment.amount_pence)}</td>
                  <td>{payment.provider}</td>
                  <td className="num muted">{payment.granted_days ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {comps.length > 0 && (
          <>
            <h2>Free extensions</h2>
            <table className="grid">
              <thead>
                <tr><th>When</th><th>#</th><th>Detail</th></tr>
              </thead>
              <tbody>
                {comps.map((comp) => (
                  <tr key={comp.id}>
                    <td className="muted">{comp.created_at.slice(0, 16)}</td>
                    <td className="muted">{comp.user_id ?? "—"}</td>
                    <td className="wrap muted">{JSON.stringify(comp.detail)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      {/* ── 6. the log ─────────────────────────────────────────────────── */}
      <section>
        <h2>
          Log{" "}
          <span className="section-note">
            {comma(log.length)} shown
            {filters.q ? ` · matching "${filters.q}"` : ""}
          </span>
        </h2>
        {log.length === 0 ? (
          <p className="hint">
            Nothing matches. The worker only logs warnings and errors at this level —
            silence here is the good outcome.
          </p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>When</th><th>Level</th><th>Job</th><th>Stage</th><th>Message</th>
              </tr>
            </thead>
            <tbody>
              {log.map((entry) => (
                <tr key={entry.id}>
                  <td className="muted">{entry.ts.slice(5, 19)}</td>
                  <td>
                    <span className={`badge ${LEVELS[entry.level] ?? "warning"}`}>
                      {entry.level}
                    </span>
                  </td>
                  <td className="muted">{entry.job}</td>
                  <td className="muted">{entry.stage ?? "—"}</td>
                  <td className="wrap">
                    {entry.message}
                    {/* The context, when there is any. It is where the worker puts
                        the numbers that explain the line, and dropping it would make
                        this a list of sentences instead of a log. */}
                    {entry.ctx && Object.keys(entry.ctx).length > 0 && (
                      <span className="counters">
                        {" "}
                        {Object.entries(entry.ctx)
                          .map(([name, value]) => `${name}=${String(value)}`)
                          .join(" ")}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <p className="footnote">
        Visitor counts are the sum of daily uniques from a salt that changes every
        day, so the same person on two days counts twice and no visitor can be
        followed between days. No addresses are stored.
      </p>
    </div>
  );
}
