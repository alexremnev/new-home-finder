import { visitorPoints, visitorsBy, visitorsByDay } from "@/lib/admin-queries";

import { Metric, Rank, Series } from "../charts";
import { bucketMinutes, bucketWords, spanFrom } from "../span";

// What each card on this page contains, as its own async component.
//
// Defined once and used twice: the page renders these inside Suspense, so the
// panels query in parallel and the page streams; the refresh actions render the
// same components again for one card. There is no second copy of a card's
// contents to keep in step with the first.

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

export async function VisitorFacet({
  span,
  facet,
}: {
  span: string;
  facet: Facet;
}) {
  const rows = await visitorsBy(spanFrom(span), facet);
  const label = NAME[facet] ?? ((value: string | null) => value ?? "unknown");

  if (rows.length === 0) {
    return <p className="hint">Nothing recorded for this range.</p>;
  }

  return (
    <Rank
      data={rows.map((one) => ({ label: label(one.name), value: one.visitors }))}
    />
  );
}

export async function VisitorChart({ span }: { span: string }) {
  const win = spanFrom(span);
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
  const win = spanFrom(span);
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
  );
}
