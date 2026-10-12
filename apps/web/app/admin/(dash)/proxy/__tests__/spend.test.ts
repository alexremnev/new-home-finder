import { describe, expect, it } from "vitest";

import type { ProxySpend } from "@/lib/admin-queries";

import { mb, PROXY_JOBS, tone, word } from "../spend";

// The part of the Proxy tab that can be wrong without looking wrong.
//
// Every tile is five numbers and a coloured stripe, and the numbers are read
// off the counters the run itself wrote — there is nothing to get wrong there.
// The stripe is a judgment, and the whole value of this page is that it is the
// right way round: a nought is the healthy answer on four of the five readers
// and a broken one on the fifth, so a tone function that ignored the
// expectation would paint the one reader that matters green exactly when the
// proxy had stopped being used.

const row = (over: Partial<ProxySpend> = {}): ProxySpend => ({
  task: "zoopla_london",
  runs: 4,
  requests: 8,
  proxy_requests: 8,
  bytes: 1_000_000,
  proxy_bytes: 900_000,
  fallback_requests: 0,
  fallback_bytes: 0,
  rotations: 0,
  pinned: 0,
  refused: 0,
  last_at: "2026-10-12T09:00:00Z",
  ...over,
});

describe("tone", () => {
  it("is quiet about a reader that paid nothing and was never meant to", () => {
    expect(tone(row({ proxy_requests: 0, proxy_bytes: 0 }), "direct")).toBe("ok");
  });

  it("warns when a reader that should be free has started paying", () => {
    expect(tone(row({ proxy_requests: 3 }), "direct")).toBe("warn");
  });

  it("is quiet about the one reader that is supposed to pay", () => {
    expect(tone(row(), "proxy")).toBe("ok");
  });

  // The failure this page exists for: the city sweep ran and used no proxy,
  // which means either SCRAPE_PROXY is unset where the worker runs or Zoopla
  // stopped refusing. Both are news; a green tile would be neither.
  it("warns when the reader that needs the proxy ran without it", () => {
    expect(tone(row({ proxy_requests: 0, proxy_bytes: 0 }), "proxy")).toBe("warn");
  });

  it("calls the second provider bad news whoever reached it", () => {
    const fell = row({ fallback_requests: 2, fallback_bytes: 4_000 });
    expect(tone(fell, "proxy")).toBe("bad");
    expect(tone({ ...fell, proxy_requests: 0 }, "direct")).toBe("bad");
  });

  // A reader with no runs in the range has not passed or failed anything, and
  // the district Zoopla reader has no timer at all — so this is the normal
  // state of at least one tile rather than an edge case.
  it("judges nothing about a reader that did not run", () => {
    expect(tone(row({ runs: 0 }), "proxy")).toBe("idle");
    expect(tone(undefined, "proxy")).toBe("idle");
  });
});

describe("word", () => {
  it("says which way round a warning is", () => {
    expect(word("direct", "warn")).toBe("paying");
    expect(word("proxy", "warn")).toBe("went direct");
  });

  it("does not call a reader free just because it paid what it should", () => {
    expect(word("proxy", "ok")).toBe("proxied");
    expect(word("direct", "ok")).toBe("free");
  });
});

describe("mb", () => {
  // The one distinction the page turns on: nothing at all, versus a little.
  it("prints a true nought as a bare zero", () => {
    expect(mb(0)).toBe("0");
    expect(mb(null)).toBe("0");
    expect(mb(undefined)).toBe("0");
  });

  it("never rounds a small spend down to nothing", () => {
    expect(mb(1)).toBe("<0.01");
    expect(mb(5_000)).toBe("<0.01");
  });

  // bigint comes back from pg as a string, which is why this takes one.
  it("reads the string a bigint column returns", () => {
    expect(mb("1048576")).toBe("1.00");
    expect(mb(7 * 1024 * 1024)).toBe("7.00");
  });

  it("drops a decimal once the figure is big enough not to need it", () => {
    expect(mb(123.4 * 1024 * 1024)).toBe("123.4");
  });
});

describe("PROXY_JOBS", () => {
  // The list is passed to the query as the set of names that are job names
  // rather than source keys. A job missing from it would be grouped under its
  // source key instead, which for the Zoopla pair means silently merging the
  // two readers this page exists to separate.
  it("names every scraper job the worker schedules", () => {
    expect([...PROXY_JOBS].sort()).toEqual([
      "openrent",
      "rightmove",
      "spareroom",
      "zoopla",
      "zoopla_london",
    ]);
  });
});
