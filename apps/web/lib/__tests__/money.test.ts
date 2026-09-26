import { describe, expect, it } from "vitest";

import { pounds } from "../money";

describe("prices", () => {
  it("keeps the pence that were actually charged", () => {
    // The bug this replaced: the admin's Payments page rounded, so a £19.99
    // plan was reported as £20 — a price nobody was ever charged, on the one
    // page whose job is to say what was.
    expect(pounds(1999)).toBe("£19.99");
    expect(pounds(999)).toBe("£9.99");
    expect(pounds(499)).toBe("£4.99");
  });

  it("drops a trailing .00", () => {
    expect(pounds(2000)).toBe("£20");
    expect(pounds(500)).toBe("£5");
    expect(pounds(0)).toBe("£0");
  });

  it("groups thousands", () => {
    expect(pounds(123456)).toBe("£1,234.56");
    expect(pounds(500000)).toBe("£5,000");
  });

  it("puts the sign before the symbol on a refund", () => {
    // Refunds are stored as negative amounts in the same table.
    expect(pounds(-1999)).toBe("−£19.99");
  });
});
