import { describe, expect, it } from "vitest";

import { ago, at, atSecond, clockOf, dayOf, olderThan, since } from "../when";

// Postgres hands back `2026-07-17 10:39:58.73+00`: a space, and the offset.
const summer = "2026-07-17 10:39:58.73+00"; // BST, London is UTC+1
const winter = "2026-01-17 10:39:58.73+00"; // GMT, London is UTC+0

describe("London time", () => {
  it("shifts a summer timestamp by the hour BST adds", () => {
    expect(clockOf(summer)).toBe("11:39");
    expect(at(summer)).toBe("17 Jul 11:39");
  });

  it("leaves a winter timestamp where it is", () => {
    expect(clockOf(winter)).toBe("10:39");
    expect(at(winter)).toBe("17 Jan 10:39");
  });

  it("does the same for an ISO string with a T", () => {
    expect(clockOf("2026-07-17T10:39:58Z")).toBe("11:39");
  });

  it("reads an offset other than zero correctly", () => {

    // 09:39+01 is 08:39 UTC, which is 09:39 in London in July. The offset in
    // the string is what matters, not the digits in front of it.
    expect(clockOf("2026-07-17 09:39:00+01")).toBe("09:39");
  });

  it("keeps the seconds when asked", () => {
    expect(atSecond(summer)).toBe("17 Jul 11:39:58");
  });

  it("writes a day the way a person does", () => {
    expect(dayOf(summer)).toBe("17 Jul 2026");
  });
});

describe("what it does with nothing", () => {
  it("says so rather than printing Invalid Date", () => {
    for (const empty of [null, "", "not a date"]) {
      expect(at(empty as string | null)).toBe("—");
      expect(dayOf(empty as string | null)).toBe("—");
      expect(ago(empty as string | null)).toBe("never");
      expect(since(empty as string | null)).toBe("never");
    }
  });
});

describe("relative time", () => {
  const minutesAgo = (n: number) =>
    new Date(Date.now() - n * 60_000).toISOString();

  it("counts from now, whatever the zone", () => {
    expect(ago(minutesAgo(0.2))).toBe("just now");
    expect(ago(minutesAgo(12))).toBe("12 min ago");
    expect(ago(minutesAgo(180))).toBe("3h ago");
    expect(ago(minutesAgo(60 * 48))).toBe("2d ago");
  });

  it("compresses the same thing for a table cell", () => {
    expect(since(minutesAgo(12))).toBe("12m");
    expect(since(minutesAgo(180))).toBe("3h");
  });

  it("never reports less than a minute as zero", () => {
    expect(since(minutesAgo(0.1))).toBe("1m");
  });

  it("answers whether a promise made in hours is broken", () => {
    expect(olderThan(minutesAgo(60 * 25), 24)).toBe(true);
    expect(olderThan(minutesAgo(60 * 23), 24)).toBe(false);
    expect(olderThan(null, 24)).toBe(false);
  });
});
