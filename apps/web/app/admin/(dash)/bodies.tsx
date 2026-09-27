import Link from "next/link";

import type { PortalRun, ReaderOverlap, ReaderTally } from "@/lib/admin-queries";
import {
  delivery, duplicates, intakePoints, jobStates, knownJobs, logPage,
  messagePoints, portalRuns, problems, readerOverlap, readerTally, recentRuns,
  runPoints, scrapeBytes, sightingsSince, sourceFeeds, unparseablePoints,
} from "@/lib/admin-queries";
import { ago, at } from "@/lib/when";

import { Metric, RunBars, Series, Why } from "./charts";
import { bucketMinutes, spanFrom, spanWords } from "./span";

// What each card on the System tab contains, one component each.
//
// The page renders these inside Suspense, so twelve queries run in parallel and
// the page arrives without waiting for the slowest; each card's refresh action
// renders the same component again for that card alone. See ./panel.tsx.

// Named and ordered here, not taken from whatever the database happens to
// return: a source that has never produced anything still needs a panel, and a
// red one is the whole point.
//
// `portal` is which site the listing is from; `feed` is how it reached us — a
// listing came from the Telegram feed exactly when a source message points at
// it, and from a scraper when none does.
const FEEDS: {
  label: string;
  portal: string;
  feed: boolean;
  why: string;
  // Only the scraper downloads anything of ours to measure: a feed listing
  // arrives inside a Telegram message somebody else paid to deliver.
  traffic?: boolean;
}[] = [
  {
    label: "tg → Rightmove",
    portal: "rightmove",
    feed: true,
    why: "Объявления Rightmove, пришедшие через Telegram-фид. Зелёный — что-то пришло за последний час; жёлтый — было сегодня, но в этот час тихо; красный — за сутки ничего.",
  },
  {
    label: "tg → Zoopla",
    portal: "zoopla",
    feed: true,
    why: "То же для Zoopla. Источник определяется по ссылке в сообщении фида, а не по названию канала.",
  },
  {
    label: "tg → OpenRent",
    portal: "openrent",
    feed: true,
    why: "OpenRent через Telegram-фид. Красный — ожидаемо: фид его не публикует, это и была причина завести отдельный скрапер. Станет зелёным, если фид когда-нибудь начнёт.",
  },
  {
    label: "scraper → OpenRent",
    portal: "openrent",
    feed: false,
    traffic: true,
    why: "OpenRent из собственного скрапера — объявления, за которыми не стоит ни одно сообщение фида. Это основной источник OpenRent; фид остаётся страховкой, и если он найдёт то же объявление, оно склеится в ту же строку.",
  },
];

// Ten is enough to see what is wrong. Uncapped, one repeating check buries
// every other kind — and the kinds are the information.
const FAULTS_SHOWN = 10;

// Bytes as somebody reads them. One decimal, because the second never changed
// a decision.
function weight(bytes: number): string {
  if (bytes <= 0) return "no traffic";
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function feedTone(day: number, hour: number): "good" | "warn" | "bad" {
  if (hour > 0) return "good";
  return day > 0 ? "warn" : "bad";
}

// Russian, as asked: the names being explained are English and whoever reads
// this did not write them.
const JOB_WHY: Record<string, string> = {
  ingest:
    "Читает новые сообщения из Telegram-фида, разбирает их в объявления и ставит " +
    "совпадения в очередь. Запускается каждые две минуты. Если молчит — новых " +
    "объявлений не появится вообще.",
  rightmove:
    "Читает страницы поиска Rightmove — по одной на район, около 120 КБ, все поля " +
    "из встроенного в страницу JSON. По расписанию рабочего дня: в будни с 7:20 " +
    "до 17 каждые 20 минут, с 17 до 22 раз в час, ночью не запускается; в " +
    "выходные с 10 до 20 раз в час. Запускается на сервере: Rightmove его " +
    "адресу отдаёт. Если молчит — Rightmove перестанет приходить со скрапера, " +
    "но фид его публикует, так что часть объявлений всё равно дойдёт.",
  zoopla:
    "То же для Zoopla, около 63 КБ на район. Важно: Zoopla отвечает адресу " +
    "сервера 403 под любым отпечатком браузера, а домашней машине отдаёт " +
    "нормально — поэтому либо этот читатель стоит в планировщике на Windows, " +
    "либо ему нужен резидентский прокси. «Отказов» в состоянии означает именно это.",
  openrent_v2:
    "Читает OpenRent со страниц поиска: одна страница района отдаёт все его id " +
    "целиком, дальше страница объявления качается только для действительно " +
    "новых. Заменил старый scrape, который тянул карту сайта всей страны — " +
    "около 950 МБ в сутки. OpenRent тоже отвечает серверу 405, оговорка та же, " +
    "что у Zoopla. Если молчит — OpenRent перестанет приходить вовсе: фид его " +
    "не публикует, это и была причина завести отдельный читатель.",
  portals:
    "Все три портальных читателя одним прогоном. По расписанию не стоит — это " +
    "для ручного обхода. Если появился здесь, значит кто-то запускал вручную.",
  scrape:
    "Старый читатель OpenRent по карте сайта. Выключен установщиком: качал " +
    "карту сайта всей страны каждый прогон — около 950 МБ в сутки ради двух-трёх " +
    "объявлений, — а на страницы объявлений OpenRent отвечает серверу 405. " +
    "Пустая строка здесь — норма. Его работу делает openrent_v2.",
  drain:
    "Отправляет то, что стоит в очереди: уведомления об окончании плана, вечерний " +
    "дайджест и сами алерты. Каждые две минуты. Если молчит — объявления есть, но " +
    "до людей не доходят.",
  rollup:
    "Пересчитывает статистику за последние 3 дня в таблицы daily_stats и " +
    "district_days, чтобы графики открывались мгновенно. Если молчит — цифры на " +
    "графиках устаревают, но доставка не страдает.",
  report:
    "Строит отчёт о состоянии и пишет в ops-чат, если что-то встало. Раз в час. " +
    "Если молчит — вы просто не узнаете о поломке так быстро.",
};

// job_runs.status has five values and the page had two. A run in flight was
// therefore drawn as a failure, and the banner said Degraded while a job was
// doing its job.
const JOB_TONE: Record<string, "ok" | "run" | "warn" | "bad"> = {
  ok: "ok",
  running: "run",
  // A skipped run means the previous one was still going: normal, not a fault.
  skipped_locked: "ok",
  degraded: "warn",
  failed: "bad",
};

const JOB_WORD: Record<string, string> = {
  ok: "ok",
  run: "running",
  warn: "degraded",
  bad: "failed",
  idle: "silent",
};

const LEVEL_TONE: Record<string, string> = {
  error: "bad",
  warn: "warn",
  info: "good",
  debug: "",
};

export async function Health({ span }: { span: string }) {
  const win = spanFrom(span);
  const jobs = await jobStates(win);

  // A job counts as broken when its most recent run was not successful, and as
  // silent when it has not run at all in the window. Both are degraded; the
  // wording tells them apart, because the fixes differ.
  // Running is neither broken nor silent; it is the healthy middle of a run.
  const broken = jobs.filter(
    (job) => job.last_status === "failed" || job.last_status === "degraded",
  );
  const silent = jobs.filter((job) => job.runs === 0);
  const healthy = jobs.length > 0 && broken.length === 0 && silent.length === 0;

  return (
    <div className={healthy ? "verdict verdict-ok" : "verdict verdict-bad"}>
      <span className="verdict-dot" />
      <span>{jobs.length === 0 ? "No runs" : healthy ? "Healthy" : "Degraded"}</span>
      <span className="verdict-note">
        {jobs.length === 0
          ? `nothing ran ${spanWords(win)}`
          : healthy
            ? `all ${jobs.length} jobs ran successfully · ${spanWords(win)}`
            : [
                broken.length > 0 && `${broken.map((j) => j.job).join(", ")} failing`,
                silent.length > 0 && `${silent.map((j) => j.job).join(", ")} silent`,
              ]
                .filter(Boolean)
                .join(" · ")}
      </span>
    </div>
  );
}

export async function Faults() {
  const faults = await problems().catch(() => []);
  if (faults.length === 0) {
    return <p className="hint">Nothing is wrong that these checks can see.</p>;
  }

  return (
    <>
      {faults.slice(0, FAULTS_SHOWN).map((fault) => (
        <div key={fault.kind + fault.detail} className="log-line">
          <span className="log-when">{at(fault.last_at)}</span>
          <span className="log-level bad">{fault.kind}</span>
          <span className="log-message">
            {fault.detail}
            {fault.count > 1 ? ` · ×${fault.count}` : ""}
          </span>
        </div>
      ))}

      {faults.length > FAULTS_SHOWN && (
        <p className="hint">
          {faults.length - FAULTS_SHOWN} more of the same kind, not shown. The
          list is capped so one noisy check cannot bury the rest.
        </p>
      )}
    </>
  );
}

export async function Feeds({ span }: { span: string }) {
  const win = spanFrom(span);
  const [feeds, downloaded] = await Promise.all([
    sourceFeeds(),
    scrapeBytes(win, "openrent"),
  ]);

  return (
    <div className="tiles">
      {FEEDS.map((one) => {
        const found = feeds.find(
          (row) => row.portal === one.portal && row.from_feed === one.feed,
        );
        const day = found?.day ?? 0;
        const hour = found?.hour ?? 0;
        return (
          <Metric
            key={one.label}
            label={one.label}
            value={day}
            tone={feedTone(day, hour)}
            note={[
              found?.newest ? `last ${ago(found.newest)}` : "nothing in 30 days",
              // Only the scraper has a bill attached to it, and it follows the
              // range above rather than a fixed day.
              one.traffic ? `${weight(downloaded)} ${spanWords(win)}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
            why={
              one.traffic
                ? one.why +
                  " Объём — сколько скрапер скачал за выбранный сверху период, по счётчикам прогонов. Запрос идёт без сжатия, поэтому это ровно то, что прошло по сети. Большая часть этого — карта сайта: в ней нет lastmod, поэтому она качается целиком каждый прогон."
                : one.why
            }
          />
        );
      })}
    </div>
  );
}

// The readers that fetch a portal's own pages, one row each. Named and
// ordered here rather than taken from the database, so that a reader which has
// not run at all still gets a panel — a silent scraper is the thing worth
// seeing, and it cannot show itself.
// The readers that fetch a portal's own pages, one tile each. Named and
// ordered here rather than taken from the database, so that a reader which has
// not run at all still gets a tile — a silent scraper is the thing worth
// seeing, and it cannot show itself.
const PORTALS: { source: string; label: string; why: string }[] = [
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
    source: "openrent_v2",
    label: "OpenRent (search)",
    why: "Новый читатель OpenRent. Одна страница района отдаёт PROPERTYIDS — все id района целиком, а не только показанные 20. Для незнакомых id спрашивается редирект (3 КБ) — он раскрывает slug, а значит район, число спален и тип; страница объявления качается только для тех, что действительно новые и действительно в нужном районе. Радиус поиска 2 км, поэтому район определяется по slug, а не по запросу. OpenRent отвечает адресу сервера 405, так что здесь та же оговорка, что у Zoopla.",
  },
  {
    source: "openrent",
    label: "OpenRent (sitemap)",
    why: "Старый читатель. Выключен установщиком: он качал карту сайта всей страны каждый прогон — около 950 МБ в сутки ради двух-трёх объявлений, — а на страницы объявлений OpenRent отвечает серверу 405. Пустая плитка здесь — норма, а не поломка. Оставлен рядом, чтобы было с чем сравнить новый.",
  },
];

function portalTone(row: PortalRun | undefined, expected: boolean): "good" | "warn" | "bad" {
  // A reader that is deliberately off is not a fault, so it stays neutral.
  if (!row || row.runs === 0) return expected ? "bad" : "warn";
  if (row.bad > 0 || row.refused > 0) return "bad";
  // A district left half-read means the page cap stopped a sweep early. Not
  // broken, but it will not settle until a run finishes the district.
  if (row.partial > 0 || row.invalid > 0) return "warn";
  return "good";
}

// Tiles, not a table.
//
// Two attempts at a wide table broke this tab, and the reason is structural
// rather than a detail to be tuned: `.grid` is width: 100%, so inside a card it
// cannot overflow and scroll — it squeezes, and every heading collapses into a
// stack of single words. Every other panel here uses `.tiles`, which is a grid
// of cards that reflows at any width, and matching it is both safer and more
// consistent than making tables a special case.
//
// The headline number is what the scraper found FIRST, before any other
// reader. That is its real contribution: `stored` undercounts it, because a
// listing the feed published a minute earlier is already in the table and the
// scraper writes nothing.
export async function Portals({ span }: { span: string }) {
  const win = spanFrom(span);
  const [rows, tally] = await Promise.all([portalRuns(win), readerTally(win)]);

  const billable = rows.reduce((sum, row) => sum + Number(row.proxy_bytes ?? 0), 0);
  const total = rows.reduce((sum, row) => sum + Number(row.bytes ?? 0), 0);

  return (
    <>
      <div className="tiles">
        {PORTALS.map((one) => {
          const row = rows.find((r) => r.source === one.source);
          const mine = tally.find((t) => t.reader === one.source);
          const expected = one.source !== "openrent";
          const quiet = !row || row.runs === 0;
          return (
            <Metric
              key={one.source}
              label={one.label}
              value={mine?.got_first ?? 0}
              tone={portalTone(row, expected)}
              note={[
                quiet ? (expected ? "never ran" : "switched off") : null,
                mine ? `${mine.saw} seen` : null,
                row ? `${row.stored} stored` : null,
                row ? `${row.announced} sent` : null,
                row ? weight(Number(row.bytes ?? 0)) : null,
                row && row.runs > 0 ? `${row.runs} runs` : null,
                row?.last_at ? ago(row.last_at) : null,
                row && row.refused > 0 ? `${row.refused} refused` : null,
                row && row.bad > 0 ? `${row.bad} failed` : null,
                row && row.invalid > 0 ? `${row.invalid} rejected` : null,
                row && row.partial > 0 ? `${row.partial} part-read` : null,
              ]
                .filter(Boolean)
                .join(" · ")}
              why={one.why}
            />
          );
        })}
      </div>
      <p className="hint">
        {`found first ${spanWords(win)} · ${weight(total)} downloaded`}
        {billable > 0
          ? ` · ${weight(billable)} of it through the proxy, which is what DataImpulse bills`
          : " · no proxy in use, everything went out directly"}
      </p>
    </>
  );
}

const PORTAL_NAMES: Record<string, string> = {
  rightmove: "Rightmove",
  zoopla: "Zoopla",
  openrent: "OpenRent",
};

function lead(seconds: number | null): string {
  if (seconds === null) return "";
  const amount = Math.abs(seconds);
  const how = amount < 90 ? `${amount}s` : `${Math.round(amount / 60)} min`;
  if (amount < 30) return "neck and neck";
  return seconds > 0 ? `scraper ${how} later` : `scraper ${how} sooner`;
}

// Can the Telegram feed be switched off? One tile per portal, and the headline
// number is the only one that answers it: listings the feed found and the
// scraper did not, in a district the scrapers actually read.
export async function FeedVersusScrapers({ span }: { span: string }) {
  const win = spanFrom(span);
  const [rows, since] = await Promise.all([readerOverlap(win), sightingsSince()]);

  const missed = rows.reduce((sum, row) => sum + row.feed_only_covered, 0);
  const seen = rows.reduce((sum, row) => sum + row.total, 0);

  if (seen === 0) {
    return (
      <p className="hint">
        Nothing to compare in this range. Recording started{" "}
        {since ? at(since) : "— not yet"}: before then nothing wrote down which
        reader saw a listing, and that cannot be reconstructed afterwards.
      </p>
    );
  }

  return (
    <>
      <div className="tiles">
        {rows.map((row) => (
          <Metric
            key={row.portal}
            label={PORTAL_NAMES[row.portal] ?? row.portal}
            value={row.feed_only_covered}
            tone={row.feed_only_covered > 0 ? "bad" : "good"}
            note={[
              `${row.total} listings`,
              `${row.both} both`,
              `${row.feed_only} feed only`,
              `${row.scraper_only} scraper only`,
              row.both > 0
                ? `first: ${row.scraper_first} scraper / ${row.feed_first} feed`
                : null,
              row.both > 0 ? lead(row.median_lead_secs) : null,
            ]
              .filter(Boolean)
              .join(" · ")}
            why="Большое число — объявления, которые нашёл фид, а скрапер нет, и которые лежали в районе, который скрапер читает. Это единственная цифра, означающая промах: объявление в районе, который никто не выбрал, скрапер не смотрит по замыслу, поэтому «feed only» само по себе ничего не значит. Устойчивый ноль здесь — основание выключить Telegram-источник. «first» — сколько раз первым был скрапер против фида, и медианная разница: читатель, который находит всё, но на пять минут позже, для уведомлений заменой не является. Группировка идёт по id объявления на портале, а не по строке в базе: для Rightmove и Zoopla фид и скрапер пишут в одну строку, а у OpenRent старый и новый читатели — две строки с одним номером."
          />
        ))}
      </div>
      <p className="hint">
        {missed === 0
          ? "The scrapers missed nothing the feed found in a district they read."
          : `The feed found ${missed} listings in districts the scrapers read and they did not — too early to switch it off.`}
        {since ? ` · recording since ${at(since)}` : ""}
      </p>
    </>
  );
}

export async function Duplicates({ span }: { span: string }) {
  const win = spanFrom(span);
  const copies = await duplicates(win);

  return (
    <>
      <div className="dupe-big">{copies.copies.toLocaleString("en-GB")}</div>
      <p className="hint">
        copies suppressed {spanWords(win)}
        {copies.listings > 0
          ? ` · ${Math.round((copies.copies / copies.listings) * 100)}% of ${copies.listings.toLocaleString("en-GB")} listings`
          : ""}
      </p>

      {copies.pairs.length === 0 ? (
        <p className="hint">
          No pair found in this range. Either the portals published nothing in
          common, or nothing arrived at all — the panels above say which.
        </p>
      ) : (
        <div className="scroll-x">
          <table className="grid">
            <thead>
              <tr>
                <th>Copy came from</th>
                <th>Kept the one from</th>
                <th className="num">Copies</th>
              </tr>
            </thead>
            <tbody>
              {copies.pairs.map((pair) => (
                <tr key={`${pair.copy}:${pair.kept}`}>
                  <td>{pair.copy}</td>
                  <td>
                    <strong>{pair.kept}</strong>
                  </td>
                  <td className="num">{pair.copies.toLocaleString("en-GB")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export async function Jobs({ span }: { span: string }) {
  const jobs = await jobStates(spanFrom(span));
  if (jobs.length === 0) {
    return <p className="hint">No job has run in this range.</p>;
  }

  return (
    <div className="tiles">
      {jobs.map((job) => {
        const state =
          job.runs === 0 ? "idle" : JOB_TONE[job.last_status ?? ""] ?? "bad";
        return (
          <div key={job.job} className={`card job job-${state}`}>
            <div className="job-name">
              {job.job}
              <span className={`pill pill-${state}`}>
                {JOB_WORD[state] ?? (job.last_status ?? "unknown")}
              </span>
              {job.job in JOB_WHY && <Why text={JOB_WHY[job.job] as string} />}
            </div>
            <div className="metric-note">
              {state === "run"
                ? `started ${ago(job.last_at)}`
                : `last run ${ago(job.last_at)}`}
            </div>

            <div className="job-numbers">
              <span>
                <b className={job.ok > 0 ? "good" : undefined}>{job.ok}</b>
                <span>successful</span>
              </span>
              <span>
                <b className={job.bad > 0 ? "bad" : undefined}>{job.bad}</b>
                <span>failed</span>
              </span>
              <span>
                <b>{job.skipped}</b>
                <span>locked</span>
              </span>
              <span>
                <b>{job.median_secs === null ? "—" : `${job.median_secs}s`}</b>
                <span>median</span>
              </span>
            </div>

            {state !== "ok" && state !== "run" && job.last_error && (
              <div className="job-error">{job.last_error.slice(0, 300)}</div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export async function RunsChart({ span }: { span: string }) {
  const win = spanFrom(span);
  return <RunBars data={await runPoints(win, bucketMinutes(win.hours))} />;
}

export async function MessagesRead({ span }: { span: string }) {
  const win = spanFrom(span);
  return <Series data={await messagePoints(win, bucketMinutes(win.hours))} />;
}

export async function ListingsCreated({ span }: { span: string }) {
  const win = spanFrom(span);
  return <Series data={await intakePoints(win, bucketMinutes(win.hours))} />;
}

export async function QueueTiles({ span }: { span: string }) {
  const win = spanFrom(span);
  const bucket = bucketMinutes(win.hours);

  const [queue, read, unread, errorLog] = await Promise.all([
    delivery().catch(() => null),
    messagePoints(win, bucket),
    unparseablePoints(win, bucket),
    // Counted over the whole range rather than over the page of log lines
    // below: "errors on this page" changed as you paged, which made it a
    // number about the page rather than about the system.
    logPage(win, { level: "error" }, 1, 1),
  ]);

  const messages = read.reduce((sum, d) => sum + d.value, 0);
  const missed = unread.reduce((sum, d) => sum + d.value, 0);
  const badShare = messages > 0 ? Math.round((missed / messages) * 100) : 0;
  const errors = errorLog.total;

  return (
    <div className="tiles">
      <Metric
        label="Unparseable"
        value={`${badShare}%`}
        tone={badShare > 20 ? "bad" : badShare > 5 ? "warn" : "good"}
        note={`${missed} of ${messages} messages · ${spanWords(win)}`}
        why="Доля сообщений, которые парсер не смог прочитать. Выше 20% — источник почти наверняка сменил формат. Ноль при нулевом трафике ничего не значит."
      />
      <Metric
        label="Oldest queued"
        value={
          queue?.oldest_queued_mins === null || queue === null
            ? "—"
            : `${queue.oldest_queued_mins}m`
        }
        tone={(queue?.oldest_queued_mins ?? 0) > 10 ? "bad" : "good"}
        note="a queue that stops moving looks like a small one"
        why="Возраст самого старого сообщения в очереди. Число сообщений обманывает: если доставка встала, очередь выглядит маленькой, потому что в неё ничего не добавляется."
      />
      <Metric
        label="Median latency"
        value={
          queue?.median_latency_secs === null || queue === null
            ? "—"
            : `${queue.median_latency_secs}s`
        }
        // Under a minute is the promise this service makes; over five and
        // "instant alerts" is no longer true.
        tone={
          queue?.median_latency_secs == null
            ? undefined
            : queue.median_latency_secs > 300
              ? "bad"
              : queue.median_latency_secs > 60
                ? "warn"
                : "good"
        }
        note="queued to delivered, 24h"
        why="Медианное время от попадания в очередь до отправки. До минуты — норма. Больше пяти минут — обещание мгновенных уведомлений перестаёт быть правдой, и смотреть надо на джобу drain."
      />
      <Metric
        label="Errors logged"
        value={errors}
        tone={errors > 0 ? "bad" : "good"}
        note={spanWords(win)}
        why="Сколько строк уровня error задачи написали за выбранный период. Считается по всему периоду, а не по открытой странице лога ниже."
      />
    </div>
  );
}

export async function LastRuns() {
  const runs = await recentRuns(12).catch(() => []);
  if (runs.length === 0) return <p className="hint">No run has been recorded.</p>;

  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>Started</th>
            <th>Job</th>
            <th>Trigger</th>
            <th>Result</th>
            <th className="num">Secs</th>
            <th>What it did</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const shade = JOB_TONE[run.status] ?? "bad";
            return (
              <tr key={run.id}>
                <td className="mono">{at(run.started_at)}</td>
                <td>{run.job}</td>
                <td>{run.trigger}</td>
                <td
                  className={
                    shade === "bad" ? "bad" : shade === "warn" ? "warn"
                    : shade === "ok" ? "good" : undefined
                  }
                >
                  {run.status}
                </td>
                <td className="num">{run.seconds ?? "—"}</td>
                <td className="mono">
                  {run.error
                    ? run.error.slice(0, 120)
                    : Object.entries(run.counters ?? {})
                        .slice(0, 4)
                        .map(([name, value]) => `${name} ${value}`)
                        .join(" · ") || "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export async function LogLines({
  span,
  job,
  level,
  page,
}: {
  span: string;
  job?: string;
  level?: string;
  page: number;
}) {
  const win = spanFrom(span);
  const filter = { job, level };
  const [logs, jobNames] = await Promise.all([
    logPage(win, filter, page),
    knownJobs().catch(() => []),
  ]);

  const totalPages = Math.max(1, Math.ceil(logs.total / 10));
  const link = (over: Record<string, string | undefined>) => {
    const next = new URLSearchParams();
    for (const [name, value] of Object.entries({
      w: win.key, job, level, p: String(page), ...over,
    })) {
      if (value && value !== "1") next.set(name, value);
      else if (name === "w" && value) next.set(name, value);
    }
    return `/admin?${next.toString()}`;
  };

  return (
    <>
      <div className="dash-head" style={{ marginBottom: "0.6rem" }}>
        <div className="window-picker">
          <Link className={!job ? "win win-on" : "win"} href={link({ job: undefined, p: "1" })}>
            all jobs
          </Link>
          {jobNames.map((name) => (
            <Link
              key={name}
              className={job === name ? "win win-on" : "win"}
              href={link({ job: name, p: "1" })}
            >
              {name}
            </Link>
          ))}
        </div>
        <div className="window-picker">
          {["error", "warn", "info"].map((one) => (
            <Link
              key={one}
              className={level === one ? "win win-on" : "win"}
              href={link({ level: level === one ? undefined : one, p: "1" })}
            >
              {one}
            </Link>
          ))}
        </div>
      </div>

      {logs.rows.length === 0 ? (
        <p className="metric-note">Nothing logged in this range.</p>
      ) : (
        logs.rows.map((row) => (
          <div key={row.id} className="log-line mono">
            <span className="log-when">{at(row.ts)}</span>
            <span className={`log-level ${LEVEL_TONE[row.level] ?? ""}`}>
              {row.job}
            </span>
            <span className="log-message">
              <span className={LEVEL_TONE[row.level] ?? ""}>{row.level}</span>{" "}
              {row.stage ? `${row.stage} · ` : ""}
              {row.message}
            </span>
          </div>
        ))
      )}

      <div className="pager">
        <Link className={page <= 1 ? "off" : ""} href={link({ p: String(page - 1) })}>
          ← newer
        </Link>
        <span>
          page {page} of {totalPages} · {logs.total} lines
        </span>
        <Link
          className={page >= totalPages ? "off" : ""}
          href={link({ p: String(page + 1) })}
        >
          older →
        </Link>
      </div>
    </>
  );
}
