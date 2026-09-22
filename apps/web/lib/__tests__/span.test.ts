import { describe, expect, it } from "vitest";

import {
  DEFAULT_SPAN,
  SPAN_KEYS,
  bucketMinutes,
  bucketWords,
  spanFrom,
} from "@/app/admin/(dash)/span";

// 14:30 London on a summer day (BST, UTC+1), so a test that confused the two
// zones would be an hour out rather than accidentally right.
const SUMMER = new Date("2026-09-22T13:30:00Z");

// 09:15 London in winter, when London is UTC.
const WINTER = new Date("2026-01-14T09:15:00Z");

describe("the list itself", () => {
  it("offers every range the dashboard asks for, in order", () => {
    expect(SPAN_KEYS).toEqual([
      "today", "yesterday",
      "1h", "2h", "3h", "6h", "12h",
      "1d", "2d", "3d", "4d", "5d", "6d",
      "1w", "2w", "1m",
    ]);
  });

  it("opens on today", () => {
    expect(DEFAULT_SPAN).toBe("today");
    // An unknown or absent `?w=` is today too, not a crash and not a week.
    expect(spanFrom(undefined, SUMMER).key).toBe("today");
    expect(spanFrom("nonsense", SUMMER).key).toBe("today");
  });
});

describe("today", () => {
  it("starts at London midnight, not at midnight UTC", () => {
    const span = spanFrom("today", SUMMER);
    // 14:30 London, so 870 minutes of the day have passed. 810 would mean the
    // clock was read in UTC.
    expect(span.mins).toBe(14 * 60 + 30);
    expect(span.endMins).toBe(0);
  });

  it("is one London day, both ends", () => {
    const span = spanFrom("today", SUMMER);
    expect(span.fromDay).toBe("2026-09-22");
    expect(span.toDay).toBe("2026-09-22");
    expect(span.days).toBe(1);
  });

  it("works when London is on UTC", () => {
    const span = spanFrom("today", WINTER);
    expect(span.mins).toBe(9 * 60 + 15);
    expect(span.fromDay).toBe("2026-01-14");
  });
});

describe("yesterday", () => {
  it("is a window that ended at this morning's midnight", () => {
    const span = spanFrom("yesterday", SUMMER);
    // Ends where today begins, and starts a day before that.
    expect(span.endMins).toBe(14 * 60 + 30);
    expect(span.mins).toBe(14 * 60 + 30 + 1_440);
  });

  it("names yesterday's date on both ends", () => {
    const span = spanFrom("yesterday", SUMMER);
    expect(span.fromDay).toBe("2026-09-21");
    expect(span.toDay).toBe("2026-09-21");
  });

  it("is twenty-four hours long, not the time since midnight", () => {
    // The bug this guards: measuring the window as `mins` alone would make
    // "yesterday" mean "the last 38 hours" at half past two in the afternoon.
    const span = spanFrom("yesterday", SUMMER);
    expect(span.mins - span.endMins).toBe(1_440);
    expect(span.hours).toBe(24);
  });
});

describe("rolling windows", () => {
  it("ends now", () => {
    for (const key of ["1h", "6h", "1d", "1w", "1m"] as const) {
      expect(spanFrom(key, SUMMER).endMins).toBe(0);
    }
  });

  it("measures the length asked for", () => {
    expect(spanFrom("1h", SUMMER).mins).toBe(60);
    expect(spanFrom("3h", SUMMER).mins).toBe(180);
    expect(spanFrom("2d", SUMMER).mins).toBe(48 * 60);
    expect(spanFrom("1w", SUMMER).mins).toBe(168 * 60);
    expect(spanFrom("2w", SUMMER).mins).toBe(336 * 60);
    expect(spanFrom("1m", SUMMER).mins).toBe(720 * 60);
  });

  it("covers whole days for the tables keyed on a date", () => {
    // A date column cannot answer part of a day, so an hour-long window reads
    // today's row — the nearest true answer rather than an empty one.
    const hour = spanFrom("1h", SUMMER);
    expect(hour.fromDay).toBe("2026-09-22");
    expect(hour.toDay).toBe("2026-09-22");
    expect(hour.days).toBe(1);

    const week = spanFrom("1w", SUMMER);
    expect(week.toDay).toBe("2026-09-22");
    expect(week.fromDay).toBe("2026-09-16");
    expect(week.days).toBe(7);
  });

  it("counts a month as thirty days", () => {
    const month = spanFrom("1m", SUMMER);
    expect(month.days).toBe(30);
    expect(month.fromDay).toBe("2026-08-24");
  });
});

describe("chart buckets", () => {
  it("keeps every range to a readable number of points", () => {
    for (const key of SPAN_KEYS) {
      const span = spanFrom(key, SUMMER);
      const points = (span.hours * 60) / bucketMinutes(span.hours);
      // Few enough to read, many enough to have a shape.
      expect(points).toBeGreaterThanOrEqual(4);
      expect(points).toBeLessThanOrEqual(90);
    }
  });

  it("says what a bucket is in words", () => {
    expect(bucketWords(2)).toBe("every 2 minutes");
    expect(bucketWords(60)).toBe("hourly");
    expect(bucketWords(240)).toBe("every 4 hours");
    expect(bucketWords(1_440)).toBe("daily");
    expect(bucketWords(2_880)).toBe("every 2 days");
  });
});
