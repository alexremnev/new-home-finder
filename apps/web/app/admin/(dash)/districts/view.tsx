"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import type { DistrictDay, RecipientDay } from "@/lib/admin-queries";

import { Metric, Series, Why } from "../charts";

// Every range the page offers, as a window of London days ending `back` days
// ago. Yesterday is its own answer rather than a two-day window: "what happened
// yesterday" and "what happened over the last two days" are different
// questions and the second hides the first.
const RANGES = [
  { key: "today", label: "Today", days: 1, back: 0 },
  { key: "yesterday", label: "Yesterday", days: 1, back: 1 },
  { key: "2d", label: "2d", days: 2, back: 0 },
  { key: "3d", label: "3d", days: 3, back: 0 },
  { key: "4d", label: "4d", days: 4, back: 0 },
  { key: "5d", label: "5d", days: 5, back: 0 },
  { key: "6d", label: "6d", days: 6, back: 0 },
  { key: "7d", label: "7d", days: 7, back: 0 },
  { key: "2w", label: "2 weeks", days: 14, back: 0 },
  { key: "month", label: "Month", days: 30, back: 0 },
] as const;

type RangeKey = (typeof RANGES)[number]["key"];

const COLUMNS = [
  { key: "district", label: "District", numeric: false },
  { key: "name", label: "Area", numeric: false },
  { key: "total", label: "Total", numeric: true },
  { key: "average", label: "Average", numeric: true },
  { key: "max", label: "Max", numeric: true },
  { key: "min", label: "Min", numeric: true },
  { key: "present", label: "Days with any", numeric: true },
] as const;

type ColumnKey = (typeof COLUMNS)[number]["key"];

const PER_PAGE = 25;

type Aggregate = {
  district: string;
  name: string;
  total: number;
  average: number;
  max: number;
  min: number;
  present: number;
};

// The days a range covers, newest first, as the YYYY-MM-DD strings the rows use.
function daysIn(today: string, days: number, back: number): string[] {
  const end = new Date(`${today}T00:00:00Z`);
  end.setUTCDate(end.getUTCDate() - back);
  return Array.from({ length: days }, (_, step) => {
    const one = new Date(end);
    one.setUTCDate(one.getUTCDate() - step);
    return one.toISOString().slice(0, 10);
  });
}

export function DistrictsView({
  days,
  recipients,
  names,
  today,
}: {
  days: DistrictDay[];
  recipients: RecipientDay[];
  names: Record<string, string>;
  today: string;
}) {
  const [range, setRange] = useState<RangeKey>("7d");
  const [sort, setSort] = useState<{ column: ColumnKey; down: boolean }>({
    column: "total",
    down: true,
  });
  const [page, setPage] = useState(1);

  const chosen = RANGES.find((one) => one.key === range) ?? RANGES[7];

  const window = useMemo(
    () => new Set(daysIn(today, chosen.days, chosen.back)),
    [today, chosen.days, chosen.back],
  );

  const rows = useMemo(() => {
    const held = new Map<string, number[]>();
    for (const one of days) {
      if (!window.has(one.day)) continue;
      const kept = held.get(one.district) ?? [];
      kept.push(one.listings);
      held.set(one.district, kept);
    }

    const out: Aggregate[] = [];
    for (const [district, counts] of held) {
      const total = counts.reduce((sum, n) => sum + n, 0);
      out.push({
        district,
        name: names[district] ?? "",
        total,
        // Divided by the days in the range, not by the days this district
        // appeared on: a district silent for five of seven days produces less
        // per day, and saying otherwise flatters it.
        average: total / chosen.days,
        max: Math.max(...counts),
        // A row exists only for a day that produced something, so a district
        // that missed a day of the range truly had a zero.
        min: counts.length < chosen.days ? 0 : Math.min(...counts),
        present: counts.length,
      });
    }
    return out;
  }, [days, window, names, chosen.days]);

  const sorted = useMemo(() => {
    const column = sort.column;
    const ordered = [...rows].sort((a, b) => {
      const left = a[column];
      const right = b[column];
      const gap =
        typeof left === "string" && typeof right === "string"
          ? left.localeCompare(right)
          : Number(left) - Number(right);
      // Ties fall back to the district so the order never wobbles between
      // renders, which is what makes paging trustworthy.
      return (sort.down ? -gap : gap) || a.district.localeCompare(b.district);
    });
    return ordered;
  }, [rows, sort]);

  const pages = Math.max(1, Math.ceil(sorted.length / PER_PAGE));
  const here = Math.min(page, pages);
  const shown = sorted.slice((here - 1) * PER_PAGE, here * PER_PAGE);

  const listings = rows.reduce((sum, one) => sum + one.total, 0);
  const busiest = sorted.length
    ? [...rows].sort((a, b) => b.total - a.total)[0]
    : null;

  const trend = useMemo(() => {
    const held = new Map<string, number>();
    for (const one of days) {
      if (!window.has(one.day)) continue;
      held.set(one.day, (held.get(one.day) ?? 0) + one.listings);
    }
    return [...held.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, value]) => ({ label: day, value }));
  }, [days, window]);

  const people = useMemo(() => {
    const held = new Map<
      number,
      { sent: number; channel: string | null; plan: string | null; districts: string[] }
    >();
    for (const one of recipients) {
      if (!window.has(one.day)) continue;
      const kept = held.get(one.user_id) ?? {
        sent: 0,
        channel: one.channel,
        plan: one.plan,
        districts: one.districts ?? [],
      };
      kept.sent += one.sent;
      held.set(one.user_id, kept);
    }
    return [...held.entries()]
      .map(([user_id, rest]) => ({ user_id, ...rest }))
      .sort((a, b) => b.sent - a.sent || a.user_id - b.user_id)
      .slice(0, 5);
  }, [recipients, window]);

  const pick = (column: ColumnKey) => {
    setPage(1);
    setSort((was) =>
      was.column === column
        ? { column, down: !was.down }
        : // A new column starts the way that column is usually read: biggest
          // first for a number, A to Z for a name.
          { column, down: COLUMNS.find((one) => one.key === column)?.numeric ?? true },
    );
  };

  return (
    <>
      <div className="dash-head dash-head-stuck">
        <h1>Districts</h1>
        <div className="window-picker" role="group" aria-label="Range">
          {RANGES.map((one) => (
            <button
              key={one.key}
              type="button"
              onClick={() => {
                setRange(one.key);
                setPage(1);
              }}
              className={one.key === range ? "win win-on" : "win"}
            >
              {one.label}
            </button>
          ))}
        </div>
      </div>

      <div className="dash-row">
        <Metric
          label="Listings"
          value={listings}
          note={chosen.days === 1 ? chosen.label.toLowerCase() : `over ${chosen.days} days`}
          why="Сколько объявлений всего пришло по всем районам за период. Считается по максимальному фильтру — без критериев, только район. Это потолок, а не то, что получает конкретный подписчик."
        />
        <Metric label="Districts" value={rows.length} note="with any listing" />
        <Metric
          label="Per day"
          value={chosen.days > 0 ? (listings / chosen.days).toFixed(0) : "—"}
          note={`over ${chosen.days} day${chosen.days === 1 ? "" : "s"}`}
          why="Делится на число дней в выбранном периоде, а не на дни, в которые что-то было: район, молчавший пять дней из семи, в среднем даёт меньше, и считать иначе значило бы ему польстить."
        />
        <Metric
          label="Busiest"
          value={busiest?.district ?? "—"}
          note={busiest?.name || (busiest ? "" : "no data yet")}
        />
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>Listings per day, all districts</h2>
          <Series data={trend} />
          <Why text="По одной точке на день, в лондонском времени. Данные ведутся с 18 сентября — раньше этой даты точек нет." />
        </div>
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>Top 5 — busiest subscribers</h2>
          {people.length === 0 ? (
            <p className="hint">Nothing was sent in this range.</p>
          ) : (
            <table className="grid">
              <thead>
                <tr>
                  <th>Subscriber</th>
                  <th>Channel</th>
                  <th>Plan</th>
                  <th>Districts</th>
                  <th className="num">Received</th>
                  <th className="num">Per day</th>
                </tr>
              </thead>
              <tbody>
                {people.map((one) => (
                  <tr key={one.user_id}>
                    <td>
                      <Link href={`/admin/subscribers/${one.user_id}`}>
                        #{one.user_id}
                      </Link>
                    </td>
                    <td>{one.channel ?? "—"}</td>
                    <td>{one.plan ?? "—"}</td>
                    <td>
                      {one.districts.length ? one.districts.join(", ") : "—"}
                    </td>
                    <td className="num">{one.sent.toLocaleString("en-GB")}</td>
                    <td className="num">{(one.sent / chosen.days).toFixed(1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Why text="Кто получил больше всего уведомлений за период, по sent_at — то есть по тому, что реально ушло. Districts — районы из его активной подписки. Для WhatsApp этот же список с октября является списком самых дорогих подписчиков." />
        </div>
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>By district</h2>

          {shown.length === 0 ? (
            <p className="hint">
              Nothing recorded for this range yet. The rollup runs every two
              minutes, so this fills in on its own.
            </p>
          ) : (
            <table className="grid">
              <thead>
                <tr>
                  {COLUMNS.map((one) => (
                    <th
                      key={one.key}
                      className={one.numeric ? "num sortable" : "sortable"}
                      aria-sort={
                        sort.column === one.key
                          ? sort.down
                            ? "descending"
                            : "ascending"
                          : "none"
                      }
                    >
                      <button type="button" onClick={() => pick(one.key)}>
                        {one.label}
                        {sort.column === one.key ? (sort.down ? " ↓" : " ↑") : ""}
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {shown.map((one) => (
                  <tr key={one.district}>
                    <td>
                      <strong>{one.district}</strong>
                    </td>
                    <td className="muted">{one.name || "—"}</td>
                    <td className="num">{one.total.toLocaleString("en-GB")}</td>
                    <td className="num">{one.average.toFixed(1)}</td>
                    <td className="num">{one.max}</td>
                    <td className="num">{one.min}</td>
                    <td className="num">{one.present}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {pages > 1 && (
            <div className="pager">
              <button
                type="button"
                className="ghost"
                disabled={here <= 1}
                onClick={() => setPage(here - 1)}
              >
                ← Previous
              </button>
              <span className="hint">
                Page {here} of {pages} · {sorted.length} districts
              </span>
              <button
                type="button"
                className="ghost"
                disabled={here >= pages}
                onClick={() => setPage(here + 1)}
              >
                Next →
              </button>
            </div>
          )}

          <Why text="Клик по любому заголовку сортирует, повторный клик меняет направление. Считается по максимальному фильтру: каждое объявление, впервые увиденное в этот день в этом районе, без других критериев. Min равен нулю, если в каком-то дне периода района не было вовсе — тогда «Days with any» меньше числа дней." />
        </div>
      </div>
    </>
  );
}
