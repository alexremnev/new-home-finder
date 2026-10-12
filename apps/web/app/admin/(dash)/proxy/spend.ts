import type { ProxySpend } from "@/lib/admin-queries";

// Which scraper jobs there are, what each is expected to cost, and how to read
// the two numbers that come back. Apart from `bodies.tsx` because none of it
// needs React or a database: the judgment below is the only part of this page
// that can be wrong without looking wrong, so it is the part under test.

export type Expect = "proxy" | "direct";

export type Task = {
  job: string;
  /** Whether this reader is expected to need the proxy at all. */
  expect: Expect;
  why: string;
};

// The five scraper jobs, in the order they cost money: the one that pays first.
//
// Job names, not source keys. `zoopla` and `zoopla_london` are two jobs on one
// source key and this whole page exists to tell them apart — see `proxySpend`.
export const TASKS: Task[] = [
  {
    job: "zoopla_london",
    expect: "proxy",
    why: "Единственный читатель, которому прокси нужен по существу: Zoopla отвечает 403 на адрес сервера под любым отпечатком браузера, поэтому весь обход города идёт через резидентский выход. Здесь и должен стоять почти весь счёт DataImpulse. Ноль при идущих прогонах — это не экономия, а поломка: либо SCRAPE_PROXY не задан там, где крутится воркер, либо Zoopla перестала отказывать и об этом никто не узнал. «proxied» — запросы, ушедшие через прокси, «MB paid» — то, что провайдер выставит в счёт; картинки живут на CDN и вынесены из тоннеля, поэтому «MB paid» всегда меньше «MB total».",
  },
  {
    job: "zoopla",
    expect: "direct",
    why: "Тот же сайт и тот же source_key, но другая задача: чтение по одному названному району вместо всего города. Таймера у неё нет — она нужна, чтобы подключить новый район или догнать пропущенное после сбоя, — поэтому в обычный день здесь ноль прогонов, а не ноль трафика. Как только её запускают руками, она платит ровно за то же, за что платит zoopla_london: 403 отдаётся адресу сервера, а не конкретной странице, так что «paying» на этой плитке — ожидаемое последствие ручного запуска, а не новость. Разделить эти два числа и есть причина, по которой страница группирует по имени задачи, а не по source_key: в базе обе пишут «zoopla».",
  },
  {
    job: "openrent",
    expect: "direct",
    why: "OpenRent отвечает 405 на адрес сервера, но захардкоженного запрета на прокси у него нет — значит отказ будет повторён через тоннель и за это придёт счёт. Ненулевая строка здесь означает, что OpenRent начал отказывать всерьёз: читатель берёт PROPERTYIDS со страницы района, а затем по редиректу (3 КБ) узнаёт район и число спален, и таких запросов за прогон десятки. Это самый дешёвый читатель по байтам и самый дорогой по числу запросов, поэтому следить тут надо за «proxied», а не за мегабайтами.",
  },
  {
    job: "rightmove",
    expect: "direct",
    why: "Rightmove адрес сервера не отказывает, поэтому прокси ему не нужен и здесь ожидается чистый ноль. Прогон стоит около 120 КБ на район и берёт все поля из встроенного JSON, не запрашивая отдельные страницы объявлений, так что при появлении отказов этот читатель станет самым дорогим из всех — следить стоит именно за «MB paid».",
  },
  {
    job: "spareroom",
    expect: "direct",
    why: "Прокси запрещён в коде: хост закреплён на прямом маршруте, и отказ не повторяется через тоннель. Поэтому ноль здесь гарантирован кодом, а не поведением сайта, и «pinned» в строке — это не сбой, а зафиксированный выбор: заплатить за SpareRoom можно только правкой кода, и этот счётчик существует, чтобы такое решение принималось осознанно. Читатель при этом самый тяжёлый по трафику — 6 страниц из 60 за прогон, круг за ~50 минут, в три-четыре раза больше байт, чем у zoopla_london, — так что «MB total» тут большой при нулевом «MB paid».",
  },
];

export const PROXY_JOBS = TASKS.map((one) => one.job);

/**
 * Megabytes, because that is the unit a residential proxy is sold in.
 *
 * Two decimals below ten and one above. A tile reading "0.00" has spent
 * something, and the difference between that and a true nought is the whole
 * point of the page — so the only value allowed to print as "0" is zero.
 */
export function mb(bytes: number | string | null | undefined): string {
  const value = Number(bytes ?? 0) / 1024 / 1024;
  if (!Number.isFinite(value) || value === 0) return "0";
  if (value < 0.01) return "<0.01";
  if (value < 10) return value.toFixed(2);
  return value.toFixed(1);
}

export type Tone = "ok" | "warn" | "bad" | "idle";

/**
 * The stripe down the side of a tile, answering one question: is this reader
 * costing what it is supposed to cost?
 *
 * Which is why the expectation is part of the input. A nought is good news on
 * a reader that should be free and bad news on `zoopla_london`, where it means
 * either that SCRAPE_PROXY is unset where the worker runs or that Zoopla
 * stopped refusing — and printing the same green tile for both was the one way
 * this page could mislead.
 */
export function tone(row: ProxySpend | undefined, expect: Expect): Tone {
  if (!row || row.runs === 0) return "idle";
  // The second vendor is only ever reached after the primary pool has spent
  // its whole budget on refusals, so its presence is bad news whoever it
  // happens to — and it is a second invoice as well.
  if (row.fallback_requests > 0) return "bad";
  if (expect === "direct") return row.proxy_requests > 0 ? "warn" : "ok";
  return row.proxy_requests > 0 ? "ok" : "warn";
}

/** The pill beside the job name: what the stripe means, in two words. */
export function word(expect: Expect, mood: Tone): string {
  if (mood === "idle") return "no runs";
  if (mood === "bad") return "fallback";
  if (expect === "direct") return mood === "warn" ? "paying" : "free";
  return mood === "warn" ? "went direct" : "proxied";
}
