import type { HostPoint } from "@/lib/admin-queries";
import { browserUse, hostHealth } from "@/lib/admin-queries";
import { ago } from "@/lib/when";

import { hostPointsOnce, windowFor } from "../cached";
import { Gauge, Why } from "../charts";
import { peakOf } from "../levels";
import type { Span } from "../span";
import { bucketMinutes, bucketWords, spanWords } from "../span";
import {
  BROWSER_CAP,
  browserTone,
  CPU_GUIDE,
  cpuReadings,
  diskTone,
  gb,
  loadTone,
  mb,
  measured,
  memoryTone,
  mintTone,
  mixedHosts,
  pct,
  ramGuide,
  ramReadings,
  word,
  worst,
} from "./health";

// The box itself: processors, memory, disk, and what the headless browser
// takes out of them.
//
// ── why a tab and not a line on System ───────────────────────────────────
//
// The System tab answers "did the jobs work". This answers "could they have" —
// a different question with different evidence, and one that only started
// mattering when a browser was added to a 4 GB machine. Chromium is the first
// thing this worker runs that can plausibly exhaust the box, so the figure
// that matters is not an average but a high-water mark against a ceiling, and
// it is read beside the mint rate because memory spent without a token is the
// failure worth catching.
//
// Every judgment of colour is in ./health.ts.

export async function HostTiles({ span }: { span: string }) {
  const win = windowFor(span);
  const host = await hostHealth(win);

  const load = loadTone(host.load1, host.cpus);
  const memory = memoryTone(host.mem_available);
  const disk = diskTone(host.disk_free, host.disk_total);

  if (!measured(host)) {
    return (
      <p className="hint">
        No run sampled the machine {spanWords(win)}. Every run writes one sample
        when it finishes, so an empty panel here means either that nothing ran
        or that the worker predates the sampling — not that the box is idle.
      </p>
    );
  }

  return (
    <>
      <div className="tiles tiles-roomy">
        <div className={`card job job-${load}`}>
          <div className="job-name">
            CPU
            <span className={`pill pill-${load}`}>{word(load, "load")}</span>
            <Why text="Одноминутный load average, делённый на число процессоров, которые воркеру разрешено занимать. Делённый — потому что load average это не проценты: 1.4 на восьми ядрах — это почти простой, а на двух — очередь. Жёлтый с 0.7 на ядро, красный с 1.0, то есть когда работы, готовой выполняться, больше, чем процессоров. Снимается один раз за прогон, в момент его завершения: на oneshot-юните, который обязан уложиться в свой таймер, засыпать ради второго замера смысла нет. Поэтому «пик» рядом — это худший из снимков за период, а не истинный максимум; всплеск между двумя прогонами сюда не попадёт." />
          </div>
          <div className="metric-note">
            {host.cpus ?? "—"} vCPU · sampled {ago(host.at)}
          </div>
          <div className="job-numbers">
            <span>
              <b className={load === "ok" ? undefined : "warn"}>
                {host.load1 === null ? "—" : host.load1.toFixed(2)}
              </b>
              <span>load now</span>
            </span>
            <span>
              <b>{host.load1_peak === null ? "—" : host.load1_peak.toFixed(2)}</b>
              <span>peak {spanWords(win)}</span>
            </span>
            <span>
              <b>{pct(host.load1, host.cpus)}</b>
              <span>of capacity</span>
            </span>
          </div>
        </div>

        <div className={`card job job-${memory}`}>
          <div className="job-name">
            RAM
            <span className={`pill pill-${memory}`}>{word(memory, "memory")}</span>
            <Why text="MemAvailable, а не MemFree — это оценка самого ядра, сколько новый процесс сможет получить, не уходя в своп. На этом боксе free показывает около 400 МБ при 3.1 ГБ available, потому что разница это переиспользуемый страничный кэш; красить по free значит держать плитку вечно красной, а вечно красную плитку перестают читать. Пороги привязаны не к круглому числу, а к потолку браузерного слайса (2 ГБ): если available упал ниже потолка, слайсу уже нельзя выдать то, что ему разрешено, и очередной запуск браузера может столкнуть бокс в OOM-killer; ниже половины потолка браузер не поместится даже по измеренному рабочему набору. «low» — минимум за период, и именно он важнее текущего значения: бокс, которому не хватило памяти в 14:00 и который успел отдышаться, в последнем снимке выглядит здоровым." />
          </div>
          <div className="metric-note">
            {gb(host.mem_total)} GB total · cap for the browser {gb(BROWSER_CAP)} GB
          </div>
          <div className="job-numbers">
            <span>
              <b className={memory === "ok" ? undefined : "warn"}>{gb(host.mem_available)}</b>
              <span>GB available</span>
            </span>
            <span>
              <b>{gb(host.mem_low)}</b>
              <span>GB low</span>
            </span>
            <span>
              <b>{pct(host.mem_available, host.mem_total)}</b>
              <span>of total</span>
            </span>
          </div>
        </div>

        <div className={`card job job-${disk}`}>
          <div className="job-name">
            Disk
            <span className={`pill pill-${disk}`}>{word(disk, "disk")}</span>
            <Why text="Свободное место на файловой системе, где лежит состояние воркера: там живут куки-джары и профиль браузера. В процентах, а не в байтах, потому что весь диск — 40 ГБ, и доля осталось понятнее абсолютного числа. Жёлтый ниже 20%, красный ниже 10%. Сам Chromium от Playwright занимает около 450 МБ плюс системные библиотеки — это единоразово и на фоне 40 ГБ незаметно; расти здесь будут джары (по одному на связку выход+отпечаток, 501 возможный выход) и логи journald, и первое ограничено JAR_FOR, который чистит джары старше суток на каждом старте." />
          </div>
          <div className="metric-note">{gb(host.disk_total)} GB total</div>
          <div className="job-numbers">
            <span>
              <b className={disk === "ok" ? undefined : "warn"}>{gb(host.disk_free)}</b>
              <span>GB free</span>
            </span>
            <span>
              <b>{pct(host.disk_free, host.disk_total)}</b>
              <span>left</span>
            </span>
          </div>
        </div>
      </div>

      <p className="hint">
        {host.samples} sample{host.samples === 1 ? "" : "s"} {spanWords(win)}, one per
        finished run. A spike between two runs does not appear here.
      </p>
    </>
  );
}

export async function BrowserCost({ span }: { span: string }) {
  const win = windowFor(span);
  const use = await browserUse(win);

  const size = browserTone(use.peak_bytes);
  const mint = mintTone(use);
  const mood = worst([size, mint]);

  if (use.runs === 0) {
    return (
      <p className="hint">
        The browser did not run {spanWords(win)}. Its timer covers weekday
        working hours only, so a quiet night or weekend is the expected reading
        rather than a fault.
      </p>
    );
  }

  return (
    <>
      <div className="tiles tiles-roomy">
        <div className={`card job job-${size}`}>
          <div className="job-name">
            Browser memory
            <span className={`pill pill-${size}`}>{word(size, "browser")}</span>
            <Why text="Пиковый RSS самого крупного дочернего процесса прогона, в мегабайтах — то есть Chromium, потому что на этой задаче он единственный ребёнок. Берётся из getrusage(RUSAGE_CHILDREN).ru_maxrss после закрытия браузера, поэтому не нужен ни psutil, ни агент. Важная оговорка: ru_maxrss по детям это максимум одного процесса, а не сумма всех, — на минтере это честное число, на задаче с несколькими детьми оно занижало бы, и поэтому больше никто его не вызывает. Красным с 80% потолка, а не на самом потолке: процесс, убитый MemoryMax, уже не оставит снимка, который можно покрасить, так что единственное полезное предупреждение — то, что дано заранее." />
          </div>
          <div className="metric-note">
            {use.runs} launch{use.runs === 1 ? "" : "es"} · last {ago(use.last_at)}
          </div>
          <div className="job-numbers">
            <span>
              <b className={size === "ok" ? undefined : "warn"}>{mb(use.peak_bytes)}</b>
              <span>MB peak</span>
            </span>
            <span>
              <b>{pct(use.peak_bytes, BROWSER_CAP)}</b>
              <span>of cap</span>
            </span>
            <span>
              <b>{use.avg_seconds === null ? "—" : use.avg_seconds.toFixed(1)}</b>
              <span>s each</span>
            </span>
          </div>
        </div>

        <div className={`card job job-${mint}`}>
          <div className="job-name">
            Clearance minted
            <span className={`pill pill-${mint}`}>{word(mint, "mint")}</span>
            <Why text="Сколько запусков действительно унесли токен. Это та половина картины, которой нет в «браузер запускался 68 раз»: запуск, который поднял Chromium, сжёг полгигабайта и ушёл ни с чем, строго хуже, чем незапуск — он стоил ресурсов и оставил zoopla_london под челленджем. Красным, если доля меньше половины или хоть один прогон упал с failed; жёлтым ниже 90%. Отдельные неудачи нормальны: Cloudflare иногда отдаёт челлендж, который не проходит и настоящий браузер. Устойчиво низкая доля означает одно из двух — либо выход попал в чёрный список настолько, что браузеру тоже не открывают, либо отпечаток Chromium разошёлся с тем, что имперсонирует curl_cffi, и токен выписывается не на того." />
          </div>
          <div className="metric-note">
            {use.total_seconds === null
              ? "—"
              : `${Math.round(use.total_seconds)}s of browser time`}{" "}
            {spanWords(win)}
          </div>
          <div className="job-numbers">
            <span>
              <b className={mint === "ok" ? undefined : "warn"}>{use.minted}</b>
              <span>minted</span>
            </span>
            <span>
              <b>{use.runs}</b>
              <span>launches</span>
            </span>
            <span>
              <b>{pct(use.minted, use.runs)}</b>
              <span>hit rate</span>
            </span>
            {use.degraded > 0 && (
              <span>
                <b className="warn">{use.degraded}</b>
                <span>degraded</span>
              </span>
            )}
            {use.failed > 0 && (
              <span>
                <b className="warn">{use.failed}</b>
                <span>failed</span>
              </span>
            )}
          </div>
        </div>
      </div>

      {mood === "bad" && (
        <p className="hint">
          Either the browser is close to its ceiling or it is spending memory
          without coming away with a token. Both are reasons to look at the
          clearance job before the next working day, because `zoopla_london`
          falls back to being challenged without it.
        </p>
      )}
    </>
  );
}

// ── the same samples as a timeline ───────────────────────────────────────
//
// The tiles above answer "was the box short of anything over this window". The
// question you have the moment that answer is yes is "when", and a peak cannot
// give it: 1.9 on two processors is one fault if it was a single spike at
// 03:00 where a sweep collided with the clearance timer, and another entirely
// if it was a plateau from noon.
//
// Two charts rather than one with two lines. They share an axis by
// construction — both are a percentage of the thing that runs out — but they
// run out for unrelated reasons, and a reader who has to pick a line out of a
// pair before reading it reads neither.
//
// Both are drawn from the run log, at one sample per finished run, so the
// resolution caveat on the panel above applies here too and is worth repeating
// under each: this is a line through a handful of points per bucket, not a
// trace.

/** The hint that stands in for a chart with nothing to draw. */
function NotSampled({ win }: { win: Span }) {
  return (
    <p className="hint">
      No run sampled the machine {spanWords(win)}, so there is nothing to draw.
      Samples are written by finished runs, not by an agent — see the panel
      above.
    </p>
  );
}

/**
 * Said when a bucket holds more than one machine, which makes the division
 * behind both charts meaningless.
 *
 * Not expected to ever show: see `HostPoint.hosts`. It is here because the
 * alternative to saying it is printing a load percentage computed from one
 * box's queue and another box's processors, which looks like an ordinary
 * reading and is not one.
 */
function Mixed({ points }: { points: HostPoint[] }) {
  if (!mixedHosts(points)) return null;
  return (
    <p className="hint">
      Some bucket here holds runs from more than one machine, so a percentage in
      it may divide one box's figure by another's. Both charts assume the run
      log describes a single host, which is what job_runs.host records.
    </p>
  );
}

export async function CpuChart({ span }: { span: string }) {
  const win = windowFor(span);
  const bucket = bucketMinutes(win.hours);
  const points = await hostPointsOnce(win, bucket);
  const data = cpuReadings(points);

  if (peakOf(data) === null) return <NotSampled win={win} />;

  return (
    <>
      <p className="panel-note">{bucketWords(bucket)} · worst sample in each</p>
      <Gauge data={data} guide={{ at: CPU_GUIDE, name: "one per core" }} />
      <p className="hint">
        Load average divided by the processors it is spread over, so 100% is one
        runnable process per vCPU rather than a busy box. The dashed line is
        there: above it more work is ready to run than there is anything to run
        it on, which on a scrape that has to finish inside its timer is the
        thing that turns into a late alert. Gaps are buckets no run fell in.
      </p>
      <Mixed points={points} />
    </>
  );
}

export async function RamChart({ span }: { span: string }) {
  const win = windowFor(span);
  const bucket = bucketMinutes(win.hours);
  const points = await hostPointsOnce(win, bucket);
  const data = ramReadings(points);
  const guide = ramGuide(points);

  if (peakOf(data) === null) return <NotSampled win={win} />;

  return (
    <>
      <p className="panel-note">{bucketWords(bucket)} · worst sample in each</p>
      <Gauge
        data={data}
        guide={guide === null ? null : { at: guide, name: "the browser's cap" }}
      />
      <p className="hint">
        Memory in use, as a share of the {gb(points.find((one) => one.mem_total)?.mem_total)} GB
        the box has — the complement of the MemAvailable figure the tile prints,
        drawn this way up so that both charts rise when the box is in trouble.
        {guide !== null && (
          <>
            {" "}
            The dashed line is where what is left stops covering{" "}
            {gb(BROWSER_CAP)} GB, the ceiling on the browser slice: above it a
            mint can no longer be given what it is allowed to take.
          </>
        )}
      </p>
      <Mixed points={points} />
    </>
  );
}
