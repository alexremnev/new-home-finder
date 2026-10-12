import { proxySpend } from "@/lib/admin-queries";
import { ago } from "@/lib/when";

import { windowFor } from "../cached";
import { Why } from "../charts";
import { spanWords } from "../span";
import { mb, PROXY_JOBS, TASKS, tone, word } from "./spend";

// What each scraper job spent on the residential proxy, one widget each.
//
// ── why this is a tab of its own ─────────────────────────────────────────
//
// The System tab already shows proxy traffic, as one sentence under Portal
// scrapers: a single total, every reader at once. That answers "is the bill
// about right" and nothing else. The question that actually comes up is
// narrower and per-reader — *who* is spending, and is it the one that is
// supposed to — because the proxy exists for exactly one reader. Zoopla
// refuses this server's address outright, so `zoopla_london` cannot read the
// city without one; Rightmove never refuses, OpenRent and SpareRoom are pinned
// to a direct route, and the district Zoopla reader has no timer. A figure on
// any of those four is news, and in the combined total it is invisible.
//
// The judgment each tile makes lives in ./spend.ts.

export async function ProxyTasks({ span }: { span: string }) {
  const win = windowFor(span);
  const rows = await proxySpend(win, PROXY_JOBS);

  const paid = rows.reduce((sum, row) => sum + Number(row.proxy_bytes ?? 0), 0);
  const calls = rows.reduce((sum, row) => sum + row.proxy_requests, 0);
  const second = rows.reduce((sum, row) => sum + Number(row.fallback_bytes ?? 0), 0);

  return (
    <>
      <div className="tiles tiles-roomy">
        {TASKS.map((task) => {
          const row = rows.find((one) => one.task === task.job);
          const mood = tone(row, task.expect);
          return (
            <div key={task.job} className={`card job job-${mood}`}>
              <div className="job-name">
                {task.job}
                <span className={`pill pill-${mood}`}>{word(task.expect, mood)}</span>
                <Why text={task.why} />
              </div>
              <div className="metric-note">
                {row && row.runs > 0
                  ? `${row.runs} run${row.runs === 1 ? "" : "s"} · last ${ago(row.last_at)}`
                  : `nothing ran ${spanWords(win)}`}
              </div>

              {/* The two figures this page was asked for first, then the
                  totals they are to be read against: a reader can be cheap in
                  bytes and expensive in requests, and a proxy is sold by the
                  byte while a portal counts the requests. */}
              <div className="job-numbers">
                <span>
                  <b className={row && row.proxy_requests > 0 ? "warn" : undefined}>
                    {row?.proxy_requests ?? 0}
                  </b>
                  <span>proxied</span>
                </span>
                <span>
                  <b className={row && Number(row.proxy_bytes ?? 0) > 0 ? "warn" : undefined}>
                    {mb(row?.proxy_bytes)}
                  </b>
                  <span>MB paid</span>
                </span>
                <span>
                  <b>{row?.requests ?? 0}</b>
                  <span>requests</span>
                </span>
                <span>
                  <b>{mb(row?.bytes)}</b>
                  <span>MB total</span>
                </span>
                {/* Why there is spend at all — and on a reader that should be
                    free, why there is about to be. A refusal is the portal
                    saying it does not want this address. */}
                {row && row.refused > 0 && (
                  <span>
                    <b>{row.refused}</b>
                    <span>refused</span>
                  </span>
                )}
                {/* Refused and not retried through the tunnel, because the host
                    is pinned to a direct route. Not a fault: it is the one
                    case where a zero above is a decision rather than luck. */}
                {row && row.pinned > 0 && (
                  <span>
                    <b>{row.pinned}</b>
                    <span>pinned</span>
                  </span>
                )}
                {row && row.rotations > 0 && (
                  <span>
                    <b>{row.rotations}</b>
                    <span>rotations</span>
                  </span>
                )}
              </div>

              {row && row.fallback_requests > 0 && (
                <div className="job-error">
                  {row.fallback_requests} request
                  {row.fallback_requests === 1 ? "" : "s"} and{" "}
                  {mb(row.fallback_bytes)} MB went through the second provider,
                  which is only reached once the primary pool has spent its
                  whole budget and still not got the page. A second invoice, and
                  a pool worth asking the vendor about.
                </div>
              )}
            </div>
          );
        })}
      </div>

      <p className="hint">
        {calls === 0 && paid === 0
          ? `Nothing went through the proxy ${spanWords(win)}.`
          : `${calls} request${calls === 1 ? "" : "s"} and ${mb(paid)} MB through the proxy ${spanWords(win)} — that is what DataImpulse bills.`}
        {second > 0 &&
          ` A further ${mb(second)} MB went through the second provider and is billed separately.`}
      </p>
    </>
  );
}
