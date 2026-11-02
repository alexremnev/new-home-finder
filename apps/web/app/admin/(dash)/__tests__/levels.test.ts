import { describe, expect, it } from "vitest";

import { peakOf, stretches, type Reading } from "../levels";

// ── a level over time, where "no reading" is not a reading of zero ────────
//
// `Gauge` is the only chart here that can be handed a bucket with no value,
// and the two ways it goes wrong are both silent. A gap drawn as a point at
// the floor claims the box had no memory free, which is the opposite of what
// a quiet night means. A lone reading between two gaps draws as a path with
// one point, which renders as nothing at all — so the one sample taken all
// night disappears, and the chart that was meant to prove the worker is alive
// shows an empty card.

const reading = (value: number | null): Reading => ({ label: "2026-10-12 14:00", value });

describe("stretches", () => {
  it("breaks the line at every bucket nothing sampled", () => {
    const data = [1, 2, null, 4, 5, 6].map(reading);

    expect(stretches(data)).toEqual([[0, 1], [3, 4, 5]]);
  });

  it("keeps a lone reading as a stretch of its own, so it can be drawn as a dot", () => {
    // The overnight case. A path built from one point has no line segment and
    // draws nothing, which is why this has to be distinguishable rather than
    // merged into the neighbouring stretch.
    const data = [null, 2, null, null, 5, null].map(reading);

    expect(stretches(data)).toEqual([[1], [4]]);
  });

  it("is empty when nothing sampled at all, rather than one stretch of zeroes", () => {
    expect(stretches([null, null, null].map(reading))).toEqual([]);
  });

  it("does not treat a genuine zero as a gap", () => {
    // A load of exactly 0.00 is a real reading on an idle box and belongs on
    // the line. Only null is absence — which is the distinction the whole
    // chart exists to keep.
    expect(stretches([0, 0, 0].map(reading))).toEqual([[0, 1, 2]]);
  });
});

describe("peakOf", () => {
  it("ignores the buckets with no reading", () => {
    expect(peakOf([3, null, 9, null, 4].map(reading))).toBe(9);
  });

  it("is null rather than zero when nothing sampled", () => {
    // What tells the panel to print a hint instead of an empty chart. A zero
    // would be indistinguishable from a box that was genuinely idle.
    expect(peakOf([null, null].map(reading))).toBeNull();
    expect(peakOf([])).toBeNull();
  });
});
