// The landing page and the form.
//
// The district list comes from the database rather than from a constant, so the
// page can only offer what is actually being collected. Offering a district the
// worker does not visit produces a subscription that waits for ever, and the
// person has no way to tell the difference between "nothing matched" and "nothing
// was ever looked at".

import { FURNISHED, PROPERTY_TYPES } from "@/lib/criteria";
import { headers } from "next/headers";

import { districtNames, enabledDistricts, signupPlan } from "@/lib/plans";
import { recordVisit } from "@/lib/visits";
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

  // Counted here rather than by a script in the browser: a beacon is blocked for a
  // good share of visitors, and the count matters most for exactly the people who
  // block it. Awaited but never allowed to fail — see `recordVisit`.
  const head = await headers();
  await recordVisit(head.get("x-forwarded-for"), head.get("user-agent"));

  return (
    <>
      {/* The heading is the invitation and the lede is the whole pitch. What used to
          sit here — the service name, a sentence about Telegram, and a box
          describing the trial's district allowance — was three blocks of
          explanation in front of somebody who had already decided to try it. The
          plan's limit is not hidden: it is stated on the field it applies to, where
          it is an answer rather than a warning. */}
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
