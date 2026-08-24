// Stripe's webhook: the only place a card payment grants a plan.
//
// Two properties matter more than anything else here.
//
// 1. The signature is verified before the body is trusted. Anyone can POST to
//    this URL, and an unverified webhook is a free-plans endpoint.
//
// 2. Granting is idempotent. Stripe retries, and a retry that extended the plan a
//    second time would give away a fortnight. `payments (provider, provider_ref)`
//    is unique, so the second insert fails and the plan is left alone — the
//    database enforces it rather than this code remembering to.

import { NextResponse } from "next/server";
import type Stripe from "stripe";

import { transaction } from "@/lib/db";
import { stripeClient } from "@/lib/stripe";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<NextResponse> {
  const stripe = stripeClient();
  const secret = process.env.STRIPE_WEBHOOK_SECRET;
  if (!stripe || !secret) {
    return NextResponse.json({ error: "not configured" }, { status: 503 });
  }

  const signature = request.headers.get("stripe-signature");
  if (!signature) return NextResponse.json({ error: "unsigned" }, { status: 400 });

  let event: Stripe.Event;
  try {
    // The raw body, not the parsed one: the signature is over the exact bytes.
    event = stripe.webhooks.constructEvent(await request.text(), signature, secret);
  } catch (error) {
    return NextResponse.json({ error: `bad signature: ${String(error)}` }, { status: 400 });
  }

  if (event.type !== "checkout.session.completed") {
    // Acknowledged and ignored. Answering 2xx stops Stripe retrying events we
    // have no use for.
    return NextResponse.json({ ok: true, ignored: event.type });
  }

  const session = event.data.object as Stripe.Checkout.Session;
  const userId = Number(session.metadata?.user_id);
  // No default. There used to be one — `?? "paid"` — and 0020 disabled that plan,
  // which turned a session with no plan in its metadata into a payment that could
  // never be granted: the lookup below finds nothing, the transaction throws, the
  // response is 500, and Stripe retries for three days before giving up. Money taken
  // and nothing given, discovered by accident.
  //
  // Guessing is worse than refusing. Which plan somebody bought is not something to
  // infer from a price, and every session this route creates sets the metadata — so
  // one arriving without it is a bug or a hand-made session, and either needs a
  // person rather than a default.
  const planKey = session.metadata?.plan;
  if (!Number.isFinite(userId) || !planKey) {
    console.error("stripe session missing metadata", {
      session: session.id,
      user_id: session.metadata?.user_id ?? null,
      plan: session.metadata?.plan ?? null,
      // The amount, so the payment can be found and granted by hand from the log
      // alone. Without it this line says a payment went wrong and nothing else.
      amount_pence: session.amount_total,
    });
    // 200, not 500: retrying will not add metadata that was never there. Stripe
    // would spend three days rediscovering that, and the log line would repeat
    // instead of standing out.
    return NextResponse.json({ ok: true, error: "incomplete metadata" });
  }

  try {
    await transaction(async (run) => {
      const plans = await run(
        `SELECT duration_days, price_pence FROM plans WHERE key = $1 AND enabled`,
        [planKey],
      );
      const plan = plans[0];
      if (plan === undefined) throw new Error(`no enabled plan called ${planKey}`);

      // Inserted first. If this is a repeated delivery the unique index rejects it
      // and the transaction rolls back before the plan is touched.
      await run(
        `INSERT INTO payments
                (user_id, plan, amount_pence, provider, provider_ref, granted_days, granted_by)
         VALUES ($1, $2, $3, 'stripe', $4, $5, 'stripe_webhook')`,
        [
          userId,
          planKey,
          session.amount_total ?? plan.price_pence ?? 0,
          session.id,
          plan.duration_days,
        ],
      );
      // Nothing is reset here. 0008 replaced the `expiry_notified_at` flag with
      // `plan_notices`, keyed on the expiry itself, so moving `plan_until` is what
      // makes the day and hour warnings due again for the new period — there is no
      // flag left for this path to forget.
      await run(
        `UPDATE users
            SET plan = $1,
                plan_until = CASE
                    WHEN $2::int IS NULL THEN NULL
                    ELSE greatest(coalesce(plan_until, now()), now())
                         + make_interval(days => $2::int)
                END
          WHERE id = $3`,
        [planKey, plan.duration_days, userId],
      );
      // The link is spent once the payment lands, so the checkout page cannot be
      // reused to pay again by accident.
      await run(`DELETE FROM user_tokens WHERE user_id = $1 AND purpose = 'upgrade'`, [userId]);
    });
  } catch (error) {
    const message = String(error);
    if (message.includes("payments_provider_ref")) {
      // The same payment, delivered again. Nothing to do, and nothing wrong.
      return NextResponse.json({ ok: true, duplicate: true });
    }
    console.error("stripe grant failed", { session: session.id, error: message });
    // 500 so Stripe retries: this one may be a transient database failure, and
    // the payment has been taken.
    return NextResponse.json({ error: "could not grant the plan" }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
