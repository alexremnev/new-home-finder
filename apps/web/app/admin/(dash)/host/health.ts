import type { HostHealth, HostPoint } from "@/lib/admin-queries";

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
// from `MEMORY_FLOOR`, which is the one figure here that is a decision rather
// than a reading off the box — see its note.

export type Tone = "ok" | "warn" | "bad" | "idle";

/**
 * How much memory the box should have spare, in bytes.
 *
 * 2 GB — half of this box — and a decision rather than a measurement. It was
 * `MemoryMax=` on the headless browser's slice, which is what originally made
 * it answerable: under that much available, the slice could no longer be given
 * what it was allowed to take. The browser and its slice are gone, so the
 * number no longer has that anchor and is kept as a plain headroom floor:
 * below half the box there is no room for a burst from anything.
 *
 * Worth re-deriving from what the readers actually peak at rather than
 * inheriting. It is the divisor for `memoryTone` and for the RAM chart's guide
 * line, which is why both move together when it changes.
 */
export const MEMORY_FLOOR = 2 * 1024 * 1024 * 1024;

/** Bytes as GB with one decimal, or an em dash for "not measured". */
export function gb(bytes: number | string | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  const value = Number(bytes) / 1024 / 1024 / 1024;
  if (!Number.isFinite(value)) return "—";
  if (value > 0 && value < 0.05) return "<0.1";
  return value.toFixed(1);
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
 * Whether the box has room left to do anything.
 *
 * Tied to `MEMORY_FLOOR` rather than to a round number of its own, so that
 * this and the RAM chart's guide line cannot disagree about where "tight"
 * starts. Amber under the floor, red under half of it.
 *
 * `MemAvailable` is the input, not `MemFree` — see `worker.obs.host`. On this
 * box free reads about 400 MB while available reads 3.1 GB, because the rest
 * is reclaimable page cache, and colouring on free would mean a tile that is
 * permanently red and therefore permanently ignored.
 */
export function memoryTone(available: number | null): Tone {
  if (available === null) return "idle";
  if (available < MEMORY_FLOOR / 2) return "bad";
  if (available < MEMORY_FLOOR) return "warn";
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

/** The pill beside each tile: what its colour means, in a word or two. */
export function word(tone: Tone, kind: "load" | "memory" | "disk"): string {
  if (tone === "idle") return "not measured";
  if (tone === "ok") return "room to spare";
  return tone === "warn" ? "tight" : "out of room";
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
 * falls below `MEMORY_FLOOR`.
 *
 * Derived from the box rather than written down, because it is the same
 * judgment `memoryTone` makes and the two must not be able to disagree — on
 * this 4 GB box against a 2 GB floor it lands at 50% used. Null when the box
 * did not say how much memory it has, and null when the box is smaller than
 * the floor, where the line would sit at or below zero and mean nothing.
 */
export function ramGuide(points: HostPoint[]): number | null {
  const totals = points
    .map((one) => one.mem_total)
    .filter((one): one is number => one !== null && one > 0);
  if (!totals.length) return null;
  const total = Math.max(...totals);
  if (total <= MEMORY_FLOOR) return null;
  return ((total - MEMORY_FLOOR) / total) * 100;
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
