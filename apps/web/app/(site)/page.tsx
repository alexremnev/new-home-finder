import { FURNISHED, PROPERTY_TYPES } from "@/lib/criteria";
import { headers } from "next/headers";

import {
  districtNames, enabledDistricts, entryPrice, signupPlan, trialDaysOn,
} from "@/lib/plans";
import { recordVisit } from "@/lib/visits";
import { SubscribeForm } from "./form";

export const dynamic = "force-dynamic";

export default async function Page() {

  const codes = await enabledDistricts().catch(() => []);
  const plan = await signupPlan().catch(() => null);

  const names = await districtNames().catch(() => ({}));

  // What each card says. Both numbers come from `plans`, so the page cannot
  // offer a trial the bot will not grant or a price checkout will not charge.
  const whatsappPrice = await entryPrice("whatsapp");
  const offers = {
    telegram: {
      trialDays: plan ? trialDaysOn(plan, "telegram") : null,
      // No price on the Telegram card: the cheapest Telegram plan is a week, so
      // "per month" would be the wrong unit, and a second unit on the other
      // card is one more thing to compare while choosing a messenger.
      pricePence: null as number | null,
    },
    whatsapp: {
      trialDays: plan ? trialDaysOn(plan, "whatsapp") : null,
      pricePence: whatsappPrice?.price_pence ?? null,
    },
  };

  const head = await headers();
  await recordVisit(
    head.get("x-forwarded-for"),
    head.get("user-agent"),
    // Set by the edge on Vercel; absent anywhere else.
    head.get("x-vercel-ip-country"),
  );

  const site = (process.env.SITE_URL ?? "https://londonhomefinder.co.uk").replace(
    /\/+$/,
    "",
  );

  // Told to a crawler in the shape it reads: what this is, who runs it, what it
  // costs. The free trial is declared as an Offer because "free to try" in prose
  // is invisible to a search engine.
  const structured = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${site}/#website`,
        url: `${site}/`,
        name: "London Home Finder",
        inLanguage: "en-GB",
        description:
          "New London rental listings delivered to Telegram or WhatsApp minutes " +
          "after they appear.",
      },
      {
        "@type": "SoftwareApplication",
        "@id": `${site}/#app`,
        name: "London Home Finder",
        applicationCategory: "BusinessApplication",
        operatingSystem: "Telegram, WhatsApp",
        url: `${site}/`,
        description:
          "Watches Rightmove, Zoopla and OpenRent for London rentals matching your " +
          "filter and sends each one within minutes.",
        areaServed: { "@type": "City", name: "London", addressCountry: "GB" },
        offers: {
          "@type": "Offer",
          price: "0",
          priceCurrency: "GBP",
          description: "Free trial, then a paid plan for every match.",
        },
      },
      {
        "@type": "FAQPage",
        "@id": `${site}/#faq`,
        mainEntity: [
          {
            "@type": "Question",
            name: "How quickly do alerts arrive?",
            acceptedAnswer: {
              "@type": "Answer",
              text:
                "Minutes. New listings are read continuously and matched against " +
                "your filter as they appear.",
            },
          },
          {
            "@type": "Question",
            name: "Which sites does it cover?",
            acceptedAnswer: {
              "@type": "Answer",
              text: "Rightmove, Zoopla and OpenRent, in one stream of alerts.",
            },
          },
          {
            "@type": "Question",
            name: "How do I stop the alerts?",
            acceptedAnswer: {
              "@type": "Answer",
              text:
                "Send /stop to the bot in Telegram or WhatsApp. The filter is " +
                "deleted and the messages end immediately.",
            },
          },
        ],
      },
    ],
  };

  return (
    <>
      <script
        type="application/ld+json"
        // Serialised by us from a literal above, so there is nothing of anybody
        // else's in it.
        dangerouslySetInnerHTML={{ __html: JSON.stringify(structured) }}
      />

      <div className="hero">
        <h1>Beat the London Rental Race. Get Instant Alerts.</h1>
        <p className="lede">
          Never miss a listing on Rightmove, Zoopla, or OpenRent. Set your
          criteria and receive instant notifications directly in your preferred
          messenger.
        </p>

        {codes.length === 0 ? (
          <p className="panel">
            No districts are being covered right now, so there is nothing to
            subscribe to yet.
          </p>
        ) : (
          <SubscribeForm
            districts={codes}
            maxDistricts={plan?.max_districts ?? 1}
            furnished={[...FURNISHED]}
            types={[...PROPERTY_TYPES]}
            whatsappReady={Boolean(
              process.env.WHATSAPP_NUMBER && process.env.WA_PHONE_NUMBER_ID,
            )}
            offers={offers}
            names={names}
          />
        )}
      </div>
    </>
  );
}
