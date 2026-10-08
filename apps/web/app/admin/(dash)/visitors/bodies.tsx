import { visitorPoints, visitorsBy, visitorsByDay } from "@/lib/admin-queries";

import { windowFor } from "../cached";
import { Metric, Series } from "../charts";
import { MoreRank } from "../more-rank";
import { bucketMinutes, bucketWords } from "../span";
import { moreFacet } from "./more";

// What each card on this page contains, as its own async component.
//
// The page renders these inside Suspense, so the ten breakdowns query in
// parallel and the page streams rather than waiting for the slowest. Each
// breakdown shows its longest twelve and loads the rest on demand.

// Two letters is all that is stored, so the name is looked up here rather than
// kept in a column that would need maintaining.
const COUNTRIES = new Intl.DisplayNames(["en"], { type: "region" });

function countryName(code: string | null): string {
  if (!code) return "unknown";
  try {
    return COUNTRIES.of(code) ?? code;
  } catch {
    return code;
  }
}

const LANGUAGES = new Intl.DisplayNames(["en"], { type: "language" });

function languageName(tag: string | null): string {
  if (!tag) return "unknown";
  try {
    return LANGUAGES.of(tag) ?? tag;
  } catch {
    return tag;
  }
}

export type Facet =
  | "source" | "referrer" | "campaign" | "country" | "region" | "city"
  | "os" | "browser" | "device" | "language";

// Title and explanation per facet, so the page is a list rather than ten copies
// of the same card.
export const FACETS: { key: Facet; title: string; why: string }[] = [
  {
    key: "source",
    title: "How they arrived",
    why: "Откуда пришёл посетитель, в одном слове. utm_source из ссылки важнее заголовка Referer: кампания сама говорит, что она такое, а некоторые приложения referrer вырезают. «direct» — либо ссылку открыли без referrer (набрали адрес, тапнули в приложении), либо переход внутри нашего же домена. Домены поисковиков и соцсетей сведены к одному имени: все google.* — это «google».",
  },
  {
    key: "referrer",
    title: "Referring site",
    why: "Хост, с которого пришли, без пути. Путь мы не храним сознательно: в нём может быть чужой поисковый запрос или название группы. Это доказательство под строкой «How they arrived» — там имя, здесь адрес.",
  },
  {
    key: "campaign",
    title: "Campaign",
    why: "utm_campaign из ссылки, а если его нет — utm_medium. Заполняется только у тех, кто пришёл по ссылке с метками, поэтому «unknown» здесь это норма, а не поломка.",
  },
  {
    key: "country",
    title: "Country",
    why: "Из заголовка, который ставит edge Vercel по адресу — сам адрес мы не храним. «unknown» значит, что заголовка не было: так выглядят визиты до появления этой колонки и всё, что открыто не через Vercel.",
  },
  {
    key: "region",
    title: "Region",
    why: "Административная единица внутри страны, тоже от edge Vercel. Для Великобритании это country subdivision — код вроде ENG или SCT.",
  },
  {
    key: "city",
    title: "City",
    why: "Город от edge Vercel. Город — самая честная точность для дашборда: широту и долготу edge тоже отдаёт, с точностью до квартала, но это уже слежка, а не аналитика, поэтому мы их не пишем.",
  },
  {
    key: "os",
    title: "Operating system",
    why: "Угадано по user-agent, а он — заявление браузера, не факт. Порядок проверок важен: Android представляется Linux, а iPadOS — Mac, поэтому сначала проверяются частные случаи.",
  },
  {
    key: "browser",
    title: "Browser",
    why: "По user-agent, без версий: версия — это отпечаток, а вопрос был про браузер. «unknown» бывает только у визитов до появления колонки: с тех пор визит без узнаваемого браузера не записывается вовсе — это и есть основной фильтр от ботов.",
  },
  {
    key: "device",
    title: "Device",
    why: "Три группы по user-agent: mobile, tablet, desktop. Приблизительно, по той же причине — user-agent это заявление.",
  },
  {
    key: "language",
    title: "Language",
    why: "Первый тег из Accept-Language — тот, который браузер предпочитает. Целиком заголовок не храним: это строка с высокой энтропией, годная разве что для отпечатка.",
  },
];

const NAME: Partial<Record<Facet, (value: string | null) => string>> = {
  country: countryName,
  language: languageName,
};

export const FACET_PER_PAGE = 12;

// One row more than the page needs, then dropped: that answers "is there
// another page" without counting the distinct values twice.
export async function facetPage(span: string, facet: Facet, page: number) {
  const rows = await visitorsBy(
    windowFor(span),
    facet,
    FACET_PER_PAGE + 1,
    (page - 1) * FACET_PER_PAGE,
  );
  const label = NAME[facet] ?? ((value: string | null) => value ?? "unknown");

  return {
    rows: rows
      .slice(0, FACET_PER_PAGE)
      .map((one) => ({ label: label(one.name), value: one.visitors })),
    more: rows.length > FACET_PER_PAGE,
  };
}

export async function VisitorFacet({
  span,
  facet,
}: {
  span: string;
  facet: Facet;
}) {
  const first = await facetPage(span, facet, 1);

  if (first.rows.length === 0) {
    return <p className="hint">Nothing recorded for this range.</p>;
  }

  return (
    <MoreRank
      key={span}
      rows={first.rows}
      more={first.more}
      load={moreFacet.bind(null, span, facet)}
      unit="shown"
    />
  );
}

export async function VisitorChart({ span }: { span: string }) {
  const win = windowFor(span);
  const bucket = bucketMinutes(win.hours);
  const points = await visitorPoints(win, bucket);

  return (
    <>
      <p className="panel-note">{bucketWords(bucket)}</p>
      <Series data={points} />
    </>
  );
}

export async function VisitorTiles({ span }: { span: string }) {
  const win = windowFor(span);
  const byDay = await visitorsByDay(win);

  const visitors = byDay.reduce((sum, one) => sum + one.visitors, 0);
  const hits = byDay.reduce((sum, one) => sum + one.hits, 0);
  const best = byDay.reduce<number>((top, one) => Math.max(top, one.visitors), 0);
  const perVisitor = visitors > 0 ? hits / visitors : 0;

  return (
    <div className="tiles">
      <Metric
        label="Visitors"
        value={visitors}
        tone={visitors > 0 ? "good" : "warn"}
        note={win.from ? "selected range" : win.label.toLowerCase()}
        why="Уникальные посетители, впервые пришедшие внутри выбранного периода — то же время прихода (first_at), по которому построен график ниже, поэтому плитка и график всегда согласованы. Раньше здесь читались целиком все календарные дни, которых касается период, и «past 1 day» показывал до 48 часов. Посчитано по дням и сложено: отпечаток посетителя солится датой, поэтому один и тот же человек в два разных дня — это две единицы: так можно считать людей, но нельзя следить за одним. Боты отсекаются дважды: списком тех, кто называет себя роботом, и — что важнее — тем, что визит без узнаваемого браузера не записывается вообще."
      />
      <Metric
        label="Page views"
        value={hits}
        tone={hits > 0 ? "good" : "warn"}
        note="not unique"
        why="Сумма счётчика hits по тем же посетителям. Счётчик живёт на строке «посетитель за день», а не на каждом открытии страницы, поэтому посетитель, пришедший внутри периода, приносит с собой все свои просмотры за этот день — единственное число здесь, которое период не режет по минуте."
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
        why="Самый людный из календарных дней, которых касается период. Для периода короче суток это просто сегодняшняя часть — то же число, что в плитке «Visitors»."
      />
    </div>
  );
}
