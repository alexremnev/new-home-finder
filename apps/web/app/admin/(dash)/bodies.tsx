import Link from "next/link";

import {
  delivery, duplicates, intakePoints, jobStates, knownJobs, logPage,
  messagePoints, problems, recentRuns, runPoints, scrapeBytes, sourceFeeds,
  unparseablePoints,
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
  scrape:
    "Читает OpenRent прямо с сайта: фид его не публикует. По расписанию рабочего " +
    "дня: в будни с 7:20 до 17 каждые 20 минут, с 17 до 22 раз в час, ночью " +
    "не запускается; в выходные с 10 до 20 раз в час. Карта сайта у них без " +
    "lastmod и без ETag, поэтому каждый прогон качает её целиком — реже " +
    "спрашивать это единственный способ платить меньше. " +
    "Если молчит — OpenRent перестанет приходить, остальные источники не пострадают.",
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
