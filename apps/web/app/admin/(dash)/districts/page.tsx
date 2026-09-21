import Link from "next/link";

import {
  districtDays, districtTrend, districtWindow, topRecipients,
  type DistrictSort,
} from "@/lib/admin-queries";
import { districtNames } from "@/lib/plans";

import { Metric, Rank, Series, Why } from "../charts";

export const dynamic = "force-dynamic";

const RANGES = [1, 7, 30] as const;
type Days = (typeof RANGES)[number];

const PER_PAGE = 25;
const TOP = 5;

function chosen(params: Record<string, string | undefined>) {
  const asked = Number(params.w ?? 7);
  const days: Days = (RANGES as readonly number[]).includes(asked)
    ? (asked as Days)
    : 7;
  const sort: DistrictSort =
    params.sort === "min" ? "min" : params.sort === "peak" ? "peak" : "max";
  const page = Math.max(1, Math.floor(Number(params.page ?? 1)) || 1);
  return { days, sort, page };
}

function link(days: Days, sort: DistrictSort, page: number): string {
  const params = new URLSearchParams({ w: String(days), sort });
  if (page > 1) params.set("page", String(page));
  return `/admin/districts?${params.toString()}`;
}

export default async function DistrictsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { days, sort, page } = chosen(await searchParams);

  const [window, rows, trend, names, byTotal, byPeak, byFloor, heaviest] =
    await Promise.all([
    districtWindow(days),
    districtDays(days, sort, PER_PAGE, (page - 1) * PER_PAGE),
    districtTrend(days, null),
    districtNames().catch(() => ({}) as Record<string, string>),
    // Three top fives, not four: ranking by the average is ranking by the
    // total, since every district is divided by the same number of days. The
    // average is shown as the value inside the first list instead.
    districtDays(days, "max", TOP, 0),
    districtDays(days, "peak", TOP, 0),
    districtDays(days, "reliable", TOP, 0),
    topRecipients(days, TOP),
  ]);

  // Divided by the days that exist rather than the width of the window: the
  // history starts on 18 September, so a 30-day view on day four would
  // otherwise report a tenth of the truth.
  const perDay = (listings: number) =>
    window.days_covered > 0 ? listings / window.days_covered : 0;

  const pages = Math.max(1, Math.ceil(window.districts / PER_PAGE));

  return (
    <>
      <div className="dash-head">
        <h1>Districts</h1>
        <div className="window-picker" role="group" aria-label="Range">
          {RANGES.map((one) => (
            <Link
              key={one}
              href={link(one, sort, 1)}
              className={one === days ? "win win-on" : "win"}
            >
              {one === 1 ? "Today" : `${one}d`}
            </Link>
          ))}
        </div>
      </div>

      <div className="dash-row">
        <Metric
          label="Listings"
          value={window.listings}
          note={days === 1 ? "today" : `over ${days} days`}
          why="Сколько объявлений всего пришло по всем районам за период. Считается по максимальному фильтру — то есть вообще без критериев, только район. Это потолок, а не то, что получает конкретный подписчик."
        />
        <Metric label="Districts" value={window.districts} note="with any listing" />
        <Metric
          label="Per day"
          value={window.days_covered > 0 ? perDay(window.listings).toFixed(0) : "—"}
          note={`over ${window.days_covered} day${window.days_covered === 1 ? "" : "s"} of data`}
          why="Делится на число дней, за которые есть данные, а не на ширину окна: история начинается с 18 сентября, и на четвёртый день деление на 30 занизило бы всё в десять раз."
        />
        <Metric
          label="Busiest"
          value={window.busiest ?? "—"}
          note={window.busiest ? (names[window.busiest] ?? "") : "no data yet"}
        />
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>Listings per day, all districts</h2>
          <Series data={trend.map((one) => ({ label: one.day, value: one.listings }))} />
          <Why text="По одной точке на день, в лондонском времени. Данные ведутся с 18 сентября — раньше этой даты точек нет, потому что счётчик по районам появился тогда." />
        </div>
      </div>

      <div className="dash-row">
        <div className="card">
          <h2>Top 5 — busiest</h2>
          <Rank
            data={byTotal.map((one) => ({
              label: one.district,
              value: Number(perDay(one.listings).toFixed(1)),
            }))}
            suffix=" a day"
          />
          <Why text="Пять районов с наибольшим общим числом объявлений за период. Показано среднее за день, потому что порядок по среднему и по сумме одинаковый — все районы делятся на одно и то же число дней." />
        </div>

        <div className="card">
          <h2>Top 5 — biggest day</h2>
          <Rank
            data={byPeak.map((one) => ({ label: one.district, value: one.max_day }))}
          />
          <Why text="Самый активный отдельный день за период. Отвечает на другой вопрос, чем сумма: район может дать один всплеск и молчать остальное время." />
        </div>

        <div className="card">
          <h2>Top 5 — never quiet</h2>
          <Rank
            data={byFloor.map((one) => ({ label: one.district, value: one.min_day }))}
          />
          <Why text="Ранжирование по минимуму: самый тихий день района за период. Ненулевое значение бывает только у района, в котором объявления были каждый день окна — это те районы, по которым можно обещать алерты без тишины." />
        </div>
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>Top 5 — busiest subscribers</h2>
          {heaviest.length === 0 ? (
            <p className="hint">Nothing was sent in this range.</p>
          ) : (
            <table className="grid">
              <thead>
                <tr>
                  <th>Subscriber</th>
                  <th>Channel</th>
                  <th>Plan</th>
                  <th className="num">Received</th>
                  <th className="num">Per day</th>
                </tr>
              </thead>
              <tbody>
                {heaviest.map((one) => (
                  <tr key={one.user_id}>
                    <td>
                      <Link href={`/admin/subscribers/${one.user_id}`}>
                        #{one.user_id}
                      </Link>
                    </td>
                    <td>{one.channel ?? "—"}</td>
                    <td>{one.plan ?? "—"}</td>
                    <td className="num">{one.sent.toLocaleString("en-GB")}</td>
                    <td className="num">{perDay(one.sent).toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Why text="Кто получил больше всего уведомлений за период. Считается по sent_at, то есть по тому, что реально ушло, а не по тому, что встало в очередь. Для WhatsApp этот же список с октября — список самых дорогих подписчиков: каждое сообщение тарифицируется, а max_alerts_per_day пока не применяется." />
        </div>
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <div className="card-head">
            <h2>By district</h2>
            <div className="window-picker" role="group" aria-label="Sort">
              <Link
                href={link(days, "max", 1)}
                className={sort === "max" ? "win win-on" : "win"}
              >
                Most first
              </Link>
              <Link
                href={link(days, "min", 1)}
                className={sort === "min" ? "win win-on" : "win"}
              >
                Fewest first
              </Link>
              <Link
                href={link(days, "peak", 1)}
                className={sort === "peak" ? "win win-on" : "win"}
              >
                Biggest day
              </Link>
            </div>
          </div>

          {rows.length === 0 ? (
            <p className="hint">
              Nothing recorded for this range yet. The rollup runs every two
              minutes, so this fills in on its own.
            </p>
          ) : (
            <table className="grid">
              <thead>
                <tr>
                  <th>District</th>
                  <th className="num">Total</th>
                  <th className="num">Average</th>
                  <th className="num">Max</th>
                  <th className="num">Min</th>
                  <th className="num">Days with any</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.district}>
                    <td>
                      <strong>{row.district}</strong>
                      {names[row.district] ? (
                        <span className="muted"> · {names[row.district]}</span>
                      ) : null}
                    </td>
                    <td className="num">{row.listings.toLocaleString("en-GB")}</td>
                    <td className="num">{perDay(row.listings).toFixed(1)}</td>
                    <td className="num">{row.max_day}</td>
                    <td className="num">{row.min_day}</td>
                    <td className="num">{row.days}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {pages > 1 && (
            <div className="pager">
              {page > 1 ? (
                <Link href={link(days, sort, page - 1)} className="ghost">
                  ← Previous
                </Link>
              ) : (
                <span />
              )}
              <span className="hint">
                Page {page} of {pages}
              </span>
              {page < pages ? (
                <Link href={link(days, sort, page + 1)} className="ghost">
                  Next →
                </Link>
              ) : (
                <span />
              )}
            </div>
          )}

          <Why text="Считается по максимальному фильтру: каждое объявление, впервые увиденное в этот день в этом районе, без других критериев. Реальный подписчик с ценой, спальнями и мебелью получит меньше — это верхняя граница. Average — всего поделить на число дней, за которые есть данные, то есть молчаливые дни считаются нулями. Max и Min — самый активный и самый тихий день района; Min равен нулю, если в каком-то дне окна района не было вовсе, и тогда «Days with any» меньше числа дней в окне." />
        </div>
      </div>
    </>
  );
}
