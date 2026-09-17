import Link from "next/link";
import {
  EXPECTED_JOBS, delivery, intakePoints, jobStates, knownJobs, logPage,
  messagePoints, problems, recentRuns, runPoints, unparseablePoints,
} from "@/lib/admin-queries";

import { Metric, RunBars, Series, Why } from "./charts";
import { DEFAULT_WINDOW, WindowPicker, bucketMinutes, windowFrom } from "./window";

import { ago, at } from "@/lib/when";

export const dynamic = "force-dynamic";

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
  const [jobs, points, logs, jobNames, queue, read, made, unread, faults, runs] =
    await Promise.all([
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
  ]);

  const messages = read.reduce((sum, d) => sum + d.value, 0);
  const missed = unread.reduce((sum, d) => sum + d.value, 0);
  const badShare = messages > 0 ? Math.round((missed / messages) * 100) : 0;

  // A job counts as broken when its most recent run was not clean, and as
  // silent when it has not run at all in the window. Both are degraded; the
  // wording tells them apart, because the fixes differ.
  const broken = jobs.filter((job) => job.last_status && job.last_status !== "ok"
    && job.last_status !== "skipped_locked");
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
        <div className="card" style={{ marginBottom: "0.75rem" }}>
          <h2>
            Problems
            <Why text="Проверки, которые смотрят дальше кода возврата задачи: встала ли очередь, есть ли куда отправлять, не сменил ли источник формат. Задача может завершиться успешно и при этом ничего не сделать." />
          </h2>
          {faults.map((fault) => (
            <div key={fault.kind + fault.detail} className="log-line">
              <span className="log-level bad">{fault.kind}</span>
              <span className="log-message" style={{ gridColumn: "span 2" }}>
                {fault.detail}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="dash-head">
        <h1>System</h1>
        <WindowPicker here="/admin" chosen={win.key} extra={filter} />
      </div>

      <div className="dash-row">
        {jobs.map((job) => {
          const state =
            job.runs === 0 ? "idle" : job.last_status === "ok" ? "ok" : "bad";
          return (
            <div key={job.job} className={`card job job-${state}`}>
              <div className="job-name">
                {job.job}
                <span className={`pill pill-${state}`}>
                  {state === "ok" ? "ok" : state === "bad" ? (job.last_status ?? "bad") : "silent"}
                </span>
                {job.job in JOB_WHY && <Why text={JOB_WHY[job.job] as string} />}
              </div>
              <div className="metric-note">last run {ago(job.last_at)}</div>

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

              {job.last_status !== "ok" && job.last_error && (
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
                const bad = run.status === "failed" || run.status === "degraded";
                return (
                  <tr key={run.id}>
                    <td className="mono">{at(run.started_at)}</td>
                    <td>{run.job}</td>
                    <td>{run.trigger}</td>
                    <td className={bad ? "bad" : run.status === "ok" ? "good" : undefined}>
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
