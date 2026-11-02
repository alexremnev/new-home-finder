import type { HostPoint } from "@/lib/admin-queries";
import { hostHealth } from "@/lib/admin-queries";
import { ago } from "@/lib/when";

import { hostPointsOnce, windowFor } from "../cached";
import { Gauge, Why } from "../charts";
import { peakOf } from "../levels";
import type { Span } from "../span";
import { bucketMinutes, bucketWords, spanWords } from "../span";
import {
  CPU_GUIDE,
  cpuReadings,
  diskTone,
  gb,
  loadTone,
  measured,
  memoryTone,
  MEMORY_FLOOR,
  mixedHosts,
  pct,
  ramGuide,
  ramReadings,
  word,
} from "./health";

// The box itself: processors, memory, disk.
//
// ── why a tab and not a line on System ───────────────────────────────────
//
// The System tab answers "did the jobs work". This answers "could they have" —
// a different question with different evidence. So the figure that matters is
// not an average but a high-water mark against a ceiling: a box that ran out
// of memory at 14:00 and recovered looks healthy in its latest sample, and the
// latest sample is the one a tile would otherwise print.
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
            <Why text="MemAvailable, а не MemFree — это оценка самого ядра, сколько новый процесс сможет получить, не уходя в своп. На этом боксе free показывает около 400 МБ при 3.1 ГБ available, потому что разница это переиспользуемый страничный кэш; красить по free значит держать плитку вечно красной, а вечно красную плитку перестают читать. Порог — 2 ГБ свободного запаса, половина бокса (MEMORY_FLOOR в health.ts): жёлтым ниже него, красным ниже половины от него. Оговорка, которую стоит знать: это число досталось в наследство от MemoryMax=2G снесённого браузерного слайса, то есть раньше оно отвечало на конкретный вопрос «поместится ли Chromium», а теперь это просто запас и его стоит перевывести из того, сколько на самом деле занимают скраперы. «low» — минимум за период, и именно он важнее текущего значения: бокс, которому не хватило памяти в 14:00 и который успел отдышаться, в последнем снимке выглядит здоровым." />
          </div>
          <div className="metric-note">
            {gb(host.mem_total)} GB total · {gb(MEMORY_FLOOR)} GB wanted spare
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
            <Why text="Свободное место на файловой системе, где лежит состояние воркера: там живут куки-джары. В процентах, а не в байтах, потому что весь диск — 40 ГБ, и доля осталось понятнее абсолютного числа. Жёлтый ниже 20%, красный ниже 10%. Расти здесь будут джары (по одному на связку выход+отпечаток, 501 возможный выход) и логи journald, и первое ограничено JAR_FOR, который чистит джары старше суток на каждом старте." />
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

// ── the same samples as a timeline ───────────────────────────────────────
//
// The tiles above answer "was the box short of anything over this window". The
// question you have the moment that answer is yes is "when", and a peak cannot
// give it: 1.9 on two processors is one fault if it was a single spike at
// 03:00 where two sweeps collided, and another entirely if it was a plateau
// from noon.
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
            The dashed line is where what is left stops covering the{" "}
            {gb(MEMORY_FLOOR)} GB this box is meant to keep spare.
          </>
        )}
      </p>
      <Mixed points={points} />
    </>
  );
}
