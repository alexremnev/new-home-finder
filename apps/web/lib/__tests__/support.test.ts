import { describe, expect, it } from "vitest";

import { BODY_LIMIT, SUPPORT_DONE, SUPPORT_EMAIL, readEmail } from "../support";

describe("the email step", () => {
  it("accepts an ordinary address", () => {
    expect(readEmail("someone@example.com")).toBe("someone@example.com");
    expect(readEmail("  a.b+tag@sub.example.co.uk  ")).toBe("a.b+tag@sub.example.co.uk");
  });

  it("takes a dash as answer me here", () => {
    expect(readEmail("-")).toBeNull();
    expect(readEmail("—")).toBeNull();
    expect(readEmail("   ")).toBeNull();
  });

  it("asks again rather than filing something that is not an address", () => {

    // Filing a broken address means promising a reply that cannot be delivered.
    for (const bad of ["no", "someone@", "@example.com", "a@b", "two words@x.com"]) {
      expect(readEmail(bad)).toBe("invalid");
    }
  });

  it("refuses one too long to store", () => {
    expect(readEmail("a".repeat(320) + "@example.com")).toBe("invalid");
  });
});

describe("what the person is told", () => {
  it("promises the 24 hours the admin list measures against", () => {
    expect(SUPPORT_DONE).toContain("within 24 hours");
    expect(SUPPORT_DONE).toContain("received");
  });

  it("says the alerts keep running, because that is the first worry", () => {
    expect(SUPPORT_DONE).toContain("carry on");
  });
});

describe("the body limit", () => {
  it("is large enough for a real complaint and small enough to store", () => {
    expect(BODY_LIMIT).toBeGreaterThanOrEqual(1000);
    expect(BODY_LIMIT).toBeLessThanOrEqual(4000);
  });
});

describe("the support address", () => {
  it("is on the project's own domain, not a personal one", () => {
    expect(SUPPORT_EMAIL).toBe("support@londonhomefinder.co.uk");
    expect(SUPPORT_EMAIL).not.toMatch(/gmail|outlook|yahoo/i);
  });
});
