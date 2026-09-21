import Link from "next/link";
import {
  EXPECTED_JOBS, delivery, intakePoints, jobStates, knownJobs, logPage,
  messagePoints, problems, recentRuns, runPoints, sourceFeeds, unparseablePoints,
} from "@/lib/admin-queries";

import { Metric, RunBars, Series, Why } from "./charts";
import { DEFAULT_WINDOW, WindowPicker, bucketMinutes, windowFrom } from "./window";

import { ago, at } from "@/lib/when";

export const dynamic = "force-dynamic";

// Named and ordered here, not taken from whatever the database happens to
// return: a source that has never produced anything still needs a panel, and a
// red one is the whole point.
//
// `portal` is which site the listing is from; `feed` is how it reached us — a
// listing came from the Telegram feed exactly when a source message points at
// it, and from a scraper when none does.
const FEEDS: { label: string; portal: string; feed: boolean; why: string }[] = [
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
    why: "OpenRent из собственного скрапера — объявления, за которыми не стоит ни одно сообщение фида. Это основной источник OpenRent; фид остаётся страховкой, и если он найдёт то же объявление, оно склеится в ту же строку.",
  },
];

// Ten is enough to see what is wrong. Uncapped, one repeating check buries
// every other kind — and the kinds are the information.
const FAULTS_SHOWN = 10;

function tone(day: number, hour: number): "good" | "warn" | "bad" {
  if (hour > 0) return "good";
  return day > 0 ? "warn" : "bad";
}

// Russian, as asked: the names being explained are English and whoever reads
// this did not write them.
const JOB_WHY: Record<string, string> = {
  ingest:
    "Читает новые сообщения из Telegram-фида, разбирает их в объявления и ставит " +
    "совпадения в очередь. Запускается каждые 5 минут. Если молчит — новых " +
    "объявлений не появится вообще.",
  drain:
    "Отправляет то, что стоит в очереди: алерты, стартовую пятёрку новым " +
    "подписчикам, уведомления об окончании плана и вечерний дайджест. Каждую " +
    "минуту. Если молчит — объявления есть, но до людей не доходят.",
  rollup:
    "Пересчитывает статистику за последние 3 дня в таблицу daily_stats, чтобы " +
    "графики открывались мгновенно. Каждые 5 минут. Если молчит — цифры на " +
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



export default async function SystemPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const win = windowFrom(params.w ?? DEFAULT_WINDOW);
  const page = Math.max(1, Number(params.p ?? 1) || 1);
  const filter = {
    job: params.job || undefined,
    level: params.level || undefined,
    q: params.q?.slice(0, 80) || undefined,
  };

  const bucket = bucketMinutes(win.hours);
  const [
    jobs, points, logs, jobNames, queue, read, made, unread, faults, runs, feeds,
  ] = await Promise.all([
    jobStates(win.hours),
    runPoints(win.hours, bucket),
    logPage(win.hours, filter, page),
    knownJobs().catch(() => [...EXPECTED_JOBS]),
    delivery().catch(() => null),
    messagePoints(win.hours, bucket),
    intakePoints(win.hours, bucket),
    unparseablePoints(win.hours, bucket),
    problems().catch(() => []),
    recentRuns(12).catch(() => []),
    sourceFeeds(),
  ]);

  const messages = read.reduce((sum, d) => sum + d.value, 0);
  const missed = unread.reduce((sum, d) => sum + d.value, 0);
  const badShare = messages > 0 ? Math.round((missed / messages) * 100) : 0;

  // A job counts as broken when its most recent run was not clean, and as
  // silent when it has not run at all in the window. Both are degraded; the
  // wording tells them apart, because the fixes differ.
  // Running is neither broken nor silent; it is the healthy middle of a run.
  const broken = jobs.filter(
    (job) => job.last_status === "failed" || job.last_status === "degraded",
  );
  const silent = jobs.filter((job) => job.runs === 0);
  const errors = logs.rows.filter((row) => row.level === "error").length;
  const healthy = broken.length === 0 && silent.length === 0;

  const totalPages = Math.max(1, Math.ceil(logs.total / 10));
  const link = (over: Record<string, string | undefined>) => {
    const next = new URLSearchParams();
    for (const [name, value] of Object.entries({ w: win.key, ...filter, p: String(page), ...over })) {
      if (value && value !== "1") next.set(name, value);
      else if (name === "w" && value) next.set(name, value);
    }
    return `/admin?${next.toString()}`;
  };

  return (
    <>
      <div className={healthy ? "verdict verdict-ok" : "verdict verdict-bad"}>
        <span className="verdict-dot" />
        <span>{healthy ? "Healthy" : "Degraded"}</span>
        <span className="verdict-note">
          {healthy
            ? `all ${jobs.length} jobs ran clean · last ${win.label}`
            : [
                broken.length > 0 && `${broken.map((j) => j.job).join(", ")} failing`,
                silent.length > 0 && `${silent.map((j) => j.job).join(", ")} silent`,
              ]
                .filter(Boolean)
                .join(" · ")}
        </span>
      </div>

      {faults.length > 0 && (
        <details className="card faults" open>
          <summary>
            <span>
              Problems <strong>{faults.length}</strong>
            </span>
            <Why text="Проверки, которые смотрят дальше кода возврата задачи: встала ли очередь, есть ли куда отправлять, не сменил ли источник формат. Задача может завершиться успешно и при этом ничего не сделать. Периоды у проверок разные: ошибки задач — за 48 часов, неразобранные сообщения — за 7 дней, «ничего не прочитано» — порог 6 часов, «доставка встала» — очередь старше часа. Дата в строке — последний раз, когда это случилось." />
          </summary>

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
              {faults.length - FAULTS_SHOWN} more of the same kind, not shown.
              The list is capped so one noisy check cannot bury the rest.
            </p>
          )}
        </details>
      )}

      <div className="dash-head">
        <h1>System</h1>
        <WindowPicker here="/admin" chosen={win.key} extra={filter} />
      </div>

      <div className="dash-row">
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
              tone={tone(day, hour)}
              note={found?.newest ? `last ${ago(found.newest)}` : "nothing in 30 days"}
              why={one.why}
            />
          );
        })}
      </div>

      <div className="dash-row">
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
                  <span>clean</span>
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

      <div className="dash-row">
        <div className="card">
          <h2>
            Runs
            <Why text="Каждый столбик — окно времени. Зелёное: прогоны завершились чисто. Красное: упали или прошли с ошибками. Так видно разницу между «сломалось один раз» и «сломано весь день»." />
          </h2>
          <RunBars data={points} />
        </div>

      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>
            Messages read
            <Why text="Сообщения, прочитанные из фида. Ровно это делает ingest: если линия на нуле, а задача зелёная — значит в канале тихо, а не сломано." />
          </h2>
          <Series data={read} />
        </div>
        <div className="card">
          <h2>
            Listings created
            <Why text="Сколько из прочитанных сообщений стало объявлениями. Расхождение с предыдущим графиком — это дубли и то, что парсер не понял." />
          </h2>
          <Series data={made} />
        </div>
      </div>

      <div className="dash-row">
        <Metric
          label="Unparseable"
          value={`${badShare}%`}
          tone={badShare > 20 ? "bad" : badShare > 5 ? "warn" : "good"}
          note={`${missed} of ${messages} messages · last ${win.label}`}
          why="Доля сообщений, которые парсер не смог прочитать. Выше 20% — источник почти наверняка сменил формат. Ноль при нулевом трафике ничего не значит."
        />
        <Metric
          label="Oldest queued"
          value={queue?.oldest_queued_mins === null || queue === null
            ? "—" : `${queue.oldest_queued_mins}m`}
          tone={(queue?.oldest_queued_mins ?? 0) > 10 ? "bad" : "good"}
          note="a queue that stops moving looks like a small one"
          why="Возраст самого старого сообщения в очереди. Число сообщений обманывает: если доставка встала, очередь выглядит маленькой, потому что в неё ничего не добавляется."
        />
        <Metric
          label="Median latency"
          value={queue?.median_latency_secs === null || queue === null
            ? "—" : `${queue.median_latency_secs}s`}
          note="queued to delivered, 24h"
        />
        <Metric
          label="Errors logged"
          value={errors}
          tone={errors > 0 ? "bad" : "good"}
          note="on the page below"
        />
      </div>

      <div className="card" style={{ marginBottom: "0.75rem" }}>
        <h2>
          Last runs
          <Why text="Отдельные прогоны, свежие сверху. Trigger показывает, кто запустил: schedule — таймер, manual — вы руками. skipped_locked значит, что предыдущий прогон ещё шёл, и это норма, а не сбой." />
        </h2>
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
                const tone = JOB_TONE[run.status] ?? "bad";
                return (
                  <tr key={run.id}>
                    <td className="mono">{at(run.started_at)}</td>
                    <td>{run.job}</td>
                    <td>{run.trigger}</td>
                    <td
                      className={
                        tone === "bad" ? "bad" : tone === "warn" ? "warn"
                        : tone === "ok" ? "good" : undefined
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
      </div>

      <div className="card">
        <h2>
          Log
          <Why text="Строки, которые задачи пишут о себе. error — что-то сломалось, warn — сработала защита, info — обычный ход дела. По 10 на страницу." />
        </h2>

        <div className="dash-head" style={{ marginBottom: "0.6rem" }}>
          <div className="window-picker">
            <Link className={!filter.job ? "win win-on" : "win"} href={link({ job: undefined, p: "1" })}>
              all jobs
            </Link>
            {jobNames.map((name) => (
              <Link key={name}
                className={filter.job === name ? "win win-on" : "win"}
                href={link({ job: name, p: "1" })}
              >
                {name}
              </Link>
            ))}
          </div>
          <div className="window-picker">
            {["error", "warn", "info"].map((level) => (
              <Link key={level}
                className={filter.level === level ? "win win-on" : "win"}
                href={link({ level: filter.level === level ? undefined : level, p: "1" })}
              >
                {level}
              </Link>
            ))}
          </div>
        </div>

        {logs.rows.length === 0 ? (
          <p className="metric-note">Nothing logged in this window.</p>
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
          <Link className={page >= totalPages ? "off" : ""}
            href={link({ p: String(page + 1) })}
          >
            older →
          </Link>
        </div>
      </div>
    </>
  );
}
