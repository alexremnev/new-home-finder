import { describe, expect, it } from "vitest";

import { countryFrom, deviceFrom } from "../visits";

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
