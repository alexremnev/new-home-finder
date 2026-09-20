import { describe, expect, it } from "vitest";

import { SUPPORT_EMAIL, SUPPORT_REPLY } from "../support";

describe("support", () => {
  it("answers with the address and nothing to fill in", () => {
    // The whole of support: no prompt, no draft, no ticket to lose.
    expect(SUPPORT_REPLY).toContain(SUPPORT_EMAIL);
  });

  it("names an address on our own domain", () => {
    // A support address on somebody else's domain stops working the day that
    // account does.
    expect(SUPPORT_EMAIL).toMatch(/@londonhomefinder\.co\.uk$/);
  });

  it("says how long an answer takes, because a promise is why people write", () => {
    expect(SUPPORT_REPLY).toContain("24 hours");
  });
});
