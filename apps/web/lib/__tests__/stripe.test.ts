import { afterEach, describe, expect, it, vi } from "vitest";

import { stripeMode, stripeSetup } from "../stripe";

function env(values: Record<string, string | undefined>) {
  for (const [name, value] of Object.entries(values)) {
    if (value === undefined) vi.stubEnv(name, "");
    else vi.stubEnv(name, value);
  }
}

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("stripeMode", () => {
  it("is test unless it says live, exactly", () => {
    // Anything ambiguous means test. Taking real money is opt-in.
    env({ STRIPE_MODE: undefined });
    expect(stripeMode()).toBe("test");
    env({ STRIPE_MODE: "sandbox" });
    expect(stripeMode()).toBe("test");
    env({ STRIPE_MODE: "LIVE" });
    expect(stripeMode()).toBe("live");
    env({ STRIPE_MODE: " live " });
    expect(stripeMode()).toBe("live");
  });
});

describe("stripeSetup", () => {
  it("uses the test pair in test mode", () => {
    env({
      STRIPE_MODE: "test",
      STRIPE_SECRET_KEY: "sk_test_abc",
      STRIPE_WEBHOOK_SECRET: "whsec_test",
      STRIPE_SECRET_KEY_LIVE: "sk_live_xyz",
      STRIPE_WEBHOOK_SECRET_LIVE: "whsec_live",
    });
    const setup = stripeSetup();
    expect(setup).toMatchObject({ ok: true, mode: "test", key: "sk_test_abc" });
  });

  it("uses the live pair in live mode", () => {
    env({
      STRIPE_MODE: "live",
      STRIPE_SECRET_KEY: "sk_test_abc",
      STRIPE_WEBHOOK_SECRET: "whsec_test",
      STRIPE_SECRET_KEY_LIVE: "sk_live_xyz",
      STRIPE_WEBHOOK_SECRET_LIVE: "whsec_live",
    });
    expect(stripeSetup()).toMatchObject({
      ok: true,
      mode: "live",
      key: "sk_live_xyz",
      webhookSecret: "whsec_live",
    });
  });

  it("refuses live mode holding a test key", () => {
    // This is the failure that looks like success: no money is taken and
    // nothing says why.
    env({ STRIPE_MODE: "live", STRIPE_SECRET_KEY_LIVE: "sk_test_abc" });
    const setup = stripeSetup();
    expect(setup.ok).toBe(false);
    expect(setup.ok === false && setup.why).toMatch(/looks like a test key/);
  });

  it("refuses test mode holding a live key", () => {
    // And this is the failure that charges a real card while you are trying
    // things out.
    env({ STRIPE_MODE: "test", STRIPE_SECRET_KEY: "sk_live_xyz" });
    const setup = stripeSetup();
    expect(setup.ok).toBe(false);
    expect(setup.ok === false && setup.why).toMatch(/looks like a live key/);
  });

  it("accepts a restricted key", () => {
    env({ STRIPE_MODE: "live", STRIPE_SECRET_KEY_LIVE: "rk_live_xyz" });
    expect(stripeSetup().ok).toBe(true);
  });

  it("says which variable is missing, per mode", () => {
    env({ STRIPE_MODE: "live", STRIPE_SECRET_KEY: "sk_test_abc" });
    const setup = stripeSetup();
    expect(setup.ok === false && setup.why).toContain("STRIPE_SECRET_KEY_LIVE");
  });

  it("is simply off when nothing is configured", () => {
    // A deployment with no way to pay is a valid state, and the one it starts in.
    env({ STRIPE_MODE: undefined, STRIPE_SECRET_KEY: undefined });
    expect(stripeSetup().ok).toBe(false);
  });
});
