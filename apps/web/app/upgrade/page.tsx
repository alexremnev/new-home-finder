import { accountForToken, lapsedShare, paidPlans } from "@/lib/plans";

export const dynamic = "force-dynamic";

const money = (pence: number) =>
  "£" + (pence / 100).toLocaleString("en-GB", { minimumFractionDigits: 0 });

const perDay = (pence: number, days: number | null) =>
  days && days > 0 ? `${money(Math.round(pence / days))} a day` : null;

export default async function UpgradePage({
  searchParams,
}: {
  searchParams: Promise<{ t?: string }>;
}) {
  const { t } = await searchParams;
  const token = t ?? "";
  const account = token ? await accountForToken(token, "upgrade").catch(() => null) : null;
  const plans = await paidPlans().catch(() => []);
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
          const rate = perDay(plan.price_pence, plan.duration_days);
          return (
            <div
              key={plan.key}
              className={plan.key === best.key ? "plan plan-best" : "plan"}
            >
              {plan.key === best.key && <span className="plan-flag">Better value</span>}
              <h2>{plan.display_name}</h2>
              <div className="plan-price">{money(plan.price_pence)}</div>
              {rate && <p className="hint">{rate}</p>}
              <ul className="plan-points">
                <li>
                  {share === null
                    ? "Every matching listing, not a share"
                    : `Every matching listing, not ${share}%`}
                </li>
                <li>Up to {plan.max_districts} areas</li>
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
