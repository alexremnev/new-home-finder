import Link from "next/link";

import {
  alertPoints, byDistrict, byPrice, planMix, subscriberPage,
} from "@/lib/admin-queries";
import { dayOf, since, windowLeft } from "@/lib/when";

import { Metric, Rank, Series } from "../charts";
import { bucketMinutes, spanFrom, spanWords } from "../span";

// One definition per card. See ../panel.tsx for why they live apart from the
// page: the page renders them inside Suspense so the queries run in parallel,
// and each card's refresh action renders the same component again.

export const PER_PAGE = 20;

// Green means alerts are reaching them. Anything else is a reason, not a
// colour: a paused filter and a dead channel look the same in a list of dots.
function state(row: {
  status: string;
  sent_window: number;
  failed_window: number;
  channel: string | null;
  plan_until: string | null;
}): { dot: string; why: string } {
  if (row.status !== "active") return { dot: "idle", why: row.status };
  if (!row.channel) return { dot: "bad", why: "not connected" };
  if (row.failed_window > 0) return { dot: "bad", why: `${row.failed_window} failed` };
  if (row.plan_until && new Date(row.plan_until.replace(" ", "T")) < new Date()) {
    return { dot: "idle", why: "plan ended" };
  }
  if (row.sent_window > 0) return { dot: "ok", why: "delivering" };
  return { dot: "idle", why: "nothing matched" };
}

export async function SubscriberTiles({ span, page }: { span: string; page: number }) {
  const win = spanFrom(span);
  const [{ rows, total }, plans] = await Promise.all([
    subscriberPage(win, page, PER_PAGE),
    planMix(),
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
        note={`of the ${rows.length} on this page`}
        why="Сколько человек на этой странице действительно получают алерты: за выбранный период ушёл хотя бы один и ни один не упал. Серая точка в списке — подписка есть, но ничего не подошло или план закончился. Красная — канал не привязан или отправка падает."
      />
      <Metric
        label="Alerts sent"
        value={delivered}
        // Nothing sent over the window is the thing worth noticing, and it is
        // not visible from a neutral card.
        tone={delivered > 0 ? "good" : "warn"}
        note={`on this page · ${spanWords(win)}`}
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
  const plans = await planMix();
  if (plans.length === 0) return <p className="hint">Nobody is on a plan yet.</p>;
  return <Rank data={plans} />;
}

export async function AlertsDelivered({ span }: { span: string }) {
  const win = spanFrom(span);
  return <Series data={await alertPoints(win, bucketMinutes(win.hours))} />;
}

export async function AlertDistricts({ span }: { span: string }) {
  const rows = await byDistrict(spanFrom(span));
  if (rows.length === 0) return <p className="hint">Nothing was sent in this range.</p>;
  return <Rank data={rows} />;
}

export async function AlertPrices({ span }: { span: string }) {
  const rows = await byPrice(spanFrom(span));
  if (rows.length === 0) return <p className="hint">Nothing was sent in this range.</p>;
  return <Rank data={rows} />;
}

export async function EveryoneTable({ span, page }: { span: string; page: number }) {
  const win = spanFrom(span);
  const { rows, total } = await subscriberPage(win, page, PER_PAGE);
  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));
  const link = (p: number) => `/admin/subscribers?w=${win.key}&p=${p}`;

  return (
    <>
      <div className="scroll-x">
        <table>
          <thead>
            <tr>
              <th />
              <th>Account</th>
              <th>Plan</th>
              <th>Channel</th>
              <th>Free window</th>
              <th>Areas</th>
              <th className="num">{win.key === "today" || win.key === "yesterday"
                ? win.label
                : `Last ${win.label}`}</th>
              <th className="num">All time</th>
              <th className="num">Last alert</th>
              <th>Joined</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const how = state(row);
              return (
                <tr key={row.user_id}>
                  <td>
                    <span className={`dot dot-${how.dot}`} title={how.why} />
                  </td>
                  <td>
                    <Link href={`/admin/subscribers/${row.user_id}`}>
                      {row.user_id}
                    </Link>
                    <div className="metric-note">{how.why}</div>
                  </td>
                  <td>
                    {row.plan}
                    {row.plan_until && (
                      <div className="metric-note">until {dayOf(row.plan_until)}</div>
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
                  <td style={{ maxWidth: "12rem" }}>{row.districts ?? "—"}</td>
                  <td className="num">{row.sent_window}</td>
                  <td className="num">{row.sent_total}</td>
                  <td className="num">{since(row.last_sent)}</td>
                  <td>{dayOf(row.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="pager">
        <Link className={page <= 1 ? "off" : ""} href={link(page - 1)}>← newer</Link>
        <span>page {page} of {totalPages} · {total} accounts</span>
        <Link className={page >= totalPages ? "off" : ""} href={link(page + 1)}>older →</Link>
      </div>
    </>
  );
}
