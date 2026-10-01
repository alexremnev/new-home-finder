import Link from "next/link";

import {
  alertPoints, byDistrict, byPrice, WA_DAILY_ALERT,
} from "@/lib/admin-queries";
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
function state(row: {
  status: string;
  sent_window: number;
  failed_window: number;
  channel: string | null;
  plan_until: string | null;
  plan_live: boolean;
  alert_allowance: number | null;
  alerts_used: number;
}): { dot: string; why: string } {
  if (row.status !== "active") return { dot: "idle", why: row.status };
  if (!row.channel) return { dot: "bad", why: "not connected" };
  if (row.failed_window > 0) return { dot: "bad", why: `${row.failed_window} failed` };
  // Read, not recomputed from the date. A WhatsApp month also ends at its
  // allowance, and this row said "delivering" for somebody who had spent
  // theirs — which is the one case where the page has to explain itself,
  // because the date on the next column says the plan is fine.
  if (!row.plan_live) {
    return {
      dot: "idle",
      why:
        row.alert_allowance !== null && row.alerts_used >= row.alert_allowance
          ? `all ${row.alert_allowance} alerts used`
          : "plan ended",
    };
  }
  if (row.sent_window > 0) return { dot: "ok", why: "delivering" };
  return { dot: "idle", why: "nothing matched" };
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
        <tr key={row.user_id}>
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
            {row.alert_allowance !== null && (
              <div className="metric-note">
                {row.alerts_used} / {row.alert_allowance} alerts
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
          <th className="num">All time</th>
          <th className="num">Last alert</th>
          <th>Joined</th>
        </tr>
      }
    >
      {first.rows}
    </Paged>
  );
}
