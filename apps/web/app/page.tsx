// The landing page and the form.
//
// The district list comes from the database rather than from a constant, so the
// page can only offer what is actually being collected. Offering a district the
// worker does not visit produces a subscription that waits for ever, and the
// person has no way to tell the difference between "nothing matched" and "nothing
// was ever looked at".

import { FURNISHED, PROPERTY_TYPES } from "@/lib/criteria";
import { districtNames, enabledDistricts, signupPlan } from "@/lib/plans";
import { SubscribeForm } from "./form";

export const dynamic = "force-dynamic";

export default async function Page() {
  // The page still renders and says so if the database is unreachable, rather
  // than showing a stack trace to someone who only wanted to sign up.
  const codes = await enabledDistricts().catch(() => []);
  const plan = await signupPlan().catch(() => null);
  // Empty on failure rather than fatal: without it the form still takes postcodes,
  // and a page that renders without name lookup beats a page that does not render.
  const names = await districtNames().catch(() => ({}));

  return (
    <main>
      <h1 style={{ fontSize: "1.6rem", marginBottom: "0.25rem" }}>London Rent Alerts</h1>
      <p style={{ color: "#555", marginTop: 0 }}>
        New rental listings in Telegram, minutes after they appear. Only what comes on the
        market from the moment you subscribe — never a backlog.
      </p>

      {codes.length === 0 ? (
        <p style={{ background: "#fff4f4", padding: "0.75rem", borderRadius: 6 }}>
          No districts are being covered right now, so there is nothing to subscribe to yet.
        </p>
      ) : (
        <>
          {plan && (
            <p style={{ background: "#f5fbf8", padding: "0.7rem", borderRadius: 6 }}>
              <strong>{plan.display_name}</strong>: {plan.max_districts} district
              {plan.max_districts === 1 ? "" : "s"}
              {plan.duration_days ? `, ${plan.duration_days} days` : ""}. Every matching
              listing is sent — <a href="/upgrade">more districts</a>.
            </p>
          )}
          <SubscribeForm
            districts={codes}
            maxDistricts={plan?.max_districts ?? 1}
            propertyTypes={[...PROPERTY_TYPES]}
            furnished={[...FURNISHED]}
            names={names}
          />
        </>
      )}

      <p style={{ color: "#777", fontSize: "0.85rem", marginTop: "2rem" }}>
        Sending <em>/stop</em> to the bot deletes your filter and stops the messages
        immediately. We store the criteria you choose and your Telegram chat id, nothing
        else.
      </p>
    </main>
  );
}
