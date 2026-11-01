import { FURNISHED, PROPERTY_TYPES } from "@/lib/criteria";
import { headers } from "next/headers";

import {
  botChat, channelPrices, districtNames, enabledDistricts, giftDaysOn,
  returningFor, signupPlan, trialDaysOn, whatsappChat,
} from "@/lib/plans";
import { recordVisit } from "@/lib/visits";
import { SubscribeForm } from "./form";
import { TelegramMark, WhatsAppMark } from "./logos";
import { Faq, HowItWorks, Plans, portalSentence, QUESTIONS } from "./sections";

// The canonical lives here and not in the root layout: metadata is
// inherited, so a canonical up there would have every url on the site —
// /upgrade, /admin/login, the search-param variants — claim to be a copy
// of the landing page.
export const metadata = { alternates: { canonical: "/" } };

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

  // What each card says. Every number comes from `plans`, so the page cannot
  // offer a trial the bot will not grant or a price checkout will not charge.
  //
  // One query per messenger, not two: the messages a plan includes travel with
  // the price they are included at. Both channels are sold by the week and by
  // the month, and the two periods include different numbers of messages — so
  // a single figure per channel could only ever belong to one of the prices
  // beside it.
  const [telegramPrices, whatsappPrices] = await Promise.all([
    channelPrices("telegram"),
    channelPrices("whatsapp"),
  ]);
  const offers = {
    telegram: {
      trialDays: plan ? trialDaysOn(plan, "telegram") : null,
      giftDays: plan ? giftDaysOn(plan, "telegram") : null,
      prices: telegramPrices,
    },
    whatsapp: {
      trialDays: plan ? trialDaysOn(plan, "whatsapp") : null,
      // The day a quiet trial can be given back, said beside the plan's own
      // rather than added to it — see `giftDaysOn`.
      giftDays: plan ? giftDaysOn(plan, "whatsapp") : null,
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

  // `?e=` comes from /update, and from every other link the bots hand out to
  // somebody they recognise. It says this is a person changing a filter they
  // already have, so the page drops the sign-up offer — they are on a messenger
  // already, and prices answer a question they did not ask — and the form opens
  // on their current criteria rather than on the defaults.
  //
  // `?c=` beside it is the messenger the bot handed the link out in, so the one
  // button on the page names the app they are actually writing from rather than
  // whichever of the two the account happens to have as primary.
  const editing = tag("e");
  const returning = editing
    ? await returningFor(editing, tag("c")).catch(() => null)
    : null;

  // A link that carried a token and no longer resolves to anybody. Its own
  // case, because the two outcomes it used to share are opposites: somebody
  // arriving with no `e=` at all is a new visitor and the sign-up page is the
  // right page, while somebody arriving with a dead one is a subscriber whose
  // link went stale — and showing them "Start free trial" above a blank form
  // loses the filter they came to change and reads as though their plan never
  // existed.
  //
  // `c=` is only a hint, which is all that is needed here: it names an app on
  // a button and nothing more. The token itself is past proving anything, and
  // deliberately not looked up — `issueToken` clears dead rows, so most of the
  // time there is nothing behind it to find, and a page that authenticated an
  // expired credential would make the expiry decorative.
  const stale = Boolean(editing) && returning === null;
  const staleChannel = tag("c") === "whatsapp" ? "whatsapp" : "telegram";
  const staleChat = staleChannel === "whatsapp" ? whatsappChat() : botChat();

  // The same test the layout and the form both make: a WhatsApp button on a
  // deployment with no WhatsApp number configured is a button that cannot work.
  const whatsappReady = Boolean(
    process.env.WHATSAPP_NUMBER && process.env.WA_PHONE_NUMBER_ID,
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
          `Watches ${portalSentence()} for London rentals ` +
          "matching your filter and sends each one within minutes.",
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
        // The questions on the page, not a second set written for the crawler:
        // Google's own guidance is that a FAQPage has to be visible on the page
        // it is on, and two copies is one copy to forget to update.
        mainEntity: [
          ...QUESTIONS.map(({ q, a }) => ({
            "@type": "Question",
            name: q,
            acceptedAnswer: { "@type": "Answer", text: a },
          })),
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

      <section id="search" className="hero">
        <div className="hero-inner">
          {stale ? (
            /* Nothing underneath it. A blank sign-up form below this message is
               the trap it exists to close: filling it in opens a second account,
               which the bot then has to merge back on reconnect, and in the
               meantime the person has been told to start a trial they are long
               past. The one thing they need is a working link, and the only
               place that can mint one is the chat. */
            <div className="panel">
              <h1>That link has gone stale</h1>
              <p className="lede">
                Links from the bot stop working after a week, and this one has.
                Nothing has changed — your filter is running exactly as it was,
                and your plan is untouched.
              </p>
              <p className="lede">
                Send <strong>/update</strong> in{" "}
                {staleChannel === "whatsapp" ? "WhatsApp" : "Telegram"} and the
                next link arrives straight away, opening on your current search.
              </p>
              {staleChat && (
                <a
                  href={staleChat}
                  className={
                    staleChannel === "whatsapp"
                      ? "cta cta-whatsapp"
                      : "cta cta-telegram"
                  }
                >
                  {staleChannel === "whatsapp" ? <WhatsAppMark /> : <TelegramMark />}{" "}
                  Open {staleChannel === "whatsapp" ? "WhatsApp" : "Telegram"}
                </a>
              )}
            </div>
          ) : (
            <>
              <div className="hero-say">
                {returning ? (
                  <>
                    <h1>Change what you are looking for</h1>
                    <p className="lede">
                      Your current filter is below. Saving replaces it — until
                      you do, it carries on exactly as it is.
                    </p>
                  </>
                ) : (
                  <>
                    {/* The live dot and the list of portals, which is also the
                        one place the marquee's names are said in words rather
                        than as decoration — the strip itself is hidden from a
                        screen reader. */}
                    <p className="hero-badge">
                      <span className="hero-pulse" aria-hidden="true" />
                      {/* The sentence is its own element rather than a bare
                          text node: as an anonymous flex item it wrapped as a
                          block, which on a phone put the dot on a line of its
                          own above six portal names. */}
                      <span>Watching {portalSentence()}</span>
                    </p>
                    <h1>
                      Beat the London Rental Race.{" "}
                      <span className="hero-grad">Get Instant Alerts.</span>
                    </h1>
                    <p className="lede">
                      Never miss a listing on {portalSentence("or")}. Set your
                      criteria and receive instant notifications directly in
                      your preferred messenger.
                    </p>
                  </>
                )}
              </div>

              {codes.length === 0 ? (
                <p className="panel">
                  No districts are being covered right now, so there is nothing
                  to subscribe to yet.
                </p>
              ) : (
                <SubscribeForm
                  districts={codes}
                  maxDistricts={plan?.max_districts ?? 1}
                  furnished={[...FURNISHED]}
                  types={[...PROPERTY_TYPES]}
                  whatsappReady={whatsappReady}
                  offers={offers}
                  returning={returning}
                  names={names}
                />
              )}
            </>
          )}
        </div>
      </section>

      {/* Only for somebody who has not signed up. A subscriber arriving on an
          `?e=` link came to change one field, and three screens of pitch below
          the form is three screens between them and the save button — the same
          argument the sign-up offer is dropped on. */}
      {!stale && !returning && (
        <>
          <HowItWorks />
          <Plans offers={offers} whatsappReady={whatsappReady} />
          <Faq />
        </>
      )}
    </>
  );
}
