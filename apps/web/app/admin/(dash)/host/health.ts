import type { BrowserUse, HostHealth, HostPoint } from "@/lib/admin-queries";

import type { Reading } from "../levels";

// Where every red and green on the Host tab is decided. Apart from the page
// for the same reason `../proxy/spend.ts` is: none of it needs React or a
// database, and a threshold that is wrong does not *look* wrong — so it is the
// part under test.
//
// ── the thresholds are this box, not a general rule ──────────────────────
//
// 2 vCPU, 4 GB, 40 GB, running nothing but the worker: Postgres is in Supabase
// and the web app is on Vercel. Every number below is derived from that and
// from the one deliberate limit, `BROWSER_CAP` — so moving the browser's
// MemoryMax means changing it here too, and the tests say so.

export type Tone = "ok" | "warn" | "bad" | "idle";

/**
 * The hard ceiling on the browser slice, in bytes.
 *
 * 2 GB, which is a decision rather than a measurement: a single Chromium page
 * load peaks around 400-500 MB, so this is roughly four times what it needs
 * and is set as a safety net, not as a working limit. It is the divisor for
 * two judgments below — how close a run came to being killed, and whether the
 * box currently has room to start one at all — so it must stay equal to
 * `MemoryMax=` in the slice unit. See deploy/systemd.
 */
export const BROWSER_CAP = 2 * 1024 * 1024 * 1024;

/** Bytes as GB with one decimal, or an em dash for "not measured". */
export function gb(bytes: number | string | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const value = Number(bytes) / 1024 / 1024 / 1024;
  if (!Number.isFinite(value)) return "—";
  if (value > 0 && value < 0.05) return "<0.1";
  return value.toFixed(1);
}

/** Bytes as MB, whole, for the browser's own figure. */
export function mb(bytes: number | string | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const value = Number(bytes) / 1024 / 1024;
  return Number.isFinite(value) ? Math.round(value).toString() : "—";
}

/** A ratio as a whole percentage, or an em dash. */
export function pct(part: number | null, whole: number | null): string {
  if (part === null || whole === null || !whole) return "—";
  return `${Math.round((part / whole) * 100)}%`;
}

/**
 * Load against the processors it is spread over.
 *
 * A load average is not a percentage, and treating it as one is the usual way
 * this tile lies: 1.4 is half-idle on 8 cores and oversubscribed on 2. So it
 * is divided, and the bands are about the queue rather than about busyness —
 * past 1.0 per core there is more work ready to run than there are processors
 * to run it, which on a box whose whole job is to finish a six-minute scrape
 * inside its timer is the thing worth colouring.
 */
export function loadTone(load1: number | null, cpus: number | null): Tone {
  if (load1 === null || !cpus) return "idle";
  const each = load1 / cpus;
  if (each >= 1) return "bad";
  if (each >= 0.7) return "warn";
  return "ok";
}

/**
 * Whether there is room to start the browser.
 *
 * Tied to `BROWSER_CAP` rather than to a round number, because that is the
 * question actually being asked: if available memory is under the cap, the
 * slice can no longer be given what it is allowed to take, and a mint may be
 * what pushes the box into the OOM killer. Under half the cap it will not even
 * fit the browser's measured working set.
 *
 * `MemAvailable` is the input, not `MemFree` — see `worker.obs.host`. On this
 * box free reads about 400 MB while available reads 3.1 GB, because the rest
 * is reclaimable page cache, and colouring on free would mean a tile that is
 * permanently red and therefore permanently ignored.
 */
export function memoryTone(available: number | null): Tone {
  if (available === null) return "idle";
  if (available < BROWSER_CAP / 2) return "bad";
  if (available < BROWSER_CAP) return "warn";
  return "ok";
}

/** Disk, on the proportion left rather than the bytes: 40 GB is the whole of it. */
export function diskTone(free: number | null, total: number | null): Tone {
  if (free === null || !total) return "idle";
  const left = free / total;
  if (left < 0.1) return "bad";
  if (left < 0.2) return "warn";
  return "ok";
}

/**
 * How close the browser came to its ceiling.
 *
 * Red at 80% of the cap rather than at the cap itself: a process killed by
 * `MemoryMax` leaves no sample to colour, so the only useful warning is the
 * one given before it happens.
 */
export function browserTone(peak: number | null): Tone {
  if (peak === null) return "idle";
  if (peak > BROWSER_CAP * 0.8) return "bad";
  if (peak > BROWSER_CAP / 2) return "warn";
  return "ok";
}

/**
 * Whether the browser is buying anything with the memory it spends.
 *
 * The failure this exists to make visible: a mint that launches, burns half a
 * gigabyte, and comes away without a token is strictly worse than not running
 * — it costs the resources *and* leaves `zoopla_london` to be challenged. In a
 * tile that only showed "the browser ran 68 times" it is invisible.
 */
export function mintTone(use: BrowserUse): Tone {
  if (use.runs === 0) return "idle";
  if (use.failed > 0) return "bad";
  const rate = use.minted / use.runs;
  if (rate < 0.5) return "bad";
  if (rate < 0.9) return "warn";
  return "ok";
}

/** The pill beside each tile: what its colour means, in a word or two. */
export function word(tone: Tone, kind: "load" | "memory" | "disk" | "browser" | "mint"): string {
  if (tone === "idle") return "not measured";
  if (kind === "mint") {
    return tone === "ok" ? "earning it" : tone === "warn" ? "missing some" : "spending for nothing";
  }
  if (kind === "browser") {
    return tone === "ok" ? "well under" : tone === "warn" ? "half the cap" : "near the cap";
  }
  if (tone === "ok") return "room to spare";
  return tone === "warn" ? "tight" : "out of room";
}

/**
 * The worst of several, for the banner at the top.
 *
 * `idle` is not a severity and must not win: a box where the disk could not be
 * read and memory is nearly gone is not "not measured", it is out of room.
 */
export function worst(tones: Tone[]): Tone {
  const rank: Record<Tone, number> = { idle: 0, ok: 1, warn: 2, bad: 3 };
  return tones.reduce((bad, one) => (rank[one] > rank[bad] ? one : bad), "idle" as Tone);
}

/** Whether anything sampled at all, which decides between a tile and a hint. */
export function measured(host: HostHealth): boolean {
  return host.samples > 0;
}

// ── the two charts ───────────────────────────────────────────────────────
//
// The tiles above them print "now" and "the window's worst". These turn the
// same samples into a shape, and the shape is read for one thing: whether a
// line crossed. So both are plotted as a percentage of the thing that runs
// out — processors for load, total memory for RAM — rather than in their own
// units, which puts them on one axis with one meaning and gives the guide line
// something to be.
//
// Both rise when the box is in trouble. Memory is therefore plotted as *used*
// rather than as the available figure the tile prints, because a chart where
// one line falling and the other rising mean the same thing is a chart read
// wrong once and then not read again.
//
// A bucket no run sampled stays null, all the way from the SQL to the gap in
// the line. See `hostPoints`, and the module note above about what a zero
// would claim here.

/**
 * The red line on the CPU chart, as a percentage of capacity.
 *
 * 100 rather than the 70 that `loadTone` turns amber at: one guide, at the
 * point where the box is definitely over-subscribed. The amber band is a
 * warning to act on, which is the tile's job; the chart's job is to show when
 * the thing actually happened.
 */
export const CPU_GUIDE = 100;

/** Load per processor, as a percentage. Null where nothing sampled. */
export function cpuReadings(points: HostPoint[]): Reading[] {
  return points.map((one) => ({
    label: one.label,
    value:
      one.load1 === null || !one.cpus ? null : (one.load1 / one.cpus) * 100,
  }));
}

/** Memory in use, as a percentage of the box's total. */
export function ramReadings(points: HostPoint[]): Reading[] {
  return points.map((one) => ({
    label: one.label,
    value:
      one.mem_available === null || !one.mem_total
        ? null
        : ((one.mem_total - one.mem_available) / one.mem_total) * 100,
  }));
}

/**
 * Where the RAM chart's red line goes: the point at which available memory
 * falls below `BROWSER_CAP` and the browser slice can no longer be given what
 * it is allowed to take.
 *
 * Derived from the box rather than written down, because it is the same
 * judgment `memoryTone` makes and the two must not be able to disagree — on
 * this 4 GB box against a 2 GB cap it lands at 50% used. Null when the box did
 * not say how much memory it has, and null when the box is smaller than the
 * cap, where the line would sit at or below zero and mean nothing.
 */
export function ramGuide(points: HostPoint[]): number | null {
  const totals = points
    .map((one) => one.mem_total)
    .filter((one): one is number => one !== null && one > 0);
  if (!totals.length) return null;
  const total = Math.max(...totals);
  if (total <= BROWSER_CAP) return null;
  return ((total - BROWSER_CAP) / total) * 100;
}

/**
 * Whether any one bucket holds samples from more than one machine.
 *
 * The one way these two charts can print a number belonging to no machine: a
 * bucket's worst load average comes from one row and its processor count from
 * another, and dividing them is meaningless the moment those rows are
 * different boxes. See `HostPoint.hosts` for why that cannot happen on this
 * setup and why it is counted anyway.
 */
export function mixedHosts(points: HostPoint[]): boolean {
  return points.some((one) => one.hosts > 1);
}
