import { describe, expect, it } from "vitest";

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
