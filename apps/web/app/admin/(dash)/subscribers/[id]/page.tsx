import Link from "next/link";
import { notFound } from "next/navigation";

import {
  alertBuckets, deliveredTo, historyOf, paymentsBy, person, sellablePlans, wantedBy,
} from "@/lib/admin-queries";
import { describeCriteria, type Criteria } from "@/lib/criteria";

import { Metric, Series, Why } from "../../charts";
import { DEFAULT_SPAN, spanFrom } from "../../span";

import { AsyncForm } from "../../async-form";

import { ago, at, dayOf } from "@/lib/when";

export const dynamic = "force-dynamic";

const pounds = (pence: number) =>
  "£" + (pence / 100).toLocaleString("en-GB", { maximumFractionDigits: 2 });
const comma = (n: number) => n.toLocaleString("en-GB");

export default async function UserPage({
  params, searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ done?: string; error?: string; w?: string }>;
}) {

  const { id } = await params;
  const userId = Number(id);
  if (!Number.isInteger(userId) || userId <= 0) notFound();

  const { done, w } = await searchParams;
  const win = spanFrom(w ?? DEFAULT_SPAN);
  const [who, feed, paid, history, wants, plans, buckets] = await Promise.all([
    person(userId), deliveredTo(userId), paymentsBy(userId), historyOf(userId),
    wantedBy(userId), sellablePlans(), alertBuckets(userId, win, 30),
  ]);
  if (!who) notFound();

  const live = who.status === "active";
  const expired = who.plan_until !== null && new Date(who.plan_until) < new Date();

  return (
    <>
      <header className="dash-head">
        <div>
          <p className="crumb">
            <Link href="/admin/subscribers">Subscribers</Link> / {who.user_id}
          </p>
          <h1>
            {who.plan_display ?? who.plan}
            <span className={`badge ${live ? "good" : "warning"}`}> {who.status}</span>
            {expired && <span className="badge warning"> expired</span>}
          </h1>
          <p className="hint">
            Joined {at(who.joined)} · consent {who.consent_source ?? "—"}{" "}
            {at(who.consent_at)}
          </p>
        </div>
      </header>

      {done && <p className="note">{done}.</p>}

      <div className="dash-head">
      </div>

      <div className="card" style={{ marginBottom: "0.75rem" }}>
        <h2>
          Alerts delivered, half-hour buckets
          <Why text="Каждая точка — 30 минут. Видно не только сколько человек получил, но и когда: ровная линия у нуля с редкими всплесками — это норма для узкого фильтра, а пустота весь день при активном плане — повод посмотреть канал доставки." />
        </h2>
        <Series data={buckets} />
      </div>

      <section>
        <div className="stats">
          <Metric label="Alerts delivered" value={comma(who.sent)}
                note={`last ${ago(who.last_sent_at)}`} />
          <Metric label="Held back by the plan" value={comma(who.withheld)}
                note="free tier's share" />
          <Metric label="Paid in total" value={pounds(who.paid_total_pence)}
                note={`${who.paid_count} payment${who.paid_count === 1 ? "" : "s"}, last ${ago(who.last_paid_at)}`} />
          <Metric label="Plan runs to"
                value={who.plan_until ? dayOf(who.plan_until) : "no end"}
                note={who.comped_days > 0 ? `${who.comped_days} days comped` : undefined} />
        </div>
      </section>

      <section>
        <h2>Account</h2>
        <table className="grid kv">
          <tbody>
            <tr><th>User id</th><td>{who.user_id}</td></tr>
            <tr>
              <th>Channel</th>
              <td>
                {who.channel ?? "none"}
                {who.address ? ` · ${who.address}` : ""}
                {who.verified_at ? ` · verified ${at(who.verified_at)}` : " · unverified"}
              </td>
            </tr>
            <tr><th>Payment ref</th><td>{who.payment_ref ?? "—"}</td></tr>
            <tr><th>Stopped at</th><td>{at(who.stopped_at)}</td></tr>
            <tr>
              <th>Queue</th>
              <td>
                {comma(who.queued)} waiting · {comma(who.failed)} failed
              </td>
            </tr>
            <tr>
              <th>Wants elsewhere</th>
              <td>{wants.length ? wants.join(", ") : "—"}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section>
        <h2>Filter</h2>
        {who.criteria ? (
          <>
            <pre className="criteria-block">
              {describeCriteria(who.criteria as Criteria)}
            </pre>
            <p className="hint">
              Subscription {who.subscription_id} · only listings after{" "}
              {at(who.backfill_from)} ·{" "}
              {who.seeded_at ? `starter batch sent ${at(who.seeded_at)}` : "starter batch owed"}
            </p>
            <details>
              <summary className="disclosure-inline">The stored criteria</summary>
              <pre className="criteria-block raw">
                {JSON.stringify(who.criteria, null, 2)}
              </pre>
            </details>
          </>
        ) : (
          <p className="hint">No active filter.</p>
        )}
      </section>

      <section>
        <h2>Actions</h2>
        <div className="actions">
          <AsyncForm action="/api/admin/user" className="inline-form">
            <input type="hidden" name="action" value="extend_plan" />
            <input type="hidden" name="user_id" value={who.user_id} />
            <input type="number" name="days" min={1} max={365} defaultValue={14}
                   aria-label="Days" />
            <input type="text" name="reason" placeholder="why" aria-label="Reason"
                   maxLength={200} />
            <button type="submit" className="ghost">Extend, free</button>
          </AsyncForm>

          <AsyncForm action="/api/admin/user" className="inline-form">
            <input type="hidden" name="action" value="set_plan" />
            <input type="hidden" name="user_id" value={who.user_id} />
            <select name="plan" aria-label="Plan" defaultValue={who.plan}>
              {plans.map((plan) => (
                <option key={plan.key} value={plan.key}>
                  {plan.display_name}
                </option>
              ))}
            </select>
            <input type="text" name="reason" placeholder="why" aria-label="Reason"
                   maxLength={200} />
            <button type="submit" className="ghost">Move to plan</button>
          </AsyncForm>

          <AsyncForm action="/api/admin/user" className="inline-form">
            <input type="hidden" name="action" value={live ? "pause" : "resume"} />
            <input type="hidden" name="user_id" value={who.user_id} />
            <button type="submit" className="ghost">
              {live ? "Pause delivery" : "Resume delivery"}
            </button>
          </AsyncForm>

          <AsyncForm action="/api/admin/user" className="inline-form">
            <input type="hidden" name="action"
                   value={who.status === "blocked" ? "unblock" : "block"} />
            <input type="hidden" name="user_id" value={who.user_id} />
            <input type="text" name="reason" placeholder="why" aria-label="Reason"
                   maxLength={200} />
            <button type="submit" className="ghost">
              {who.status === "blocked" ? "Unblock" : "Block"}
            </button>
          </AsyncForm>
        </div>

        <div className="danger">
          <h2>Erase</h2>
          <p className="hint">
            Removes the chat id, the tokens and the filter — everything that names
            this person.{" "}
            {who.paid_count > 0
              ? `The ${who.paid_count} payment${who.paid_count === 1 ? "" : "s"} stay, attached to an account that no longer names anybody: a financial record has to survive a request to be forgotten, and the person does not have to.`
              : "There are no payments, so the row goes entirely."}
          </p>
          <AsyncForm
            action="/api/admin/user"
            className="inline-form"
            confirm="Erase this account? Payments are kept, everything that names the person is not. This cannot be undone."
          >
            <input type="hidden" name="action" value="erase" />
            <input type="hidden" name="user_id" value={who.user_id} />
            <input type="text" name="reason" placeholder="why — recorded"
                   aria-label="Reason" maxLength={200} required />
            <button type="submit" className="ghost danger-button">
              {who.paid_count > 0 ? "Erase personal data" : "Delete this account"}
            </button>
          </AsyncForm>
        </div>
      </section>

      <section>
        <h2>
          Payments <span className="section-note">{who.paid_count}</span>
        </h2>
        {paid.length === 0 ? (
          <p className="hint">Never paid.</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>When</th><th>Plan</th><th className="num">Amount</th>
                <th className="num">Days</th><th>How</th><th>Reference</th>
              </tr>
            </thead>
            <tbody>
              {paid.map((one) => (
                <tr key={one.id}>
                  <td className="muted">{at(one.created_at)}</td>
                  <td>{one.plan}</td>
                  <td className="num">{pounds(one.amount_pence)}</td>
                  <td className="num muted">{one.granted_days ?? "—"}</td>
                  <td>{one.provider}</td>
                  <td className="muted wrap">{one.provider_ref ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>
          What was sent{" "}
          <span className="section-note">
            newest {feed.length} of {comma(who.sent + who.queued + who.failed + who.withheld)}
          </span>
        </h2>
        {feed.length === 0 ? (
          <p className="hint">Nothing yet.</p>
        ) : (
          <table className="grid">
            <thead>
              <tr>
                <th>Queued</th><th>Sent</th><th>State</th>
                <th className="num">Rent</th><th className="num">Beds</th>
                <th>Where</th><th>Listing</th>
              </tr>
            </thead>
            <tbody>
              {feed.map((one) => (
                <tr key={one.id}>
                  <td className="muted">{at(one.created_at)}</td>
                  <td className="muted">{at(one.sent_at)}</td>
                  <td>
                    <span
                      className={`badge ${
                        one.status === "sent"
                          ? "good"
                          : one.status === "failed"
                            ? "critical"
                            : "warning"
                      }`}
                    >
                      {one.status}
                      {one.error ? ` · ${one.error}` : ""}
                    </span>
                  </td>
                  <td className="num">
                    {one.price_pcm === null ? "—" : "£" + comma(one.price_pcm)}
                  </td>
                  <td className="num muted">{one.bedrooms ?? "—"}</td>
                  <td className="muted">{one.district ?? "—"}</td>
                  <td className="wrap">
                    {one.url ? (
                      <a href={one.url} target="_blank" rel="noreferrer">
                        open
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <h2>What we did to this account</h2>
        {history.length === 0 ? (
          <p className="hint">Nothing. Every action from this page is recorded here.</p>
        ) : (
          <table className="grid">
            <thead>
              <tr><th>When</th><th>What</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {history.map((one) => (
                <tr key={one.id}>
                  <td className="muted">{at(one.created_at)}</td>
                  <td><strong>{one.action}</strong></td>
                  <td className="wrap muted counters">{JSON.stringify(one.detail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
