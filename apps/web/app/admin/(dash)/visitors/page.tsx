import {
  visitorPoints, visitorsByBrowser, visitorsByCountry, visitorsByDay,
  visitorsByDevice,
} from "@/lib/admin-queries";

import { Metric, Rank, Series, Why } from "../charts";
import { DEFAULT_SPAN, bucketMinutes, bucketWords, spanFrom } from "../span";

export const dynamic = "force-dynamic";

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
  const win = spanFrom(params.w ?? DEFAULT_SPAN);
  const bucket = bucketMinutes(win.hours);

  const [byDay, byCountry, byDevice, byBrowser, points] = await Promise.all([
    visitorsByDay(win),
    visitorsByCountry(win),
    visitorsByDevice(win),
    visitorsByBrowser(win),
    visitorPoints(win, bucket),
  ]);

  const visitors = byDay.reduce((sum, one) => sum + one.visitors, 0);
  const hits = byDay.reduce((sum, one) => sum + one.hits, 0);
  const best = byDay.reduce<number>((top, one) => Math.max(top, one.visitors), 0);
  const perVisitor = visitors > 0 ? hits / visitors : 0;

  return (
    <>
      <div className="dash-head">
        <h1>Visitors</h1>
      </div>

      <div className="dash-row">
        <Metric
          label="Visitors"
          value={visitors}
          tone={visitors > 0 ? "good" : "warn"}
          note={win.days === 1 ? win.label.toLowerCase() : `over ${win.days} days`}
          why="Уникальные посетители, посчитанные по дням и сложенные. Отпечаток посетителя солится датой, поэтому один и тот же человек в два разных дня — это две единицы: так можно считать людей, но нельзя следить за одним. Боты отсекаются дважды: списком тех, кто называет себя роботом, и — что важнее — тем, что визит без узнаваемого браузера не записывается вообще."
        />
        <Metric
          label="Page views"
          value={hits}
          tone={hits > 0 ? "good" : "warn"}
          note="all visits"
        />
        <Metric
          label="Views each"
          value={visitors > 0 ? perVisitor.toFixed(1) : "—"}
          tone={visitors > 0 ? "good" : "warn"}
          note="per visitor"
          why="Сколько раз в среднем один посетитель открывал страницу. Около единицы — пришли и ушли; заметно больше — возвращаются или перезагружают."
        />
        <Metric
          label="Best day"
          value={best || "—"}
          tone={best > 0 ? "good" : "warn"}
          note="most visitors"
        />
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>Visitors, {bucketWords(bucket)}</h2>
          <Series data={points} />
          <Why text="Шаг бакета зависит от выбранного сверху периода и подписан в заголовке. Считается по first_at, то есть по времени прихода, поэтому один посетитель попадает ровно в один бакет. Неделя, нарисованная по часам, — это 168 точек шума, поэтому шаг растёт вместе с периодом. Таблицы ниже считаются по дням: строка в site_visits — одна на человека в день, поэтому часовой период читает сегодняшний день целиком." />
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
          <h2>Which browser</h2>
          <Rank
            data={byBrowser.map((one) => ({
              label: one.name ?? "unknown",
              value: one.visitors,
            }))}
          />
          <Why text="Определяется по user-agent, без версий: версия — это отпечаток, а вопрос был про браузер. «unknown» бывает только у визитов, записанных до появления этой колонки: с этого момента визит без узнаваемого браузера не записывается вовсе — это и есть основной фильтр от ботов." />
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
