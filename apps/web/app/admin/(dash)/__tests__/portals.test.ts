import { describe, expect, it } from "vitest";

import type { PortalRun } from "@/lib/admin-queries";

import { PORTALS, portalTone } from "../portals";

// The coloured stripe on a Portal scrapers tile, which is the part of that
// panel that can be wrong without looking wrong: the figures beside it are
// read straight off the counters the run wrote, and the colour is a judgment
// about what they add up to.
//
// It used to be a judgment about the window — any failure or any refusal
// anywhere in it, red — and that is the wrong question for a reader that runs
// every twelve minutes. On a day's window a reader is then red from its first
// bad sweep until that sweep scrolls out, which on this panel is most of the
// day, and a stripe that is red while the reader is working is a stripe
// nobody reads. The latest run is the one thing that answers "is it working
// now".
const row = (over: Partial<PortalRun> = {}): PortalRun => ({
  source: "rightmove",
  runs: 10,
  bad: 0,
  bytes: 1_000_000,
  proxy_bytes: 0,
  requests: 20,
  stored: 5,
  announced: 4,
  seen: 9,
  caught_up: 2,
  invalid: 0,
  refused: 0,
  partial: 0,
  last_at: "2026-11-02T09:00:00Z",
  last_status: "ok",
  ...over,
});

describe("portalTone", () => {
  it("is green when the window is clean and the last run with it", () => {
    expect(portalTone(row())).toBe("good");
  });

  it("is red when the last run failed, however well the rest went", () => {
    expect(portalTone(row({ runs: 40, bad: 1, last_status: "failed" }))).toBe("bad");
  });

  // The case the old rule got wrong: it broke once this morning and has read
  // cleanly every twelve minutes since.
  it("is amber when the last run was fine and something earlier was not", () => {
    expect(portalTone(row({ bad: 1, last_status: "ok" }))).toBe("warn");
  });

  it("is amber while the last run is itself degraded", () => {
    expect(portalTone(row({ bad: 1, last_status: "degraded" }))).toBe("warn");
  });

  // A refusal costs one late read and nothing else — the watermark only moves
  // on a complete one — so it is worth seeing and not worth a red tile.
  it("no longer turns red on a refusal alone", () => {
    expect(portalTone(row({ refused: 3 }))).toBe("warn");
  });

  it("still counts a part-read and a rejected listing as worth seeing", () => {
    expect(portalTone(row({ partial: 1 }))).toBe("warn");
    expect(portalTone(row({ invalid: 2 }))).toBe("warn");
  });

  // A reader with no runs at all is the fault this panel exists to show, and
  // it cannot show itself: there is no row to colour, which is why the tile is
  // named here rather than taken from the database.
  it("is red for a reader that has not run in the range", () => {
    expect(portalTone(undefined)).toBe("bad");
    expect(portalTone(row({ runs: 0, last_status: null, last_at: null }))).toBe("bad");
  });

  // A run killed mid-scrape leaves its stage 'running' for good, and so does
  // one that is scraping right now. Neither is a verdict, so the window
  // decides — see the Jobs panel for the 'died' count, which is where a killed
  // run is actually reported.
  it("lets the window speak for a run that never reported", () => {
    expect(portalTone(row({ last_status: "running" }))).toBe("good");
    expect(portalTone(row({ bad: 2, last_status: "running" }))).toBe("warn");
  });
});

describe("PORTALS", () => {
  // Every reader that fetches a portal's own pages needs a tile, and the one
  // added last is the one that gets forgotten: onthemarket had a timer, a
  // proxy row and a read chart for a month before it had a tile here.
  it("names every reader the dashboard schedules", () => {
    expect(PORTALS.map((one) => one.source)).toEqual([
      "rightmove",
      "zoopla",
      "openrent",
      "onthemarket",
      "spareroom",
    ]);
  });

  it("explains each one", () => {
    for (const one of PORTALS) {
      expect(one.label.length).toBeGreaterThan(0);
      expect(one.why.length).toBeGreaterThan(80);
    }
  });
});
