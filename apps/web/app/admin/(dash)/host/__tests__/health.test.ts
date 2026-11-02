import { describe, expect, it } from "vitest";

import type { BrowserUse, HostPoint } from "@/lib/admin-queries";

import {
  BROWSER_CAP,
  browserTone,
  cpuReadings,
  diskTone,
  gb,
  loadTone,
  mb,
  memoryTone,
  mintTone,
  mixedHosts,
  pct,
  ramGuide,
  ramReadings,
  word,
  worst,
} from "../health";

// The part of the Host tab that can be wrong without looking wrong.
//
// The figures themselves come straight out of the counters a run wrote, so
// there is nothing to get wrong in printing them. The colours are judgments
// about a specific 4 GB box, and two of them are the kind that pass a glance
// and fail in use: a load average read as a percentage, and memory read off
// MemFree instead of MemAvailable. Both would paint this box the wrong colour
// permanently, in opposite directions.

const use = (over: Partial<BrowserUse> = {}): BrowserUse => ({
  runs: 10,
  minted: 10,
  degraded: 0,
  failed: 0,
  peak_bytes: 450 * 1024 * 1024,
  avg_seconds: 14,
  total_seconds: 140,
  last_at: new Date().toISOString(),
  ...over,
});

describe("loadTone", () => {
  it("divides by the processors rather than reading the average as a percentage", () => {
    // The whole point. 1.4 is comfortable on 8 and oversubscribed on 2, and a
    // tone function that compared the raw number against a threshold would
    // call this box healthy at twice its capacity.
    expect(loadTone(2, 8)).toBe("ok");
    expect(loadTone(2, 2)).toBe("bad");
  });

  it("warns from seven tenths of a processor each and goes red at a full one", () => {
    expect(loadTone(0.8, 2)).toBe("ok");
    // Exactly 0.7 each: the band starts here rather than above it, because a
    // box sitting on the threshold is the one worth looking at early.
    expect(loadTone(1.4, 2)).toBe("warn");
    expect(loadTone(1.9, 2)).toBe("warn");
    // 1.0 each — more ready to run than there are processors to run it.
    expect(loadTone(2, 2)).toBe("bad");
    expect(loadTone(3.4, 2)).toBe("bad");
  });

  it("says nothing when there is nothing to say", () => {
    expect(loadTone(null, 2)).toBe("idle");
    expect(loadTone(1, null)).toBe("idle");
    // Not a division by zero dressed as a verdict.
    expect(loadTone(1, 0)).toBe("idle");
  });
});

describe("memoryTone", () => {
  it("is tied to the browser's ceiling, not to a round number", () => {
    // Above the cap there is room to start the browser and let it take
    // everything it is allowed to take.
    expect(memoryTone(BROWSER_CAP + 1)).toBe("ok");
    // Below it the slice can no longer be given its allowance.
    expect(memoryTone(BROWSER_CAP - 1)).toBe("warn");
    // Below half of it the browser will not fit at all.
    expect(memoryTone(BROWSER_CAP / 2 - 1)).toBe("bad");
  });

  it("reads this box's real figures as healthy", () => {
    // 3.1 GB available of 3.7 GB total, which is what `free -h` says here —
    // and would read as nearly out of memory if `MemFree` (about 400 MB) were
    // the input instead. That mistake is the reason this test names numbers.
    expect(memoryTone(3.1 * 1024 ** 3)).toBe("ok");
    expect(memoryTone(412 * 1024 ** 2)).toBe("bad");
  });

  it("is idle rather than alarmed when nothing was measured", () => {
    expect(memoryTone(null)).toBe("idle");
  });
});

describe("diskTone", () => {
  it("judges the proportion left, not the bytes", () => {
    expect(diskTone(30 * 1024 ** 3, 40 * 1024 ** 3)).toBe("ok");
    expect(diskTone(6 * 1024 ** 3, 40 * 1024 ** 3)).toBe("warn");
    expect(diskTone(2 * 1024 ** 3, 40 * 1024 ** 3)).toBe("bad");
    expect(diskTone(null, 40)).toBe("idle");
    expect(diskTone(1, 0)).toBe("idle");
  });
});

describe("browserTone", () => {
  it("goes red before the ceiling rather than at it", () => {
    // A process the kernel kills leaves no sample to colour, so a threshold
    // set at the cap would only ever be met by a browser that survived it.
    expect(browserTone(450 * 1024 ** 2)).toBe("ok");
    expect(browserTone(BROWSER_CAP * 0.6)).toBe("warn");
    expect(browserTone(BROWSER_CAP * 0.9)).toBe("bad");
  });
});

describe("mintTone", () => {
  it("counts memory spent without a token as the failure it is", () => {
    // The case this page exists for: the browser ran every time and came away
    // with nothing, which costs the resources and leaves zoopla_london
    // challenged anyway.
    expect(mintTone(use({ minted: 0 }))).toBe("bad");
    expect(mintTone(use({ minted: 7 }))).toBe("warn");
    expect(mintTone(use())).toBe("ok");
  });

  it("treats a crashed run as bad however good the rate is", () => {
    expect(mintTone(use({ runs: 10, minted: 10, failed: 1 }))).toBe("bad");
  });

  it("is idle when the browser did not run, which nights and weekends are", () => {
    expect(mintTone(use({ runs: 0, minted: 0 }))).toBe("idle");
  });
});

describe("worst", () => {
  it("does not let an unmeasured reading outrank a bad one", () => {
    expect(worst(["idle", "bad"])).toBe("bad");
    expect(worst(["ok", "warn"])).toBe("warn");
    expect(worst(["idle", "idle"])).toBe("idle");
    expect(worst([])).toBe("idle");
  });
});

describe("formatting", () => {
  it("prints an em dash for not measured and never a misleading zero", () => {
    expect(gb(null)).toBe("—");
    expect(mb(null)).toBe("—");
    expect(pct(null, 10)).toBe("—");
    expect(pct(10, null)).toBe("—");
    expect(pct(10, 0)).toBe("—");
  });

  it("prints the figures this box actually shows", () => {
    expect(gb(3.7 * 1024 ** 3)).toBe("3.7");
    expect(gb(40 * 1024 ** 3)).toBe("40.0");
    expect(mb(450 * 1024 ** 2)).toBe("450");
    expect(pct(450 * 1024 ** 2, BROWSER_CAP)).toBe("22%");
  });
});

describe("word", () => {
  it("names what the colour means rather than repeating the number", () => {
    expect(word("idle", "load")).toBe("not measured");
    expect(word("ok", "mint")).toBe("earning it");
    expect(word("bad", "mint")).toBe("spending for nothing");
    expect(word("bad", "browser")).toBe("near the cap");
    expect(word("ok", "memory")).toBe("room to spare");
  });
});

// ── what the two charts plot ─────────────────────────────────────────────
//
// Same reason as the tones above: these are arithmetic that cannot look wrong.
// A load average plotted undivided draws this box at half its real occupancy,
// memory plotted the wrong way up draws a crisis as a dip, and either is read
// once and then trusted. The third thing under test is the one the SQL and the
// chart both go out of their way to preserve: a bucket nobody measured has no
// value, and must not arrive as a zero.

const bucket = (over: Partial<HostPoint> = {}): HostPoint => ({
  label: "2026-10-12 14:00",
  load1: 1.4,
  cpus: 2,
  mem_available: 3.1 * 1024 ** 3,
  mem_total: 4 * 1024 ** 3,
  hosts: 1,
  ...over,
});

/** A bucket no run fell in: the aggregates come back NULL, not zero. */
const empty = (): HostPoint =>
  bucket({ load1: null, cpus: null, mem_available: null, mem_total: null, hosts: 0 });

describe("cpuReadings", () => {
  it("divides by the processors, so 100% means one runnable process each", () => {
    // The same mistake `loadTone` is built to avoid, in the chart instead of
    // the tile: undivided, 1.4 would be drawn at 1.4% of a 100% axis and this
    // box at seventy percent of capacity would look idle.
    expect(cpuReadings([bucket({ load1: 1.4, cpus: 2 })])[0]?.value).toBeCloseTo(70, 10);
    expect(cpuReadings([bucket({ load1: 2, cpus: 2 })])[0]?.value).toBeCloseTo(100, 10);
  });

  it("lets a reading past 100%, which is a real state and not a bug", () => {
    // Load average counts what is waiting as well as what is running, so it
    // goes past the processor count. Clamping it would hide the only readings
    // worth looking at.
    expect(cpuReadings([bucket({ load1: 3.4, cpus: 2 })])[0]?.value).toBeCloseTo(170, 10);
  });

  it("leaves a bucket nothing ran in as null rather than as a reading of zero", () => {
    expect(cpuReadings([empty()])[0]?.value).toBeNull();
  });

  it("is null rather than Infinity when the host did not say how many processors it has", () => {
    expect(cpuReadings([bucket({ cpus: null })])[0]?.value).toBeNull();
    expect(cpuReadings([bucket({ cpus: 0 })])[0]?.value).toBeNull();
  });

  it("keeps the labels and the order it was given, which is what pairs the gaps up", () => {
    const points = [bucket({ label: "a" }), empty(), bucket({ label: "c" })];

    expect(cpuReadings(points).map((one) => one.label)).toEqual(["a", "2026-10-12 14:00", "c"]);
  });
});

describe("ramReadings", () => {
  it("plots memory used, not the available figure the tile prints", () => {
    // Drawn this way up on purpose: both charts have to rise when the box is
    // in trouble. 3.1 GB available out of 4 is 22.5% used.
    expect(ramReadings([bucket()])[0]?.value).toBeCloseTo(22.5, 6);
  });

  it("reaches 100% when nothing is available and 0% when all of it is", () => {
    expect(ramReadings([bucket({ mem_available: 0 })])[0]?.value).toBe(100);
    expect(ramReadings([bucket({ mem_available: 4 * 1024 ** 3 })])[0]?.value).toBe(0);
  });

  it("leaves an unsampled bucket null, where zero would claim the memory was free", () => {
    // The one that matters most of the four. Zero here reads as "none of the
    // memory was in use", which is the most reassuring thing the chart could
    // say and is said about a bucket where nobody looked.
    expect(ramReadings([empty()])[0]?.value).toBeNull();
    expect(ramReadings([bucket({ mem_total: null })])[0]?.value).toBeNull();
  });
});

describe("ramGuide", () => {
  it("lands where what is left stops covering the browser's cap", () => {
    // 2 GB cap on a 4 GB box: at 50% used there is exactly the cap available,
    // and above that line a mint can no longer be given what it is allowed to
    // take. The same judgment as `memoryTone`, derived from the same two
    // numbers so the line and the tile's colour cannot disagree.
    expect(ramGuide([bucket()])).toBeCloseTo(50, 10);
  });

  it("is read off the box rather than written down, so a bigger box moves it", () => {
    expect(ramGuide([bucket({ mem_total: 8 * 1024 ** 3 })])).toBeCloseTo(75, 10);
  });

  it("has nothing to say about a box no larger than the cap", () => {
    // The line would sit at or below zero and mean nothing: such a box can
    // never satisfy the slice, which is a fact about the box rather than
    // something a dashed line says about one afternoon.
    expect(ramGuide([bucket({ mem_total: BROWSER_CAP })])).toBeNull();
    expect(ramGuide([bucket({ mem_total: 1024 ** 3 })])).toBeNull();
  });

  it("is null when no bucket said how big the box is", () => {
    expect(ramGuide([empty()])).toBeNull();
    expect(ramGuide([])).toBeNull();
  });
});

describe("mixedHosts", () => {
  it("catches a bucket holding runs from two machines", () => {
    // The one state in which both charts print a number belonging to no
    // machine: the worst load average comes from one row and the processor
    // count it is divided by from another.
    expect(mixedHosts([bucket(), bucket({ hosts: 2 }), bucket()])).toBe(true);
  });

  it("is quiet for the ordinary case, including buckets nothing ran in", () => {
    // It must not fire on a quiet night, or the notice is permanently up and
    // therefore unread — the same failure the module note gives for colouring
    // memory off MemFree.
    expect(mixedHosts([bucket(), empty(), bucket()])).toBe(false);
    expect(mixedHosts([])).toBe(false);
  });
});
