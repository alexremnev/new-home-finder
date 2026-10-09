import { describe, expect, it } from "vitest";

import { SCHEDULED_JOBS, expectation } from "@/lib/admin-queries";

const HOUR = 60;
const DAY = 24 * 60;

// Which jobs the dashboard is allowed to call silent.
//
// Silence is the one fault nothing else on the page reports, and it is also
// the easiest to cry wolf about: a job that was never going to run in the
// window, or that was stopped on purpose, is not quiet — it is doing what was
// asked of it. Both of those used to take the banner down with them.
describe("whose silence means something", () => {
  it("expects a reader that ticks every few minutes on any window", () => {
    expect(expectation("zoopla_london", 30)).toEqual({ timed: true, due: true });
    expect(expectation("spareroom", 30)).toEqual({ timed: true, due: true });
    expect(expectation("ingest", 30)).toEqual({ timed: true, due: true });
  });

  it("never calls the stopped zoopla reader silent, however long the window", () => {
    // Stopped on purpose — zoopla_london reads the whole city in one search —
    // and its silence is therefore the intended state. It keeps its tile and
    // its history; what it must not do is report a fault.
    for (const mins of [30, HOUR, DAY, 30 * DAY]) {
      expect(expectation("zoopla", mins)).toEqual({ timed: false, due: false });
    }
  });

  it("gives purge a second night before calling it silent", () => {
    // It fires at one in the morning, so a day's window contains a fire it
    // could have missed — but a night's delay in deleting expired rows is not
    // something anybody can see. Two is a stopped timer rather than a missed
    // run.
    expect(expectation("purge", DAY).due).toBe(false);
    expect(expectation("purge", DAY + 1).due).toBe(false);
    expect(expectation("purge", 2 * DAY).due).toBe(true);
  });

  it("still expects purge to exist, and to be drawn", () => {
    expect(expectation("purge", HOUR).timed).toBe(true);
  });

  it("expects a job nobody listed, because it got here by running", () => {
    expect(expectation("a-job-added-after-this-list", HOUR)).toEqual({
      timed: true,
      due: true,
    });
  });

  it("gives every scheduled job a period it could be judged by", () => {
    // A zero would make a job due on every window including an empty one, and
    // reads like an oversight rather than a decision. Nothing-starts-this is
    // spelled null.
    for (const one of SCHEDULED_JOBS) {
      expect(one.everyMins === null || one.everyMins > 0).toBe(true);
      expect(one.silentAfterMins ?? Infinity).toBeGreaterThan(0);
    }
  });
});
