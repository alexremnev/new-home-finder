import { describe, expect, it } from "vitest";

import { shareWords } from "../share";

describe("a share said as a count", () => {
  it("turns a share that divides into one listing in so many", () => {
    expect(shareWords(20)).toBe("1 in 5");
    expect(shareWords(10)).toBe("1 in 10");
    expect(shareWords(50)).toBe("1 in 2");
    expect(shareWords(25)).toBe("1 in 4");
  });

  it("keeps the percentage where a count would overstate it", () => {
    // A third of thirty is not a whole number of listings, and "1 in 3" claims
    // more is delivered than is.
    expect(shareWords(30)).toBe("30%");
    expect(shareWords(15)).toBe("15%");
  });

  // Both are said in other words entirely — "the alerts stop", "everything
  // that matches" — and neither sentence asks this function for the number.
  it("leaves the two ends alone", () => {
    expect(shareWords(0)).toBe("0%");
    expect(shareWords(100)).toBe("100%");
  });
});
