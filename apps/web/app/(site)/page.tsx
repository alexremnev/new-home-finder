import { FURNISHED, PROPERTY_TYPES } from "@/lib/criteria";
import { headers } from "next/headers";

import {
  channelPrices, districtNames, enabledDistricts, returningFor, signupPlan,
  trialDaysOn,
} from "@/lib/plans";
import { recordVisit } from "@/lib/visits";
import { SubscribeForm } from "./form";

export const dynamic = "force-dynamic";

export default async function Page({
  searchParams,
}: {
  // Only for the campaign tags on a link. Reading them here is what lets a
  // post's utm_source be recorded against the visit.
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {

  const codes = await enabledDistricts().catch(() => []);
  const plan = await signupPlan().catch(() => null);

  const names = await districtNames().catch(() => ({}));

  // What each card says. Both numbers come from `plans`, so the page cannot
  // offer a trial the bot will not grant or a price checkout will not charge.
  const [telegramPrices, whatsappPrices] = await Promise.all([
    channelPrices("telegram"),
    channelPrices("whatsapp"),
  ]);
  const offers = {
    telegram: {
      trialDays: plan ? trialDaysOn(plan, "telegram") : null,
      prices: telegramPrices,
    },
    whatsapp: {
      trialDays: plan ? trialDaysOn(plan, "whatsapp") : null,
      prices: whatsappPrices,
    },
  };

  const head = await headers();
  const tags = await searchParams;
  const tag = (name: string): string | null => {
    const value = tags[name];
    return (Array.isArray(value) ? value[0] : value) ?? null;
  };

  await recordVisit({
    address: head.get("x-forwarded-for"),
    userAgent: head.get("user-agent"),
    referer: head.get("referer"),
    language: head.get("accept-language"),
    // These four are set by the edge on Vercel and absent anywhere else. The
    // address itself is deliberately never stored — see 0043.
    country: head.get("x-vercel-ip-country"),
    city: head.get("x-vercel-ip-city"),
    region: head.get("x-vercel-ip-country-region"),
    utmSource: tag("utm_source"),
    campaign: tag("utm_campaign") ?? tag("utm_medium"),
  });

  // `?e=` comes from /update. It says this is somebody changing a filter they
  // already have, so the page drops the sign-up offer: they are on a messenger
  // already, and prices answer a question they did not ask.
  const editing = tag("e");
  const returning = editing ? await returningFor(editing).catch(() => null) : null;

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
        {returning ? (
          <>
            <h1>Change what you are looking for</h1>
            <p className="lede">
              Your current filter is below. Saving replaces it — until you do, it
              carries on exactly as it is.
            </p>
          </>
        ) : (
          <>
            <h1>Beat the London Rental Race. Get Instant Alerts.</h1>
            <p className="lede">
              Never miss a listing on Rightmove, Zoopla, or OpenRent. Set your
              criteria and receive instant notifications directly in your
              preferred messenger.
            </p>
          </>
        )}

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
            returning={returning}
            names={names}
          />
        )}
      </div>
    </>
  );
}
