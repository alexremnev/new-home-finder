import { pounds } from "@/lib/money";
import { accountForToken, lapsedShare, paidPlans } from "@/lib/plans";

export const dynamic = "force-dynamic";


export default async function UpgradePage({
  searchParams,
}: {
  searchParams: Promise<{ t?: string }>;
}) {
  const { t } = await searchParams;
  const token = t ?? "";
  const account = token ? await accountForToken(token, "upgrade").catch(() => null) : null;
  // Only what their messenger is priced at. WhatsApp costs us per message, so
  // offering the Telegram month here would be selling delivery below cost.
  const plans = account
    ? await paidPlans(account.channel ?? undefined).catch(() => [])
    : [];
  const share = await lapsedShare().catch(() => null);

  if (!account) {
    return (
      <div className="panel">
        <h1>That link has expired</h1>
        <p className="lede">
          Upgrade links are short lived on purpose — one left in a chat should stop
          working. Send <strong>/pay</strong> to the bot for a new one.
        </p>
      </div>
    );
  }

  if (plans.length === 0) {
    return (
      <div className="panel">
        <h1>Nothing to buy yet</h1>
        <p className="lede">No paid plan is set up. Nothing has been charged.</p>
      </div>
    );
  }

  const best = plans.reduce((a, b) =>
    (a.duration_days ?? 0) >= (b.duration_days ?? 0) ? a : b,
  );

  return (
    <>
      <h1>Every listing, the moment it appears</h1>
      <p className="lede">
        The free plan sends {share === null ? "a share" : `${share}%`} of what matches
        your filter. A paid plan sends all of it — same filter, nothing else to set up.
      </p>

      <div className="plans">
        {plans.map((plan) => {
          return (
            <div
              key={plan.key}
              className={plan.key === best.key ? "plan plan-best" : "plan"}
            >
              {plan.key === best.key && <span className="plan-flag">Better value</span>}
              <h2>{plan.display_name}</h2>
              <div className="plan-price">{pounds(plan.price_pence)}</div>
              <ul className="plan-points">
                <li>24/7</li>
                <li>
                  {plan.duration_days
                    ? `${plan.duration_days} days from today`
                    : "No end date"}
                </li>
              </ul>
              <a
                className="cta"
                href={`/api/checkout?t=${encodeURIComponent(token)}&plan=${encodeURIComponent(plan.key)}`}
              >
                Pay by card
              </a>
            </div>
          );
        })}
      </div>

      <p className="footnote">
        One payment for one period — nothing recurring, and no card kept on file by
        us. Payment is handled by Stripe; the card never touches this server. When it
        ends the alerts drop back to {share === null ? "the free share" : `${share}%`}{" "}
        rather than stopping, and your filter is kept either way.
      </p>
    </>
  );
}
