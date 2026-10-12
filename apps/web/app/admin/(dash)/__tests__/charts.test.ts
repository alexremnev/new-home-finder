import { describe, expect, it } from "vitest";

import { bar, splitTotals, type Split } from "../charts";

// The geometry of the stacked "alerts by origin" chart.
//
// Worth a test rather than an eye, because every way it goes wrong is silent:
// a bar that escapes the plot is drawn outside the card rather than throwing,
// a bucket with no alerts divides by a total that could be zero, and a stack
// drawn taller than its own total still looks like a chart — it just no longer
// measures against the scale printed beside it.
//
// The numbers: the baseline is at 102, the top gridline at 12, and the plot
// between them is 90 units tall.
const FLOOR = 102;
const CEILING = 12;

const bucket = (feed: number, scraper: number): Split => ({
  label: "2026-10-12 14:00",
  feed,
  scraper,
});

describe("bar", () => {
  it("stands the feed segment on the baseline", () => {
    const { feed } = bar(bucket(7, 3), 10);

    expect(feed).not.toBeNull();
    expect((feed?.y ?? 0) + (feed?.height ?? 0)).toBeCloseTo(FLOOR, 5);
    expect(feed?.height).toBeCloseTo(63, 5); // 7 of 10, over 90 units
  });

  it("sits the scraper segment directly on top of it", () => {
    const { feed, scraper } = bar(bucket(7, 3), 10);

    expect(scraper?.height).toBeCloseTo(27, 5); // 3 of 10
    expect((scraper?.y ?? 0) + (scraper?.height ?? 0)).toBeCloseTo(feed?.y ?? 0, 5);
  });

  it("keeps a full bucket inside the plot, so the stack measures against the scale", () => {
    // The gap is painted over the join, not spaced into the stack, precisely
    // so that this holds: a bar whose two segments fill the scale reaches the
    // top gridline exactly and no further.
    const { feed, scraper } = bar(bucket(4, 6), 10);

    expect(scraper?.y).toBeCloseTo(CEILING, 5);
    for (const band of [feed, scraper]) {
      expect(band?.y).toBeGreaterThanOrEqual(CEILING);
      expect((band?.y ?? 0) + (band?.height ?? 0)).toBeLessThanOrEqual(FLOOR);
    }
  });

  it("separates two segments by two units, taken from either side of the join", () => {
    const { feed, join } = bar(bucket(7, 3), 10);

    expect(join?.height).toBe(2);
    expect(join?.y).toBeCloseTo((feed?.y ?? 0) - 1, 5);
  });

  it("leaves the join alone where one segment is too thin to spare the pixel", () => {
    // A gap drawn across a one-unit segment would erase it, and an erased
    // segment reads as no alerts rather than as one.
    expect(bar(bucket(100, 1), 100).join).toBeNull();
    expect(bar(bucket(1, 100), 100).join).toBeNull();
  });

  it("draws nothing for a bucket where no alert was sent", () => {
    expect(bar(bucket(0, 0), 4)).toEqual({ feed: null, scraper: null, join: null });
  });

  it("draws only the segment that has alerts", () => {
    expect(bar(bucket(0, 5), 10).feed).toBeNull();
    expect(bar(bucket(5, 0), 10).scraper).toBeNull();
    // The one that is drawn still stands on the baseline.
    const { scraper } = bar(bucket(0, 5), 10);
    expect((scraper?.y ?? 0) + (scraper?.height ?? 0)).toBeCloseTo(FLOOR, 5);
  });
});

describe("splitTotals", () => {
  it("totals each reader over the window", () => {
    expect(splitTotals([bucket(7, 3), bucket(1, 9)])).toEqual({ feed: 8, scraper: 12 });
  });

  it("is nought for an empty window rather than NaN", () => {
    expect(splitTotals([])).toEqual({ feed: 0, scraper: 0 });
  });
});
