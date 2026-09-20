import Link from "next/link";

import { visitorsByCountry, visitorsByDay, visitorsByDevice } from "@/lib/admin-queries";

import { Metric, Rank, Series, Why } from "../charts";

export const dynamic = "force-dynamic";

const RANGES = [1, 2, 7] as const;
type Days = (typeof RANGES)[number];

// Two letters is all that is stored, so the name is looked up here rather than
// kept in a column that would need maintaining.
const NAMES = new Intl.DisplayNames(["en"], { type: "region" });

function place(code: string | null): string {
  if (!code) return "unknown";
  try {
    return NAMES.of(code) ?? code;
  } catch {
    return code;
  }
}

export default async function VisitorsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const asked = Number(params.d ?? 1);
  const days: Days = (RANGES as readonly number[]).includes(asked) ? (asked as Days) : 1;

  const [byDay, byCountry, byDevice] = await Promise.all([
    visitorsByDay(days),
    visitorsByCountry(days),
    visitorsByDevice(days),
  ]);

  const visitors = byDay.reduce((sum, one) => sum + one.visitors, 0);
  const hits = byDay.reduce((sum, one) => sum + one.hits, 0);
  const best = byDay.reduce<number>((top, one) => Math.max(top, one.visitors), 0);
  const perVisitor = visitors > 0 ? hits / visitors : 0;

  return (
    <>
      <div className="dash-head">
        <h1>Visitors</h1>
        <div className="window-picker" role="group" aria-label="Range">
          {RANGES.map((one) => (
            <Link
              key={one}
              href={`/admin/visitors?d=${one}`}
              className={one === days ? "win win-on" : "win"}
            >
              {one === 1 ? "Today" : `${one}d`}
            </Link>
          ))}
        </div>
      </div>

      <div className="dash-row">
        <Metric
          label="Visitors"
          value={visitors}
          note={days === 1 ? "today" : `over ${days} days`}
          why="Уникальные посетители, посчитанные по дням и сложенные. Отпечаток посетителя солится датой, поэтому один и тот же человек в два разных дня — это две единицы: так можно считать людей, но нельзя следить за одним. Боты отбрасываются по user-agent."
        />
        <Metric label="Page views" value={hits} note="all visits" />
        <Metric
          label="Views each"
          value={visitors > 0 ? perVisitor.toFixed(1) : "—"}
          note="per visitor"
          why="Сколько раз в среднем один посетитель открывал страницу. Около единицы — пришли и ушли; заметно больше — возвращаются или перезагружают."
        />
        <Metric label="Best day" value={best || "—"} note="most visitors" />
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>Visitors per day</h2>
          <Series
            data={[...byDay]
              .reverse()
              .map((one) => ({ label: one.day, value: one.visitors }))}
          />
          <Why text="По одной точке на день. Часы здесь не показать: таблица хранит посетителя одной строкой на сутки, а не по времени прихода." />
        </div>
      </div>

      <div className="dash-row">
        <div className="card">
          <h2>Where from</h2>
          <Rank
            data={byCountry.map((one) => ({
              label: place(one.name),
              value: one.visitors,
            }))}
          />
          <Why text="Страна берётся из заголовка, который ставит edge Vercel по адресу — сам адрес мы не храним. «unknown» значит, что заголовка не было: так выглядят визиты, записанные до этой возможности, и всё, что открыто не через Vercel." />
        </div>

        <div className="card">
          <h2>On what</h2>
          <Rank
            data={byDevice.map((one) => ({
              label: one.name ?? "unknown",
              value: one.visitors,
            }))}
          />
          <Why text="Три группы, угаданные по user-agent: mobile, tablet, desktop. User-agent — это заявление браузера, а не факт, так что числа приблизительные." />
        </div>
      </div>
    </>
  );
}
