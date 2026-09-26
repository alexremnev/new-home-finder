import { NextResponse } from "next/server";
import type Stripe from "stripe";

import { transaction } from "@/lib/db";
import { stripeClient, stripeMode, stripeWebhookSecret } from "@/lib/stripe";

import { paymentReceived, refundIssued } from "@/lib/messages";
import { tell } from "@/lib/reach";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<NextResponse> {
  const stripe = stripeClient();
  // The secret of the endpoint in whichever account STRIPE_MODE names. Both
  // functions log which mode they were in and what was missing, so the usual
  // mistake — the flag flipped and the live endpoint not added yet — says so
  // instead of looking like a bad signature.
  const secret = stripeWebhookSecret();
  if (!stripe || !secret) {
    return NextResponse.json(
      { error: "not configured", mode: stripeMode() },
      { status: 503 },
    );
  }

  const signature = request.headers.get("stripe-signature");
  if (!signature) return NextResponse.json({ error: "unsigned" }, { status: 400 });

  let event: Stripe.Event;
  try {

    event = stripe.webhooks.constructEvent(await request.text(), signature, secret);
  } catch (error) {
    // Naming the mode here because a signature that never matches is almost
    // always the other account's endpoint calling this one.
    console.error("stripe webhook rejected", { mode: stripeMode(), error: String(error) });
    return NextResponse.json({ error: `bad signature: ${String(error)}` }, { status: 400 });
  }

  if (event.type === "charge.refunded") {
    return refunded(stripe, event.data.object as Stripe.Charge);
  }

  if (event.type !== "checkout.session.completed") {

    return NextResponse.json({ ok: true, ignored: event.type });
  }

  const session = event.data.object as Stripe.Checkout.Session;
  const userId = Number(session.metadata?.user_id);

  const planKey = session.metadata?.plan;
  if (!Number.isFinite(userId) || !planKey) {
    console.error("stripe session missing metadata", {
      session: session.id,
      user_id: session.metadata?.user_id ?? null,
      plan: session.metadata?.plan ?? null,

      amount_pence: session.amount_total,
    });

    return NextResponse.json({ ok: true, error: "incomplete metadata" });
  }

  let told: { plan: string; until: Date | null } | null = null;

  try {
    await transaction(async (run) => {
      const plans = await run(
        `SELECT duration_days, price_pence FROM plans WHERE key = $1 AND enabled`,
        [planKey],
      );
      const plan = plans[0];
      if (plan === undefined) throw new Error(`no enabled plan called ${planKey}`);

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

      await run(`DELETE FROM user_tokens WHERE user_id = $1 AND purpose = 'upgrade'`, [userId]);

      const now = await run(
        `SELECT u.plan_until, coalesce(p.display_name, u.plan) AS plan_name
           FROM users u LEFT JOIN plans p ON p.key = u.plan
          WHERE u.id = $1`,
        [userId],
      );
      told = {
        plan: String(now[0]?.plan_name ?? planKey),
        until: (now[0]?.plan_until as Date | null) ?? null,
      };
    });
  } catch (error) {
    const message = String(error);
    if (message.includes("payments_provider_ref")) {

      return NextResponse.json({ ok: true, duplicate: true });
    }
    console.error("stripe grant failed", { session: session.id, error: message });

    return NextResponse.json({ error: "could not grant the plan" }, { status: 500 });
  }

  // After the transaction, never inside it: a chat app being slow or down must
  // not roll back a payment that has already cleared.
  if (told) {
    const said: { plan: string; until: Date | null } = told;
    await tell(userId, paymentReceived(said.plan, said.until)).catch((error) => {
      console.error("payment granted but not announced", {
        user: userId,
        error: String(error),
      });
      return false;
    });
  }

  return NextResponse.json({ ok: true });
}

/**
 * A refund made in the Stripe dashboard.
 *
 * Before this the event was ignored, so a refund took the money back and left
 * the subscriber with their access and the admin's Refunds count at nought —
 * true nowhere except in Stripe.
 *
 * Three things happen: the refund is recorded as a negative payment, the plan
 * is shortened by the time it bought, and the person is told. Partial refunds
 * are honoured in proportion — half the money back takes half the days.
 */
async function refunded(
  stripe: Stripe,
  charge: Stripe.Charge,
): Promise<NextResponse> {
  const back = charge.amount_refunded ?? 0;
  if (back <= 0) return NextResponse.json({ ok: true, nothing_refunded: true });

  // The payment row keys on the Checkout Session id, and a charge only knows
  // its PaymentIntent — so the session has to be looked up to get back to it.
  // Done through the API rather than by storing another column, because that
  // also works for every payment taken before this handler existed.
  const intent =
    typeof charge.payment_intent === "string"
      ? charge.payment_intent
      : charge.payment_intent?.id;
  if (!intent) {
    console.error("refund without a payment intent", { charge: charge.id });
    return NextResponse.json({ ok: true, error: "no payment intent" });
  }

  let sessionId: string | null = null;
  try {
    const found = await stripe.checkout.sessions.list({ payment_intent: intent, limit: 1 });
    sessionId = found.data[0]?.id ?? null;
  } catch (error) {
    console.error("refund could not find its session", {
      charge: charge.id,
      error: String(error),
    });
    return NextResponse.json({ error: "could not resolve the session" }, { status: 500 });
  }

  if (!sessionId) {
    console.error("refund has no checkout session", { charge: charge.id, intent });
    return NextResponse.json({ ok: true, error: "no session for this charge" });
  }

  // Keyed on the charge rather than the refund, so a second `charge.refunded`
  // for the same charge — Stripe re-sends, and a second partial refund fires it
  // again — cannot take the days away twice. The cost is that a later top-up
  // refund on one charge is not applied; the admin's figures show the first,
  // and the note below says what to look at.
  const ref = `refund:${charge.id}`;

  let told: { amount: number; until: Date | null } | null = null;
  let userId: number | null = null;

  try {
    await transaction(async (run) => {
      const original = await run(
        `SELECT id, user_id, plan, amount_pence, granted_days
           FROM payments
          WHERE provider = 'stripe' AND provider_ref = $1`,
        [sessionId],
      );
      const paid = original[0];
      if (paid === undefined) {
        throw new Error(`no payment recorded for session ${sessionId}`);
      }

      userId = Number(paid.user_id);
      const charged = Number(paid.amount_pence) || 0;
      const granted = Number(paid.granted_days) || 0;

      // In proportion, so a partial refund takes a partial period. Rounded, and
      // never more than was granted.
      const share = charged > 0 ? Math.min(1, back / charged) : 1;
      const daysBack = Math.min(granted, Math.round(granted * share));

      await run(
        `INSERT INTO payments
                (user_id, plan, amount_pence, provider, provider_ref, granted_days, granted_by)
         VALUES ($1, $2, $3, 'stripe', $4, $5, 'stripe_refund')`,
        [userId, String(paid.plan), -back, ref, -daysBack],
      );

      // Shortened by what the refund took back. `greatest` with now() means a
      // plan whose remaining time is less than that simply ends now, rather
      // than being stamped with a date in the past.
      const after = await run(
        `UPDATE users
            SET plan_until = CASE
                    WHEN plan_until IS NULL THEN NULL
                    ELSE greatest(now(), plan_until - make_interval(days => $1::int))
                END
          WHERE id = $2
        RETURNING plan_until`,
        [daysBack, userId],
      );

      told = {
        amount: back,
        until: (after[0]?.plan_until as Date | null) ?? null,
      };
    });
  } catch (error) {
    const message = String(error);
    if (message.includes("payments_provider_ref")) {

      return NextResponse.json({ ok: true, duplicate: true });
    }
    console.error("refund not recorded", { charge: charge.id, error: message });

    return NextResponse.json({ error: "could not record the refund" }, { status: 500 });
  }

  // After the transaction, for the same reason as a payment: a chat app being
  // slow must not roll back a refund Stripe has already made.
  if (told && userId !== null) {
    const said: { amount: number; until: Date | null } = told;
    await tell(userId, refundIssued(said.amount, said.until)).catch((error) => {
      console.error("refund recorded but not announced", {
        user: userId,
        error: String(error),
      });
      return false;
    });
  }

  return NextResponse.json({ ok: true, refunded: back });
}
