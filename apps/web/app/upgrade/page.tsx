// What you get, what it costs, and how to pay.
//
// Prices and limits are read from the `plans` table, so this page cannot promise
// something different from what the worker enforces. If a plan is edited in the
// database, this page changes with it.
//
// Payment is deliberately a choice of route rather than one hard-wired provider.
// A bank transfer with a reference works from the first day and needs no account
// anywhere; a card checkout is added by setting STRIPE_PRICE_ID, and this page
// then shows the button instead. Neither path can grant a plan by itself — that
// happens in /api/stripe/webhook or by /grant in the bot, both of which write to
// `payments`.

import { accountForToken, paidPlans, siteUrl } from "@/lib/plans";
import { cardPaymentsEnabled } from "@/lib/stripe";

export const dynamic = "force-dynamic";

function money(pence: number): string {
  return pence % 100 === 0 ? `£${pence / 100}` : `£${(pence / 100).toFixed(2)}`;
}

export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ t?: string }>;
}) {
  const token = (await searchParams).t ?? "";
  // The page works without a token — the expiry message links here plainly — but
  // only a token identifies whose plan is being paid for, so the payment
  // reference and the card button appear only with one.
  const account = token ? await accountForToken(token, "upgrade").catch(() => null) : null;
  const plans = await paidPlans().catch(() => []);
  const card = cardPaymentsEnabled() && account !== null;
  const transfer = process.env.PAYMENT_LINK;

  return (
    <main>
      <h1 style={{ fontSize: "1.5rem" }}>More districts</h1>

      {plans.length === 0 ? (
        <p>Nothing is on sale at the moment.</p>
      ) : (
        <ul style={{ paddingLeft: "1.1rem" }}>
          {plans.map((plan) => (
            <li key={plan.key} style={{ marginBottom: "0.5rem" }}>
              <strong>{plan.display_name}</strong> — {money(plan.price_pence)}
              {plan.duration_days ? ` for ${plan.duration_days} days` : ""}: up to{" "}
              {plan.max_districts} districts.
            </li>
          ))}
        </ul>
      )}

      <h2 style={{ fontSize: "1.1rem", marginTop: "1.5rem" }}>How to pay</h2>

      {card && (
        <p>
          <a
            href={`/api/checkout?t=${encodeURIComponent(token)}`}
            style={{
              display: "inline-block",
              padding: "0.6rem 1.1rem",
              background: "#0a7",
              color: "#fff",
              borderRadius: 5,
              textDecoration: "none",
            }}
          >
            Pay by card
          </a>
        </p>
      )}

      {transfer ? (
        <p>
          {card ? "Or send" : "Send"} the amount to <a href={transfer}>{transfer}</a> and{" "}
          <strong>put your reference in the message</strong>:{" "}
          {account?.payment_ref ? (
            <code style={{ background: "#f2f2f2", padding: "0.1rem 0.3rem" }}>
              {account.payment_ref}
            </code>
          ) : (
            <>
              send <em>/upgrade</em> to the bot to see yours
            </>
          )}
          . Without it there is no way to tell whose account to extend, and the plan has
          to be granted by hand either way.
        </p>
      ) : (
        !card && (
          <p>
            Payment is not set up yet. Send <em>/upgrade</em> to the bot and you will be
            told how to pay.
          </p>
        )
      )}

      {!account && (
        <p style={{ color: "#777", fontSize: "0.85rem" }}>
          Send <em>/upgrade</em> to the bot to get a link that knows which account is
          yours.
        </p>
      )}

      <p style={{ color: "#777", fontSize: "0.85rem", marginTop: "1.5rem" }}>
        Every listing that matches your filter is sent, on every plan, from the run that
        found it — a paid plan covers more districts and lasts longer, it is not a faster
        queue or a bigger allowance. Your filter is kept when a plan ends,
        so renewing turns the alerts back on with nothing to set up again.
      </p>
      <p style={{ fontSize: "0.85rem" }}>
        <a href={siteUrl()}>Back</a>
      </p>
    </main>
  );
}
