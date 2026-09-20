import Stripe from "stripe";

// Two Stripe accounts, one switch.
//
// The test keys and the live keys both sit in the environment; STRIPE_MODE says
// which pair is in use. That way going live is one variable rather than pasting
// secrets under pressure, and going back is the same variable.
//
// Everything that differs between the two accounts is selected together — the
// secret key, the webhook secret and the Price ids — because switching one
// without the others is the interesting way to break this.
export type StripeMode = "test" | "live";

export function stripeMode(): StripeMode {
  return (process.env.STRIPE_MODE ?? "").trim().toLowerCase() === "live" ? "live" : "test";
}

// Stripe keys say which account they belong to, so the mismatch that matters
// can be caught here instead of by a real card being charged. Restricted keys
// (rk_) are as valid as secret ones.
const EXPECTED: Record<StripeMode, RegExp> = {
  test: /^(sk|rk)_test_/,
  live: /^(sk|rk)_live_/,
};

function pick(mode: StripeMode, live: string | undefined, test: string | undefined) {
  return ((mode === "live" ? live : test) ?? "").trim() || null;
}

export type StripeSetup =
  | { ok: true; mode: StripeMode; key: string; webhookSecret: string | null }
  | { ok: false; mode: StripeMode; why: string };

export function stripeSetup(): StripeSetup {
  const mode = stripeMode();
  const key = pick(mode, process.env.STRIPE_SECRET_KEY_LIVE, process.env.STRIPE_SECRET_KEY);
  const webhookSecret = pick(
    mode,
    process.env.STRIPE_WEBHOOK_SECRET_LIVE,
    process.env.STRIPE_WEBHOOK_SECRET,
  );

  if (!key) {
    return {
      ok: false,
      mode,
      why:
        mode === "live"
          ? "STRIPE_MODE is live but STRIPE_SECRET_KEY_LIVE is not set"
          : "STRIPE_SECRET_KEY is not set",
    };
  }

  if (!EXPECTED[mode].test(key)) {
    // The whole point of the guard. Live mode with a test key takes no money
    // and looks like it worked; test mode with a live key charges somebody's
    // real card while you are trying things out.
    const looks = /_live_/.test(key) ? "live" : /_test_/.test(key) ? "test" : "neither";
    return {
      ok: false,
      mode,
      why:
        `STRIPE_MODE is ${mode} but the key given for it looks like a ${looks} ` +
        `key. Refusing to use it — check STRIPE_SECRET_KEY` +
        (mode === "live" ? "_LIVE" : "") + ".",
    };
  }

  return { ok: true, mode, key, webhookSecret };
}

export function stripeClient(): Stripe | null {
  const setup = stripeSetup();
  if (!setup.ok) {
    console.error("stripe not usable", { mode: setup.mode, why: setup.why });
    return null;
  }
  return new Stripe(setup.key);
}

// The signing secret of the endpoint belonging to whichever account is in use.
// Not both: an endpoint is created per account, and accepting either signature
// would mean a test event could grant a plan in production.
export function stripeWebhookSecret(): string | null {
  const setup = stripeSetup();
  if (!setup.ok) {
    console.error("stripe webhook not usable", { mode: setup.mode, why: setup.why });
    return null;
  }
  if (!setup.webhookSecret) {
    console.error("stripe webhook secret missing", {
      mode: setup.mode,
      expected:
        setup.mode === "live" ? "STRIPE_WEBHOOK_SECRET_LIVE" : "STRIPE_WEBHOOK_SECRET",
    });
    return null;
  }
  return setup.webhookSecret;
}
