import { describe, expect, it } from "vitest";

import {
  DEFAULT_SPAN,
  SPAN_PRESETS,
  absoluteKey,
  bucketMinutes,
  bucketWords,
  londonToMs,
  msToLondon,
  spanFrom,
  spanWords,
} from "@/app/admin/(dash)/span";

// 14:30 London on a summer day (BST, UTC+1), so a test that confused the two
// zones would be an hour out rather than accidentally right.
const SUMMER = new Date("2026-09-22T13:30:00Z");

// 09:15 London in winter, when London is UTC.
const WINTER = new Date("2026-01-14T09:15:00Z");

describe("the list itself", () => {
  it("offers the same ladder of windows Datadog does, shortest first", () => {
    expect(SPAN_PRESETS.map((one) => one.key)).toEqual([
      "15m", "30m", "1h", "4h", "1d", "2d", "1w", "1mo", "3mo",
    ]);
  });

  it("labels each one as a sentence the button can show", () => {
    expect(SPAN_PRESETS[0]?.label).toBe("Past 15 minutes");
    expect(SPAN_PRESETS.at(-1)?.label).toBe("Past 3 months");
  });

  it("opens on a day, and falls back to it rather than crashing", () => {
    expect(DEFAULT_SPAN).toBe("1d");
    expect(spanFrom(undefined, SUMMER).key).toBe("1d");
    expect(spanFrom("nonsense", SUMMER).key).toBe("1d");
    // The ranges the old picker offered are gone, and an old bookmark pointing
    // at one must still open the dashboard.
    expect(spanFrom("today", SUMMER).key).toBe("1d");
    expect(spanFrom("yesterday", SUMMER).key).toBe("1d");
  });
});

describe("rolling windows", () => {
  it("ends now", () => {
    for (const key of ["15m", "1h", "1d", "1w", "3mo"]) {
      expect(spanFrom(key, SUMMER).endMins).toBe(0);
    }
  });

  it("measures the length asked for", () => {
    expect(spanFrom("15m", SUMMER).mins).toBe(15);
    expect(spanFrom("1h", SUMMER).mins).toBe(60);
    expect(spanFrom("4h", SUMMER).mins).toBe(240);
    expect(spanFrom("2d", SUMMER).mins).toBe(48 * 60);
    expect(spanFrom("1w", SUMMER).mins).toBe(168 * 60);
    expect(spanFrom("3mo", SUMMER).mins).toBe(90 * 24 * 60);
  });

  it("covers whole London days for the tables keyed on a date", () => {
    // A date column cannot answer part of a day, so a quarter-hour window reads
    // today's row — the nearest true answer rather than an empty one.
    const short = spanFrom("15m", SUMMER);
    expect(short.fromDay).toBe("2026-09-22");
    expect(short.toDay).toBe("2026-09-22");
    expect(short.days).toBe(1);

    const week = spanFrom("1w", SUMMER);
    expect(week.toDay).toBe("2026-09-22");
    expect(week.fromDay).toBe("2026-09-15");
    expect(week.days).toBe(8);
  });

  it("reads the London date, not the UTC one", () => {
    // 00:30 London in summer is still the previous day in UTC.
    const justAfterMidnight = new Date("2026-09-21T23:30:00Z");
    expect(spanFrom("15m", justAfterMidnight).toDay).toBe("2026-09-22");
  });

  it("works when London is on UTC", () => {
    expect(spanFrom("1d", WINTER).toDay).toBe("2026-01-14");
    expect(spanFrom("1d", WINTER).fromDay).toBe("2026-01-13");
  });
});

describe("an absolute range", () => {
  const from = Date.UTC(2026, 8, 20, 8, 0); // 09:00 London, BST
  const to = Date.UTC(2026, 8, 21, 16, 0); // 17:00 London

  it("travels as two instants and comes back as the same two", () => {
    const span = spanFrom(absoluteKey(from, to), SUMMER);
    expect(span.from).toBe(from);
    expect(span.to).toBe(to);
    expect(span.fromDay).toBe("2026-09-20");
    expect(span.toDay).toBe("2026-09-21");
  });

  it("is measured from now, because that is what the queries ask in", () => {
    const span = spanFrom(absoluteKey(from, to), SUMMER);
    expect(span.mins).toBe((SUMMER.getTime() - from) / 60_000);
    // Unlike every preset, this one ended in the past — which is the whole
    // reason the queries take both bounds.
    expect(span.endMins).toBe((SUMMER.getTime() - to) / 60_000);
    expect(span.endMins).toBeGreaterThan(0);
  });

  it("takes the two ends in either order", () => {
    expect(spanFrom(`${to}~${from}`, SUMMER).from).toBe(from);
  });

  it("cannot reach into the future, where there are no rows", () => {
    const span = spanFrom(absoluteKey(from, SUMMER.getTime() + 86_400_000), SUMMER);
    expect(span.to).toBe(SUMMER.getTime());
    expect(span.endMins).toBe(0);
  });

  it("is never empty, which would read as an outage", () => {
    const span = spanFrom(absoluteKey(from, from), SUMMER);
    expect(span.to! - span.from!).toBeGreaterThan(0);
  });
});

describe("the calendar's wall clock", () => {
  it("reads a typed time as London, not as UTC", () => {
    // 14:30 on a BST day is 13:30 UTC.
    expect(londonToMs("2026-09-22T14:30")).toBe(Date.UTC(2026, 8, 22, 13, 30));
    // And in winter London is UTC.
    expect(londonToMs("2026-01-14T09:15")).toBe(Date.UTC(2026, 0, 14, 9, 15));
  });

  it("round-trips, so reopening the calendar shows what was applied", () => {
    for (const wall of ["2026-09-22T14:30", "2026-01-14T09:15", "2026-03-29T13:00"]) {
      expect(msToLondon(londonToMs(wall)!)).toBe(wall);
    }
  });

  it("gives midnight as 00:00 rather than as 24:00", () => {
    // What en-GB hour12:false hands back at midnight is "24", and a value of
    // "2026-09-22T24:00" is one a datetime-local input silently discards.
    expect(msToLondon(Date.UTC(2026, 8, 21, 23, 0))).toBe("2026-09-22T00:00");
  });

  it("refuses anything that is not a wall clock", () => {
    expect(londonToMs("")).toBeNull();
    expect(londonToMs("yesterday")).toBeNull();
  });
});

describe("chart buckets", () => {
  it("keeps every preset to a readable number of points", () => {
    for (const { key } of SPAN_PRESETS) {
      const span = spanFrom(key, SUMMER);
      const points = (span.hours * 60) / bucketMinutes(span.hours);
      // Few enough to read, many enough to have a shape.
      expect(points).toBeGreaterThanOrEqual(4);
      expect(points).toBeLessThanOrEqual(90);
    }
  });

  it("says what a bucket is in words", () => {
    expect(bucketWords(1)).toBe("every 1 minute");
    expect(bucketWords(2)).toBe("every 2 minutes");
    expect(bucketWords(60)).toBe("hourly");
    expect(bucketWords(240)).toBe("every 4 hours");
    expect(bucketWords(1_440)).toBe("daily");
    expect(bucketWords(2_880)).toBe("every 2 days");
  });
});

describe("the range inside a sentence", () => {
  it("reads as a fragment, not as a button label", () => {
    expect(spanWords(spanFrom("1h", SUMMER))).toBe("in the past 1 hour");
  });

  it("names both ends of an absolute range", () => {
    const words = spanWords(
      spanFrom(absoluteKey(Date.UTC(2026, 8, 20, 8, 0), Date.UTC(2026, 8, 21, 16, 0)), SUMMER),
    );
    expect(words).toContain("between");
    expect(words).toContain("20 Sep");
    expect(words).toContain("21 Sep");
  });
});
