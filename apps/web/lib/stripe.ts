// Stripe, kept behind one module so that the rest of the app never imports it.
//
// Everything here is inert unless STRIPE_SECRET_KEY and STRIPE_PRICE_ID are set.
// That is what lets the same deployment take bank transfers on day one and cards
// later, without a branch anywhere else.
//
// Honest note: this code has not been exercised against Stripe's live or test API
// in this repository. The signature check is delegated to the SDK rather than
// hand-rolled, which is the part most worth not writing oneself, but the first
// real payment should be a test-mode one watched end to end.

import Stripe from "stripe";

export function stripeClient(): Stripe | null {
  const key = process.env.STRIPE_SECRET_KEY;
  return key ? new Stripe(key) : null;
}

export function cardPaymentsEnabled(): boolean {
  return Boolean(process.env.STRIPE_SECRET_KEY && process.env.STRIPE_PRICE_ID);
}
