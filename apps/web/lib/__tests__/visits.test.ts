import { afterEach, describe, expect, it, vi } from "vitest";

import { browserFrom, countryFrom, deviceFrom } from "../visits";

describe("deviceFrom", () => {
  it("calls a phone a phone", () => {
    expect(deviceFrom("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile/15E148")).toBe("mobile");
    expect(
      deviceFrom("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/120 Mobile Safari/537"),
    ).toBe("mobile");
  });

  it("does not call an android tablet a phone", () => {
    // Android tablets say "Android" without "Mobile", which is the only thing
    // separating them.
    expect(deviceFrom("Mozilla/5.0 (Linux; Android 14; SM-X200) Chrome/120 Safari/537")).toBe(
      "tablet",
    );
    expect(deviceFrom("Mozilla/5.0 (iPad; CPU OS 17_0) Safari/604")).toBe("tablet");
  });

  it("treats anything it cannot place as a desktop", () => {
    expect(deviceFrom("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605")).toBe(
      "desktop",
    );
    expect(deviceFrom("something nobody has seen")).toBe("desktop");
  });
});

describe("countryFrom", () => {
  it("takes the two letters the edge gives", () => {
    expect(countryFrom("GB")).toBe("GB");
    expect(countryFrom(" gb ")).toBe("GB");
  });

  it("keeps nothing it cannot use", () => {
    // XX is what Vercel sends when the address cannot be placed, and storing it
    // as a country would put a country called "XX" in the chart.
    expect(countryFrom("XX")).toBeNull();
    expect(countryFrom(null)).toBeNull();
    expect(countryFrom("United Kingdom")).toBeNull();
  });
});

describe("browserFrom", () => {
  it("is not fooled by what these strings claim about each other", () => {
    // Edge says Chrome, Chrome says Safari, and everything says Mozilla. Order
    // is the whole implementation.
    expect(
      browserFrom(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
          "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0",
      ),
    ).toBe("Edge");
    expect(
      browserFrom(
        "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) " +
          "Chrome/141.0.0.0 Safari/537.36",
      ),
    ).toBe("Chrome");
    expect(
      browserFrom(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 " +
          "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
      ),
    ).toBe("Safari");
  });

  it("knows the ones that rename themselves on iOS", () => {
    // Every iOS browser is Safari underneath, so they announce themselves with
    // their own suffix instead.
    expect(browserFrom("Mozilla/5.0 (iPhone) CriOS/141.0 Mobile/15E148 Safari/604.1"))
      .toBe("Chrome");
    expect(browserFrom("Mozilla/5.0 (iPhone) FxiOS/141.0 Mobile/15E148 Safari/605.1"))
      .toBe("Firefox");
    expect(browserFrom("Mozilla/5.0 (iPhone) EdgiOS/141.0 Mobile/15E148 Safari/605.1"))
      .toBe("Edge");
  });

  it("keeps no version number", () => {
    // A version is a fingerprint, and the question was which browser.
    const browser = browserFrom("Mozilla/5.0 Chrome/141.0.7390.55 Safari/537.36");
    expect(browser).toBe("Chrome");
    expect(browser).not.toMatch(/\d/);
  });

  it("answers nothing when the string names no browser", () => {
    // This is the answer that matters: it is the strongest single signal that
    // nobody was reading the page, and it is what the recorder refuses on.
    expect(browserFrom("python-requests/2.32.3")).toBeNull();
    expect(browserFrom("Mozilla/5.0 (compatible)")).toBeNull();
    expect(browserFrom("")).toBeNull();
    expect(browserFrom("Go-http-client/2.0")).toBeNull();
  });
});

// ── what a sign-up is told about where it came from ───────────────────────
//
// `arrivalOf` is the half of 0073 that cannot be read off the request that
// creates the account: the form POSTs from our own page, so its own Referer is
// our own domain. These cover the two halves separately, because they fail
// separately — place and language come off this request, and the source comes
// off the visit row the same person's landing page wrote.

describe("arrivalOf", () => {
  const AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605";
  const HEADERS = {
    address: "203.0.113.9, 70.41.3.18",
    userAgent: AGENT,
    country: "GB",
    city: "London",
    region: "ENG",
    // Our own page, which is what the form always posts from. The whole reason
    // the source is not read from here.
    referer: "https://londonhomefinder.co.uk/",
    language: "en-GB,en;q=0.9,ru;q=0.8",
  };

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it("reads the place and the language off the request itself", async () => {
    // No secret, so there is no visitor hash and no visit row can be found.
    // These three still have to arrive: they are properties of the connection
    // and the browser, not of the visit, and an account that knows nothing
    // about where its owner is would be the commoner outcome otherwise.
    vi.stubEnv("ADMIN_SESSION_SECRET", "");

    const { arrivalOf } = await import("../visits");
    const from = await arrivalOf(HEADERS);

    expect(from.city).toBe("London");
    expect(from.region).toBe("ENG");
    // The first tag only. The whole header is a fingerprint.
    expect(from.language).toBe("en-GB");
  });

  it("says nothing rather than 'direct' when it cannot find the visit", async () => {
    vi.stubEnv("ADMIN_SESSION_SECRET", "");

    const { arrivalOf } = await import("../visits");
    const from = await arrivalOf(HEADERS);

    // The whole point. Normalising this request's own Referer would say
    // "direct" for every sign-up ever made, and a measurement that is always
    // the same number is worse than a blank.
    expect(from.source).toBeNull();
    expect(from.referrer).toBeNull();
    expect(from.campaign).toBeNull();
  });

  it("takes the source from the visit the sign-up came from", async () => {
    vi.stubEnv("ADMIN_SESSION_SECRET", "x".repeat(32));
    const db = await import("../db");
    const asked = vi
      .spyOn(db, "query")
      .mockResolvedValue([
        { source: "reddit", referrer: "reddit.com", campaign: "nov-post" },
      ] as never);

    const { arrivalOf } = await import("../visits");
    const from = await arrivalOf(HEADERS);

    expect(from.source).toBe("reddit");
    expect(from.referrer).toBe("reddit.com");
    expect(from.campaign).toBe("nov-post");
    // Today's row for this visitor, and only that: the hash is per day, so
    // yesterday's visit is a different person as far as this table knows.
    const [sql, params] = asked.mock.calls[0] as [string, unknown[]];
    expect(sql).toContain("FROM site_visits");
    expect(params[0]).toBe(new Date().toISOString().slice(0, 10));
    expect(params[1]).toMatch(/^[0-9a-f]{32}$/);
  });

  it("is not the reason a sign-up fails when the lookup breaks", async () => {
    vi.stubEnv("ADMIN_SESSION_SECRET", "x".repeat(32));
    const db = await import("../db");
    vi.spyOn(db, "query").mockRejectedValue(new Error("no database"));

    const { arrivalOf } = await import("../visits");
    const from = await arrivalOf(HEADERS);

    expect(from.source).toBeNull();
    // And the facts that never needed the database are still there.
    expect(from.city).toBe("London");
  });
});
