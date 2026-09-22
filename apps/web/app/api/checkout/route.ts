import { NextResponse } from "next/server";

import { accountForToken, paidPlan, paidPlans, siteUrl } from "@/lib/plans";
import { stripeClient, stripeMode } from "@/lib/stripe";

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

  // The plan key arrives in the query string, so the page offering only this
  // channel's prices is a courtesy rather than a control. Without this check a
  // WhatsApp subscriber could buy the Telegram month by editing the url, and
  // every alert after that is delivered below cost.
  const allowed = await paidPlans(account.channel ?? undefined);
  if (!allowed.some((one) => one.key === plan.key)) {
    return NextResponse.json(
      {
        error:
          `${plan.display_name} is not sold for ${account.channel ?? "this messenger"}. ` +
          `Send /pay to the bot for the prices that apply to you.`,
      },
      { status: 403 },
    );
  }
  if (!plan.stripe_price_id) {
    // Which column is missing depends on the mode, and saying so is the
    // difference between a one-line fix and a hunt.
    const column =
      stripeMode() === "live" ? "stripe_price_id_live" : "stripe_price_id";
    return NextResponse.json(
      {
        error:
          `${plan.display_name} is not set up for card payment in ` +
          `${stripeMode()} mode: plans.${column} is empty for "${plan.key}".`,
      },
      { status: 503 },
    );
  }
  if (!plan.stripe_price_id.startsWith("price_")) {

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

    mode: "payment",
    line_items: [{ price: plan.stripe_price_id, quantity: 1 }],
    success_url: `${siteUrl()}/upgrade/thanks`,
    cancel_url: `${siteUrl()}/upgrade?t=${encodeURIComponent(token)}`,

    client_reference_id: account.payment_ref ?? String(account.user_id),
    metadata: { user_id: String(account.user_id), plan: plan.key },
  });

  if (!session.url) {
    return NextResponse.json({ error: "stripe returned no checkout url" }, { status: 502 });
  }
  return Response.redirect(session.url, 303);
}
