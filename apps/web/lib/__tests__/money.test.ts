import { describe, expect, it } from "vitest";

import { dollars, pounds, rate, waFormula } from "../money";

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

describe("WhatsApp rates", () => {
  it("writes a rate at the precision it has, not the cent", () => {
    // $0.022 rounded to the nearest cent is $0.02 — a tenth out, and on a
    // figure the reader is invited to multiply by.
    expect(rate(22000)).toBe("$0.022");
    expect(rate(63500)).toBe("$0.0635");
  });

  it("still rounds a total to the cent", () => {
    // The other half of the same rule: a rate keeps its precision, a total
    // is money and reads as money.
    expect(dollars(22000)).toBe("$0.02");
    expect(dollars(27_588_500)).toBe("$27.59");
  });

  it("writes the bill out as the sum it is", () => {
    // 1234 × $0.022 + 7 × $0.0635 = $27.1485 + $0.4445 = $27.59
    expect(waFormula(1234, 7, 22000, 63500)).toBe(
      "1,234 × $0.022 service + 7 × $0.0635 marketing = $27.59",
    );
  });

  it("says nought rather than nothing on an empty month", () => {
    expect(waFormula(0, 0, 22000, 63500)).toBe(
      "0 × $0.022 service + 0 × $0.0635 marketing = $0.00",
    );
  });
});
