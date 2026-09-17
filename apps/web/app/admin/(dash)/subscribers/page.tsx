import Link from "next/link";

import {
  alertPoints, byDistrict, byPrice, planMix, subscriberPage,
} from "@/lib/admin-queries";

import { Metric, Rank, Series, Why } from "../charts";
import { DEFAULT_WINDOW, WindowPicker, bucketMinutes, windowFrom } from "../window";

import { dayOf, since } from "@/lib/when";

export const dynamic = "force-dynamic";

const PER_PAGE = 20;



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

export default async function SubscribersPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const win = windowFrom(params.w ?? DEFAULT_WINDOW);
  const page = Math.max(1, Number(params.p ?? 1) || 1);

  // byDistrict and byPrice are written against whole days; the shortest window
  // they can honestly answer is one.
  const days = Math.max(1, Math.round(win.hours / 24)) as 1 | 7 | 30 | 90;
  const [{ rows, total }, plans, sent, districts, prices] = await Promise.all([
    subscriberPage(win.hours, page, PER_PAGE),
    planMix(),
    alertPoints(win.hours, bucketMinutes(win.hours)),
    byDistrict(days),
    byPrice(days),
  ]);

  const totalPages = Math.max(1, Math.ceil(total / PER_PAGE));
  const link = (p: number) => `/admin/subscribers?w=${win.key}&p=${p}`;
  const reaching = rows.filter((row) => state(row).dot === "ok").length;
  const delivered = rows.reduce((sum, row) => sum + row.sent_window, 0);

  return (
    <>
      <div className="dash-head">
        <h1>Subscribers</h1>
        <WindowPicker here="/admin/subscribers" chosen={win.key} />
      </div>

      <div className="dash-row">
        <Metric label="Accounts" value={total} note="everyone not erased" />
        <Metric
          label="Delivering"
          value={`${reaching}/${rows.length}`}
          tone={reaching === rows.length ? "good" : undefined}
          note="green on this page"
          why="Зелёный значит, что за выбранный период человеку ушёл хотя бы один алерт и ни один не упал. Серый — подписка есть, но ничего не подошло или план закончился. Красный — канал не привязан или отправка падает."
        />
        <Metric
          label="Alerts sent"
          value={delivered}
          note={`on this page · last ${win.label}`}
        />
        <div className="card">
          <h3>
            Plans
            <Why text="Сколько аккаунтов на каком тарифе. trial — пробный период, free — то, куда падает закончившийся план, week и month — платные." />
          </h3>
          <Rank data={plans} />
        </div>
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>
            Alerts delivered
            <Why text="Сколько уведомлений ушло за период, по времени. Всплески вечером — нормально: объявления публикуют неравномерно." />
          </h2>
          <Series data={sent} />
        </div>
        <div className="card">
          <h2>
            Where the alerts went
            <Why text="Районы, по которым чаще всего совпадают фильтры подписчиков. Считается по целым дням, поэтому для окон короче суток показывает сутки." />
          </h2>
          <Rank data={districts} />
        </div>
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>
            By rent
            <Why text="Разбивка отправленных объявлений по арендной плате, полосами по £250. Показывает, в каком бюджете люди действительно ищут." />
          </h2>
          <Rank data={prices} />
        </div>
      </div>

      <div className="card">
        <h2>
          Everyone
          <Why text="По 20 на страницу, новые сверху. Нажмите на номер — вся аналитика по человеку, смена плана, его платежи и история фильтров." />
        </h2>

        <div className="scroll-x">
          <table>
            <thead>
              <tr>
                <th />
                <th>Account</th>
                <th>Plan</th>
                <th>Channel</th>
                <th>Areas</th>
                <th className="num">Last {win.label}</th>
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
      </div>
    </>
  );
}
