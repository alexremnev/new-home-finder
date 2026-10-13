import Link from "next/link";

import {
  alertPoints, byDistrict, byPrice, waSpend, WA_DAILY_ALERT,
} from "@/lib/admin-queries";
import { dollars } from "@/lib/money";
import { dayOf, since, windowLeft } from "@/lib/when";

import { planMixOnce, subscriberPageOnce, windowFor } from "../cached";
import { Metric, Rank, Series } from "../charts";
import { MoreRank } from "../more-rank";
import { Paged, type MorePage } from "../paged";
import { bucketMinutes, spanWords } from "../span";
import { moreDistricts, moreEveryone } from "./more";

// One definition per card. They live apart from the page so the page can
// render each inside its own Suspense boundary and the queries run in
// parallel; the lists among them page on through a server action, appended in
// place by ../paged.tsx.

export const PER_PAGE = 20;

// Green means alerts are reaching them. Anything else is a reason, not a
// colour: a paused filter and a dead channel look the same in a list of dots.
//
// `tint` is the same judgement at row scale, and it answers a different
// question from the dot: not "is delivery working" but "is this account one we
// are serving". Live plan — trial, paid or granted — is green; a plan that has
// run out of days or out of alerts is amber, because that is the list to work
// through. Everything else (pending, stopped, blocked) is left plain: a row
// nobody is waiting on should not compete for attention with one that is.
function state(row: {
  status: string;
  sent_window: number;
  failed_window: number;
  channel: string | null;
  plan_until: string | null;
  plan_live: boolean;
  message_allowance: number | null;
  messages_used: number;
}): { dot: string; why: string; tint: "live" | "lapsed" | null } {
  // Read before the delivery checks below, so the tint says what the account
  // is entitled to even when its channel is broken — the dot is what says
  // delivery is broken, and the two are different facts.
  const tint = row.status !== "active" ? null : row.plan_live ? "live" : "lapsed";
  if (row.status !== "active") return { dot: "idle", why: row.status, tint };
  if (!row.channel) return { dot: "bad", why: "not connected", tint };
  if (row.failed_window > 0) {
    return { dot: "bad", why: `${row.failed_window} failed`, tint };
  }
  // Read, not recomputed from the date. A WhatsApp month also ends at its
  // allowance, and this row said "delivering" for somebody who had spent
  // theirs — which is the one case where the page has to explain itself,
  // because the date on the next column says the plan is fine.
  if (!row.plan_live) {
    return {
      dot: "idle",
      why:
        row.message_allowance !== null && row.messages_used >= row.message_allowance
          ? `all ${row.message_allowance} messages used`
          : "plan ended",
      tint,
    };
  }
  if (row.sent_window > 0) return { dot: "ok", why: "delivering", tint };
  return { dot: "idle", why: "nothing matched", tint };
}

// Judged on the newest page of accounts rather than on all of them: the three
// figures below are counted from `rows`, and asking the same questions of every
// account ever created is a different and much heavier query. The notes say so.
export async function SubscriberTiles({ span }: { span: string }) {
  const win = windowFor(span);
  const [{ rows, total }, plans] = await Promise.all([
    subscriberPageOnce(win, 1, PER_PAGE),
    planMixOnce(),
  ]);

  const reaching = rows.filter((row) => state(row).dot === "ok").length;
  const delivered = rows.reduce((sum, row) => sum + row.sent_window, 0);

  return (
    <div className="tiles">
      <Metric
        label="Accounts"
        value={total}
        tone={total > 0 ? "good" : "warn"}
        note="everyone not erased"
      />
      <Metric
        label="Delivering"
        value={`${reaching}/${rows.length}`}
        tone={reaching === rows.length ? "good" : reaching === 0 ? "bad" : "warn"}
        note={`of the ${rows.length} newest`}
        why="Сколько человек из последних двадцати зарегистрированных действительно получают алерты: за выбранный период ушёл хотя бы один и ни один не упал. Серая точка в списке — подписка есть, но ничего не подошло или план закончился. Красная — канал не привязан или отправка падает."
      />
      <Metric
        label="Alerts sent"
        value={delivered}
        // Nothing sent over the window is the thing worth noticing, and it is
        // not visible from a neutral card.
        tone={delivered > 0 ? "good" : "warn"}
        note={`newest ${rows.length} accounts · ${spanWords(win)}`}
      />
      <Metric
        label="Plans"
        value={plans.length}
        tone={plans.length > 0 ? "good" : "warn"}
        note="in use"
        why="Сколько разных тарифов сейчас используется. Разбивка — в карточке Plans ниже."
      />
    </div>
  );
}

export async function WhatsAppSpend() {
  const spend = await waSpend();
  const priced = spend.service_micros > 0;
  // Templates with no rate against them are the one thing here that makes the
  // total an understatement, so the tile says how many rather than letting a
  // confident figure stand for an incomplete one.
  const uncosted = spend.templates > 0 && spend.template_micros === 0;

  // Nine hundred a subscriber a period is the bound we sold; there is no
  // monthly bound on the account as a whole, so the only honest tone here is
  // neutral — a big number is not by itself a fault, and colouring it as one
  // would be inventing a threshold nobody set.
  return (
    <>
      <div className="tiles">
        <Metric
          label="Messages"
          value={spend.messages}
          note="this calendar month, London"
          why="Все сообщения, которые Meta у нас приняла с первого числа: объявления, ответы бота, вечерняя сводка, чек-ин перед закрытием окна и уведомления о тарифе. Meta берёт деньги за каждое, поэтому считаются все, а не только алерты."
        />
        <Metric
          label="What it cost"
          value={priced ? dollars(spend.cost_micros) : "—"}
          note={
            priced
              ? uncosted
                ? `${spend.templates} template${spend.templates === 1 ? "" : "s"} not costed`
                // Three places where the total gets two: the rate is $0.022,
                // and rounding it to the nearest cent prints $0.02 — a tenth
                // out, and confidently so.
                : `at $${(spend.service_micros / 1_000_000).toFixed(3)} a message`
              : "set plan_settings.wa_service_micros"
          }
          tone={priced ? (uncosted ? "warn" : undefined) : "warn"}
          why="В долларах, потому что Meta выставляет счёт в них — пересчёт в фунты означал бы выдуманный курс в роли факта. Цена берётся из plan_settings и штампуется на каждой строке журнала в момент отправки, поэтому смена цены не переписывает уже закрытый месяц. Сообщение внутри 24-часового окна стоит service-тариф ($0.022); шаблон, которым уведомление о тарифе достаёт человека с закрытым окном, стоит дороже, и пока wa_template_micros не задан, такие сообщения считаются, но в сумму не входят — об этом и говорит подпись."
        />
        <Metric
          label="Accounts reached"
          value={spend.people}
          note={spend.busiest > 0 ? `heaviest had ${spend.busiest}` : "nobody yet"}
          why="Сколько разных аккаунтов получили хотя бы одно сообщение в этом месяце, и сколько пришлось на самый тяжёлый из них. Лимит сообщений — на подписчика за его оплаченный период (у недельного плана свой, у месячного свой), а не на нас за месяц, так что счёт растёт вместе с числом подписчиков."
        />
      </div>
      {spend.by_kind.length === 0 ? (
        <p className="hint">Nothing has been sent on WhatsApp this month.</p>
      ) : (
        <Rank data={spend.by_kind} />
      )}
    </>
  );
}

export async function PlanMix() {
  const plans = await planMixOnce();
  if (plans.length === 0) return <p className="hint">Nobody is on a plan yet.</p>;
  return <Rank data={plans} />;
}

export async function AlertsDelivered({ span }: { span: string }) {
  const win = windowFor(span);
  return <Series data={await alertPoints(win, bucketMinutes(win.hours))} />;
}

export const DISTRICTS_PER_PAGE = 12;

export async function districtsPage(span: string, page: number) {
  const rows = await byDistrict(
    windowFor(span),
    DISTRICTS_PER_PAGE + 1,
    (page - 1) * DISTRICTS_PER_PAGE,
  );
  return {
    rows: rows.slice(0, DISTRICTS_PER_PAGE),
    more: rows.length > DISTRICTS_PER_PAGE,
  };
}

export async function AlertDistricts({ span }: { span: string }) {
  const first = await districtsPage(span, 1);
  if (first.rows.length === 0) {
    return <p className="hint">Nothing was sent in this range.</p>;
  }
  return (
    <MoreRank
      key={span}
      rows={first.rows}
      more={first.more}
      load={moreDistricts.bind(null, span)}
      unit="districts"
    />
  );
}

export async function AlertPrices({ span }: { span: string }) {
  const rows = await byPrice(windowFor(span));
  if (rows.length === 0) return <p className="hint">Nothing was sent in this range.</p>;
  return <Rank data={rows} />;
}

export async function everyonePage(
  span: string,
  page: number,
): Promise<MorePage & { total: number }> {
  const win = windowFor(span);
  const { rows, total } = await subscriberPageOnce(win, page, PER_PAGE);

  return {
    total,
    more: total > page * PER_PAGE,
    rows: rows.map((row) => {
      const how = state(row);
      return (
        <tr key={row.user_id} className={how.tint ? `row-${how.tint}` : undefined}>
          <td>
            <span className={`dot dot-${how.dot}`} title={how.why} />
          </td>
          <td>
            <Link href={`/admin/subscribers/${row.user_id}`}>{row.user_id}</Link>
            <div className="metric-note">{how.why}</div>
          </td>
          <td>
            {row.plan}
            {row.plan_until && (
              <div className="metric-note">until {dayOf(row.plan_until)}</div>
            )}
            {row.message_allowance !== null && (
              <div className="metric-note">
                {row.messages_used} / {row.message_allowance} messages
              </div>
            )}
          </td>
          <td>{row.channel ?? "—"}</td>
          <td>
            {row.channel !== "whatsapp" ? (
              "—"
            ) : windowLeft(row.last_inbound_at) ? (
              <span className="good">{windowLeft(row.last_inbound_at)}</span>
            ) : (
              <span className="metric-note">closed</span>
            )}
          </td>
          {/* Red at thirty: that is where delivery stops for somebody who is
              not paying, and where the alert fires for somebody who is. */}
          <td className={row.wa_today >= WA_DAILY_ALERT ? "num bad" : "num"}>
            {row.channel === "whatsapp" ? row.wa_today : "—"}
          </td>
          <td style={{ maxWidth: "12rem" }}>{row.districts ?? "—"}</td>
          <td className="num">{row.sent_window}</td>
          <td className="num">{row.sent_total}</td>
          <td className="num">{since(row.last_sent)}</td>
          <td>{dayOf(row.created_at)}</td>
        </tr>
      );
    }),
  };
}

export async function EveryoneTable({ span }: { span: string }) {
  const win = windowFor(span);
  const first = await everyonePage(span, 1);

  return (
    // Keyed on the range: the "sent in this range" column changes meaning with
    // it, so pages loaded under the old range must not be kept.
    <Paged
      key={win.key}
      load={moreEveryone.bind(null, span)}
      more={first.more}
      per={PER_PAGE}
      total={first.total}
      unit="accounts"
      head={
        <tr>
          <th />
          <th>Account</th>
          <th>Plan</th>
          <th>Channel</th>
          <th>Free window</th>
          <th className="num">WA today</th>
          <th>Areas</th>
          <th className="num">Last {win.short}</th>
          {/* Not "all time" any more: the nightly purge deletes delivery
              history older than two months, so this is what is still stored.
              A column that says "all time" and means "since August" is the
              kind of number somebody builds an argument on. */}
          <th
            className="num"
            title="Alerts still on record. Delivery history older than two months is deleted nightly."
          >
            All kept
          </th>
          <th className="num">Last alert</th>
          <th>Joined</th>
        </tr>
      }
    >
      {first.rows}
    </Paged>
  );
}
