import type { PortalRun } from "@/lib/admin-queries";

// Which portal readers there are and what the colour of a tile means. Apart
// from `bodies.tsx` for the same reason `proxy/spend.ts` is: none of it needs
// React or a database, and the judgment at the bottom is the part of the panel
// that can be wrong without looking wrong — so it is the part under test.

// The readers that fetch a portal's own pages, one tile each. Named and
// ordered here rather than taken from the database, so that a reader which has
// not run at all still gets a tile — a silent scraper is the thing worth
// seeing, and it cannot show itself.
export type Reader = { source: string; label: string; why: string };

export const PORTALS: Reader[] = [
  {
    source: "rightmove",
    label: "Rightmove",
    why: "Читает страницу поиска по каждому району: все поля берутся из встроенного JSON, отдельные страницы объявлений не запрашиваются. Около 120 КБ на район за прогон. Сортировка — «самые новые», листание останавливается, как только доходит до объявлений старше прошлого обхода, поэтому в обычном режиме это одна страница. Большое число — сколько объявлений этот читатель увидел раньше всех остальных, включая Telegram-фид; это его настоящий вклад. Рядом: «seen» — всё, что попалось, включая найденное кем-то раньше; «stored» — сколько новых строк создал (меньше, чем seen, потому что объявление, опубликованное фидом минутой раньше, уже лежит в базе); «sent» — сколько из этого действительно ушло подписчикам, и расхождение со stored нормально для района, который ещё дочитывается. Трафик — из счётчика libcurl, то есть ровно то, что прошло по сети в сжатом виде.",
  },
  {
    source: "zoopla",
    label: "Zoopla",
    why: "То же, но данные лежат в RSC-потоке страницы, а не в __NEXT_DATA__. Читается только массив собственных объявлений района: рядом лежат ещё два — продвинутые и из соседних районов, — и второй заведомо не наш. Около 63 КБ на район, вдвое дешевле Rightmove. Замечание: Zoopla отвечает адресу сервера 403 под любым отпечатком браузера, поэтому либо этот читатель запускается с домашней машины, либо ему нужен резидентский прокси. Если в состоянии видно «refused» — дело именно в этом.",
  },
  {
    source: "openrent",
    label: "OpenRent",
    why: "Одна страница района отдаёт PROPERTYIDS — все id района целиком, а не только показанные 20. Для незнакомых id спрашивается редирект (3 КБ) — он раскрывает slug, а значит район, число спален и тип; страница объявления качается только для тех, что действительно новые и действительно в нужном районе. Радиус поиска 2 км, поэтому район определяется по slug, а не по запросу. OpenRent отвечает адресу сервера 405, так что здесь та же оговорка, что у Zoopla.",
  },
  {
    source: "onthemarket",
    label: "OnTheMarket",
    why: "Агентский портал: частных арендодателей здесь нет вовсе, и почти всё, что он показывает, стоит ещё и на Rightmove с Zoopla. Поэтому главное число тут всегда маленькое, а большая часть «seen» уходит в копии — сверять надо с панелью «Duplicates across portals», а не читать это как читателя, работающего вхолостую. Читается фиксированный запрос «добавлено за последние 24 часа», а не выдача по порядку и не крутящееся окно: один запрос покрывает девять районов сразу (девять — предел, больше сайт отдаёт пустую страницу вообще без ошибки), так что весь Лондон — это 14 запросов, около 58 КБ каждый, примерно 954 КБ за прогон, и расширение покрытия цену почти не меняет. Страница объявления качается только для действительно новых объявлений в подписанном районе (до 40 за прогон) — только там есть меблировка, животные, минимальный срок и полный посткод; ещё до 15 страниц за прогон уходит на то, чтобы узнать район карточки, у которой в адресе нет ни посткода, ни даже outward code (таких около 8%, и каждую спрашивают один раз за всё время). Полный посткод сайт называет примерно на каждой двенадцатой карточке, остальное выводится из координат и помечается derived. Две особенности, из-за которых плитка желтеет без настоящей поломки: origin сбрасывает нагрузку голым 404 от nginx — читатель ждёт 20 секунд и просит страницу снова, не больше минуты за прогон (счётчики «shed» и «shed_retried»), — а если сайт перестал подтверждать параметры запроса, читатель отказывается читать страницу и пишет degraded вместо того, чтобы отрапортовать пустой район. И кеш CDN на полчаса: получасовые ступеньки на мелком окне — это он, а не тишина рынка.",
  },
  {
    source: "spareroom",
    label: "SpareRoom",
    why: "Комнаты в шеред-флэтах — то, чего в Telegram-фиде нет вообще, поэтому это единственный источник такого стока. Читается не «до вотермарка», а крутящимся окном: 6 страниц за прогон из 60, полный круг за 10 прогонов (~50 минут). Так сделано не из экономии: robots.txt запрещает и сортировку по новизне, и поиск, и все фильтры в query, а дефолтный порядок сайта определяется платными апгрейдами. Замерено 9 октября 2026 — страницы 1-8 через час держали 87 из тех же 88 объявлений, а свежие id попадались и на 1-й, и на 20-й, и на 40-й странице; правило «все id знакомы — стоп» здесь остановилось бы на первой странице и больше никогда ничего не нашло. Цена такого чтения — задержка: объявление доходит в среднем за ~25 минут. «window_from» в счётчиках показывает, с какой страницы начался прогон. Страница объявления качается только для подписанного района (лимит 15 за прогон) — только там есть меблировка, животные и минимальный срок. Полного посткода сайт не публикует нигде: он выводится из координат и помечается derived, так что склейка дублей по нему не работает. Ванных и площади на сайте нет совсем. Прокси запрещён в коде: «refused_and_pinned» означает, что SpareRoom отказал адресу сервера и прогон сознательно не пошёл через платный прокси.",
  },
];

// Red is now about the latest run and nothing else, because that is the only
// question this colour can answer: a reader that failed four hours ago and has
// read cleanly since is working, and a panel that keeps it red until the
// window scrolls past the failure is a panel whose red stops meaning anything.
// So: red if the last run failed, amber if the last run was fine but something
// in the window was not, green only when the window is clean and the last run
// with it.
//
// `refused` used to be red on its own and is amber here. A refusal is one late
// read — the watermark only moves on a complete one, so the next run covers
// the same ground — and the run that was refused is already marked degraded,
// which the window count picks up anyway. A part-read and a rejected listing
// are the same kind of thing: worth seeing, not worth a red tile.
export function portalTone(row: PortalRun | undefined): "good" | "warn" | "bad" {
  if (!row || row.runs === 0) return "bad";
  if (row.last_status === "failed") return "bad";
  if (row.last_status === "degraded") return "warn";
  if (row.bad > 0 || row.refused > 0 || row.partial > 0 || row.invalid > 0) {
    return "warn";
  }
  return "good";
}
