// Start a card payment.
//
// ── what identifies the buyer, and what identifies the purchase ─────────────
//
// The token says who. Without it a payment cannot be attributed to an account, and
// an unattributable payment is a refund waiting to happen.
//
// The `plan` parameter says what — and it is a key looked up in the database, never
// a price sent to Stripe. Anything arriving in a URL is somebody's suggestion: a
// route that took an amount from the query string would sell a month for a penny to
// the first person who tried it.

import { NextResponse } from "next/server";

import { accountForToken, paidPlan, siteUrl } from "@/lib/plans";
import { stripeClient } from "@/lib/stripe";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<NextResponse | Response> {
  const url = new URL(request.url);
  const token = url.searchParams.get("t") ?? "";
  const wanted = url.searchParams.get("plan") ?? "";

  const account = token ? await accountForToken(token, "upgrade") : null;
  if (!account) {
    return NextResponse.json(
      { error: "this link has expired; send /pay to the bot for a new one" },
      { status: 404 },
    );
  }

  const plan = wanted ? await paidPlan(wanted) : null;
  if (!plan) {
    return NextResponse.json({ error: "no such plan" }, { status: 404 });
  }
  if (!plan.stripe_price_id) {
    // A plan with no Stripe price is a plan somebody added to the table and has not
    // finished setting up. Said plainly, because the alternative is a Stripe error
    // page that blames the buyer.
    return NextResponse.json(
      { error: `${plan.display_name} is not set up for card payment yet` },
      { status: 503 },
    );
  }
  if (!plan.stripe_price_id.startsWith("price_")) {
    // Almost always a Product id where a Price id belongs. They are different
    // objects — a product holds one or more prices — and the dashboard puts the
    // product's id in the more prominent place, so `prod_…` is the easy thing to
    // copy. Stripe's own answer to it is "No such price", which names the symptom
    // and not the mistake.
    //
    // Checked here rather than by a constraint on the column: a shape rule in the
    // database would be a second place to update if Stripe ever changes its
    // prefixes, and this is the one place the value is used.
    return NextResponse.json(
      {
        error:
          `${plan.display_name} has "${plan.stripe_price_id}" where a Price id ` +
          `belongs. A Price id starts with "price_"; "prod_" is the Product that ` +
          `holds it. Open the product in Stripe, find the row in its Pricing table, ` +
          `and copy that id.`,
      },
      { status: 503 },
    );
  }

  const stripe = stripeClient();
  if (!stripe) {
    return NextResponse.json({ error: "card payments are not set up" }, { status: 503 });
  }

  const session = await stripe.checkout.sessions.create({
    // `payment`, not `subscription`: this sells a fixed period that has to be bought
    // again, which is what the plan means and what `plan_until` records. A Stripe
    // subscription would put the renewal schedule in two places — theirs and ours —
    // and the two would disagree the first time a card was declined.
    mode: "payment",
    line_items: [{ price: plan.stripe_price_id, quantity: 1 }],
    success_url: `${siteUrl()}/upgrade/thanks`,
    cancel_url: `${siteUrl()}/upgrade?t=${encodeURIComponent(token)}`,
    // Both, on purpose. `client_reference_id` is what appears in the Stripe
    // dashboard beside the payment; the metadata is what the webhook reads back.
    client_reference_id: account.payment_ref ?? String(account.user_id),
    metadata: { user_id: String(account.user_id), plan: plan.key },
  });

  if (!session.url) {
    return NextResponse.json({ error: "stripe returned no checkout url" }, { status: 502 });
  }
  return Response.redirect(session.url, 303);
}
