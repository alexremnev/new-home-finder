// Start a card payment.
//
// The token in the URL is how this route knows whose plan to extend. Without it
// there is no way to connect a card payment to an account, and a payment that
// cannot be attributed is a refund waiting to happen.

import { NextResponse } from "next/server";

import { accountForToken, siteUrl } from "@/lib/plans";
import { cardPaymentsEnabled, stripeClient } from "@/lib/stripe";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<NextResponse | Response> {
  if (!cardPaymentsEnabled()) {
    return NextResponse.json({ error: "card payments are not set up" }, { status: 503 });
  }
  const url = new URL(request.url);
  const token = url.searchParams.get("t") ?? "";
  const account = token ? await accountForToken(token, "upgrade") : null;
  if (!account) {
    return NextResponse.json(
      { error: "this link has expired; send /upgrade to the bot for a new one" },
      { status: 404 },
    );
  }

  const stripe = stripeClient();
  if (!stripe) return NextResponse.json({ error: "not configured" }, { status: 503 });

  const session = await stripe.checkout.sessions.create({
    mode: "payment",
    line_items: [{ price: process.env.STRIPE_PRICE_ID!, quantity: 1 }],
    success_url: `${siteUrl()}/upgrade/thanks`,
    cancel_url: `${siteUrl()}/upgrade?t=${token}`,
    // Both, on purpose. `client_reference_id` is what shows up in the Stripe
    // dashboard next to the payment, and the metadata is what the webhook reads.
    client_reference_id: account.payment_ref ?? String(account.user_id),
    metadata: { user_id: String(account.user_id), plan: process.env.STRIPE_PLAN_KEY ?? "paid" },
  });

  if (!session.url) {
    return NextResponse.json({ error: "stripe returned no checkout url" }, { status: 502 });
  }
  return Response.redirect(session.url, 303);
}
