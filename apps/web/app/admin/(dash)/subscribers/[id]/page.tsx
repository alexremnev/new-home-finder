import { dollars, pounds, rate, waFormula } from "@/lib/money";
import Link from "next/link";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import {
  person, sellablePlans, wantedBy,
} from "@/lib/admin-queries";
import { describeCriteria, type Criteria } from "@/lib/criteria";

import { Metric, Why } from "../../charts";
import { Panel, PanelWait } from "../../panel";
import {
  AccountHistory, AccountPayments, AccountSent, AlertBuckets,
} from "./bodies";
import { DEFAULT_SPAN, spanFrom } from "../../span";

import { AsyncForm } from "../../async-form";

import { ago, at, dayOf } from "@/lib/when";

export const dynamic = "force-dynamic";

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
  const [who, wants, plans] = await Promise.all([
    person(userId), wantedBy(userId), sellablePlans(),
  ]);
  if (!who) notFound();

  const live = who.status === "active";
  // Read from `user_entitlement`, not worked out from the date. A WhatsApp
  // month ends at thirty days or at nine hundred messages, whichever comes
  // first, so this page used to show `active` and a date three weeks away for
  // somebody who had been receiving nothing for a fortnight — which is the
  // one case where the page has to explain itself.
  const ended = who.plan_live === false;
  const spent =
    who.message_allowance !== null && who.messages_used >= who.message_allowance;
  // The same sum the month-wide tile shows, over this person's current period.
  // Written out so the dollar figure beside it can be checked rather than
  // taken — see `waFormula`.
  const sum = waFormula(
    who.wa_service, who.wa_marketing, who.wa_service_micros,
    who.wa_marketing_micros,
  );

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
            {ended && (
              <span className="badge warning">
                {spent ? " out of messages" : " expired"}
              </span>
            )}
          </h1>
          <p className="hint">
            Joined {at(who.joined)} · consent {who.consent_source ?? "—"}{" "}
            {at(who.consent_at)}
          </p>
        </div>
      </header>

      {done && <p className="note">{done}.</p>}

      <Panel
        wide
        title="Alerts delivered, half-hour buckets"
        why="Каждая точка — 30 минут. Видно не только сколько человек получил, но и когда: ровная линия у нуля с редкими всплесками — это норма для узкого фильтра, а пустота весь день при активном плане — повод посмотреть канал доставки."
      >
        <Suspense fallback={<PanelWait />}>
          <AlertBuckets userId={userId} span={win.key} />
        </Suspense>
      </Panel>

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
          {/* The other half of "is this plan live", and the half the date
              cannot show. Only for a metered plan, which is WhatsApp's: there
              is nothing to meter where delivery is free, and a tile reading
              "— / —" is a tile that has to be explained every time. */}
          {who.message_allowance !== null && (
            <Metric
              label="WhatsApp messages"
              value={`${comma(who.messages_used)} / ${comma(who.message_allowance)}`}
              tone={spent ? "bad" : undefined}
              note={
                // Dollars: it is what Meta bills in, and the money this page
                // shows above it is in pounds because that is what arrived.
                // Mixing them is honest; converting one would not be.
                who.wa_cost_micros > 0
                  ? `${comma(who.wa_service)} service · ${comma(who.wa_marketing)} marketing · ${dollars(who.wa_cost_micros)}`
                  : `since ${who.plan_from ? dayOf(who.plan_from) : "the start"}`
              }
              why={
                "Сколько сообщений ушло этому человеку в WhatsApp за текущий оплаченный период и сколько включено в тариф. Считаются все: объявления, ответы бота, вечерняя сводка, чек-ин и уведомления о тарифе — Meta берёт деньги за каждое. " +
                `Период считается от ${who.plan_from ? dayOf(who.plan_from) : "начала"}: при смене или продлении тарифа эта дата сдвигается, поэтому счётчик обнуляется. ` +
                `Стоимость за период: ${sum}. ` +
                `Два тарифа: service ${rate(who.wa_service_micros)} — свободный текст внутри 24-часового окна и utility-шаблон объявления; marketing ${rate(who.wa_marketing_micros)} — уведомление о законченном платном тарифе с кнопкой оплаты. ` +
                (who.wa_uncosted > 0
                  ? `На ${who.wa_uncosted} сообщениях цены не оказалось — они отправлены до того, как тариф задали, и в сумму не входят, поэтому формула и число могут расходиться. `
                  : "") +
                "Когда счётчик доходит до лимита, период заканчивается так же, как если бы кончились дни: человек получает об этом сообщение, а в опс-чат приходит алерт."
              }
            />
          )}
        </div>
      </section>

      <section>
        <h2>Account</h2>
        <div className="scroll-x">
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
        </div>
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

      <Panel
        wide
        title="Payments"
        why="Каждый платёж по этому аккаунту: тариф, сумма, сколько дней он дал и чем оплачено. Reference — идентификатор на стороне провайдера, по нему платёж находится в дашборде Stripe."
      >
        <Suspense fallback={<PanelWait />}>
          <AccountPayments userId={userId} />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="What was sent"
        why="Последние уведомления по этому аккаунту, свежие сверху. sent — ушло, failed — не удалось, skipped — задержано долей плана."
      >
        <Suspense fallback={<PanelWait />}>
          <AccountSent userId={userId} />
        </Suspense>
      </Panel>

      <Panel
        wide
        title="What we did to this account"
        why="Каждое действие с этой страницы записывается здесь — смена плана, выданные дни, стирание данных."
      >
        <Suspense fallback={<PanelWait />}>
          <AccountHistory userId={userId} />
        </Suspense>
      </Panel>
    </>
  );
}
