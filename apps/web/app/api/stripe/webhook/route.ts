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

    event = stripe.webhooks.constructEvent(await request.text(), signature, secret);
  } catch (error) {
    return NextResponse.json({ error: `bad signature: ${String(error)}` }, { status: 400 });
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
    });
  } catch (error) {
    const message = String(error);
    if (message.includes("payments_provider_ref")) {

      return NextResponse.json({ ok: true, duplicate: true });
    }
    console.error("stripe grant failed", { session: session.id, error: message });

    return NextResponse.json({ error: "could not grant the plan" }, { status: 500 });
  }

  return NextResponse.json({ ok: true });
}
