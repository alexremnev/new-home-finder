"use client";

import Link from "next/link";
import { useMemo, useState, useTransition } from "react";

import type { DistrictDay, RecipientDay } from "@/lib/admin-queries";

import { Metric, Series, Why } from "../charts";
import { refreshDays, refreshRecipients } from "./actions";

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

// The same control Panel draws on every other page. Written out here because
// this page's cards live inside a client component that owns their rows.
function Again({ onClick, busy }: { onClick: () => void; busy: boolean }) {
  return (
    <button
      type="button"
      className="panel-refresh"
      onClick={onClick}
      disabled={busy}
      aria-label="Refresh just this"
      title="Refresh just this"
    >
      <svg
        className={busy ? "panel-spin panel-spin-on" : "panel-spin"}
        viewBox="0 0 16 16"
        aria-hidden="true"
        focusable="false"
      >
        <path
          d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 1.5v3.2h-3.2"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </button>
  );
}

type Aggregate = {
  district: string;
  name: string;
  total: number;
  average: number;
  max: number;
  min: number;
  present: number;
};

export function DistrictsView({
  days: initialDays,
  recipients: initialRecipients,
  names,
  label,
  days_in_range,
  span,
}: {
  days: DistrictDay[];
  recipients: RecipientDay[];
  names: Record<string, string>;
  label: string;
  days_in_range: number;
  span: string;
}) {
  // Held in state so a card can replace its own rows without the page being
  // re-rendered. The sorting and paging below are the reason this page keeps
  // its rows rather than its markup: a refresh must not lose your column.
  const [days, setDays] = useState(initialDays);
  const [recipients, setRecipients] = useState(initialRecipients);
  const [busy, start] = useTransition();
  const [sort, setSort] = useState<{ column: ColumnKey; down: boolean }>({
    column: "total",
    down: true,
  });
  const [page, setPage] = useState(1);

  const againDays = () =>
    start(async () => {
      try {
        setDays(await refreshDays(span));
      } catch {
        // The rows already shown stay: an empty table is worse than a stale one.
      }
    });

  const againPeople = () =>
    start(async () => {
      try {
        setRecipients(await refreshRecipients(span));
      } catch {
        /* as above */
      }
    });

  // Every row handed over is already inside the range: the query asked for it.
  const rows = useMemo(() => {
    const held = new Map<string, number[]>();
    for (const one of days) {
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
        average: total / days_in_range,
        max: Math.max(...counts),
        // A row exists only for a day that produced something, so a district
        // that missed a day of the range truly had a zero.
        min: counts.length < days_in_range ? 0 : Math.min(...counts),
        present: counts.length,
      });
    }
    return out;
  }, [days, names, days_in_range]);

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
      held.set(one.day, (held.get(one.day) ?? 0) + one.listings);
    }
    return [...held.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([day, value]) => ({ label: day, value }));
  }, [days]);

  const people = useMemo(() => {
    const held = new Map<
      number,
      { sent: number; channel: string | null; plan: string | null; districts: string[] }
    >();
    for (const one of recipients) {
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
  }, [recipients]);

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
      <div className="dash-head">
        <h1>Districts</h1>
      </div>

      <div className="panel-head">
        <Again onClick={againDays} busy={busy} />
      </div>

      <div className="dash-row">
        <Metric
          label="Listings"
          value={listings}
          tone={listings > 0 ? "good" : "warn"}
          note={days_in_range === 1 ? label.toLowerCase() : `over ${days_in_range} days`}
          why="Сколько объявлений всего пришло по всем районам за период. Считается по максимальному фильтру — без критериев, только район. Это потолок, а не то, что получает конкретный подписчик."
        />
        <Metric
          label="Districts"
          value={rows.length}
          tone={rows.length > 0 ? "good" : "warn"}
          note="with any listing"
        />
        <Metric
          label="Per day"
          value={days_in_range > 0 ? (listings / days_in_range).toFixed(0) : "—"}
          tone={listings > 0 ? "good" : "warn"}
          note={`over ${days_in_range} day${days_in_range === 1 ? "" : "s"}`}
          why="Делится на число дней в выбранном периоде, а не на дни, в которые что-то было: район, молчавший пять дней из семи, в среднем даёт меньше, и считать иначе значило бы ему польстить."
        />
        <Metric
          label="Busiest"
          value={busiest?.district ?? "—"}
          tone={busiest ? "good" : "warn"}
          note={busiest?.name || (busiest ? "" : "no data yet")}
        />
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card panel">
          <div className="panel-head">
            <h2>Listings per day, all districts</h2>
            <Again onClick={againDays} busy={busy} />
          </div>
          <Series data={trend} />
          <Why text="По одной точке на день, в лондонском времени. Данные ведутся с 18 сентября — раньше этой даты точек нет." />
        </div>
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card panel">
          <div className="panel-head">
            <h2>Top 5 — busiest subscribers</h2>
            <Again onClick={againPeople} busy={busy} />
          </div>
          {people.length === 0 ? (
            <p className="hint">Nothing was sent in this range.</p>
          ) : (
            <div className="scroll-x">
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
                      <td className="num">{(one.sent / days_in_range).toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <Why text="Кто получил больше всего уведомлений за период, по sent_at — то есть по тому, что реально ушло. Districts — районы из его активной подписки. Для WhatsApp этот же список с октября является списком самых дорогих подписчиков." />
        </div>
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card panel">
          <div className="panel-head">
            <h2>By district</h2>
            <Again onClick={againDays} busy={busy} />
          </div>

          {shown.length === 0 ? (
            <p className="hint">
              Nothing recorded for this range yet. The rollup runs every two
              minutes, so this fills in on its own.
            </p>
          ) : (
            <div className="scroll-x">
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
            </div>
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
