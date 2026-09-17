import { FURNISHED } from "@/lib/criteria";
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

      <h1>Every new London rental, minutes after it lists.</h1>
      <p className="lede">
        Near real-time notifications from <strong>Zoopla</strong>,{" "}
        <strong>Rightmove</strong> and <strong>OpenRent</strong> — one stream, no
        three tabs. Tell us what you are looking for and every match arrives in
        Telegram or WhatsApp within minutes of going live.
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
          furnished={[...FURNISHED]}
          whatsappReady={Boolean(
            process.env.WHATSAPP_NUMBER && process.env.WA_PHONE_NUMBER_ID,
          )}
          names={names}
        />
      )}

      <p className="footnote">
        We keep the criteria you choose and the one address we send to — a Telegram
        chat id or a phone number. Nothing else.
      </p>
    </>
  );
}
