import { FURNISHED, PROPERTY_TYPES } from "@/lib/criteria";
import { headers } from "next/headers";

import { districtNames, enabledDistricts, signupPlan } from "@/lib/plans";
import { recordVisit } from "@/lib/visits";
import { SubscribeForm } from "./form";

export const dynamic = "force-dynamic";

export default async function Page() {

  const codes = await enabledDistricts().catch(() => []);
  const plan = await signupPlan().catch(() => null);

  const names = await districtNames().catch(() => ({}));

  const head = await headers();
  await recordVisit(head.get("x-forwarded-for"), head.get("user-agent"));

  return (
    <>

      <h1>Let&apos;s Find Your Perfect Home!</h1>
      <p className="lede">
        👋 Welcome to London Home Finder. Tell us a little about what you&apos;re
        looking for, and we&apos;ll match you with the right properties.
      </p>

      {codes.length === 0 ? (
        <p className="panel">
          No districts are being covered right now, so there is nothing to subscribe
          to yet.
        </p>
      ) : (
        <SubscribeForm
          districts={codes}
          maxDistricts={plan?.max_districts ?? 1}
          propertyTypes={[...PROPERTY_TYPES]}
          furnished={[...FURNISHED]}
          names={names}
        />
      )}

      <p className="footnote">
        Sending <em>/stop</em> to the bot deletes your filter and stops the messages
        immediately. We store the criteria you choose and your Telegram chat id,
        nothing else.
      </p>
    </>
  );
}
