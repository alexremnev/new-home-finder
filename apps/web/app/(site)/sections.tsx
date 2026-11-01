import { pounds } from "@/lib/money";

import type { Offer } from "./form";
import { TelegramMark, Tick, WhatsAppMark } from "./logos";

/**
 * The portals, in one place.
 *
 * Read by the badge over the headline, the marquee, the step that says what is
 * watched and the structured data the crawler is given. Six strings in six
 * files is six places for the seventh portal to be forgotten.
 */
export const PORTALS = [
  "Rightmove",
  "Zoopla",
  "OpenRent",
  "SpareRoom",
  "OnTheMarket",
  "PrimeLocation",
] as const;

/** "a, b and c" — the portals as a sentence rather than as a list. */
export function portalSentence(join = "and"): string {
  return `${PORTALS.slice(0, -1).join(", ")} ${join} ${PORTALS[PORTALS.length - 1]}`;
}

// ── how it works ───────────────────────────────────────────────────────────

const STEPS = [
  {
    title: "Set your criteria",
    text:
      "Pick neighbourhoods or postcodes, rent, bedrooms, size, move-in date, " +
      "furnishing and pets. Everything is optional except the area.",
  },
  {
    title: "We scan six portals",
    text:
      `${portalSentence()} are checked around the clock. ` +
      "Duplicates across portals are merged into one alert.",
  },
  {
    title: "Get it in your messenger",
    text:
      "Photo, price, rooms, size, date and a direct link arrive in WhatsApp " +
      "or Telegram. No app to install, no inbox to refresh.",
  },
  {
    title: "Book the viewing first",
    text:
      "Call the agent while others are still scrolling. Being early is the " +
      "single biggest advantage in the London rental market.",
  },
];

export function HowItWorks() {
  return (
    <section id="how" className="band how">
      <div className="band-grid">
        <div className="how-side">
          <p className="eyebrow">How it works</p>
          <h2>Your search, running while you sleep.</h2>
          <p className="band-lede">
            Good London flats are gone within hours. We watch the portals for
            you and send the listing the moment it matches, so you are the
            first to call the agent.
          </p>

          <div className="stats">
            <div>
              <p className="stat-figure">{PORTALS.length}</p>
              <p className="stat-of">portals watched</p>
            </div>
            <div>
              <p className="stat-figure">24/7</p>
              <p className="stat-of">monitoring</p>
            </div>
            <div>
              <p className="stat-figure">2</p>
              <p className="stat-of">messengers</p>
            </div>
          </div>
        </div>

        <ol className="steps">
          {STEPS.map((step, index) => (
            <li key={step.title}>
              <span className="step-number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <div>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>

      {/* The names, twice over: the strip scrolls by exactly half its width, so
          the second copy is standing where the first one started when the
          animation loops and the join is invisible. Hidden from a screen
          reader on the second pass would still read the first — which is why
          the whole strip is aria-hidden and the badge over the headline is
          what says the same thing in words. */}
      <div className="marquee" aria-hidden="true">
        <div className="marquee-run">
          {[0, 1].map((copy) => (
            <span key={copy} className="marquee-set">
              {PORTALS.map((name) => (
                <span key={name}>
                  {name}
                  <i>/</i>
                </span>
              ))}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── the plans ──────────────────────────────────────────────────────────────

/**
 * What each messenger costs, below the form rather than inside it.
 *
 * Every figure is read from `plans`, never written here: the trial length, the
 * two prices and the messages included at each. A card that stated its own
 * numbers would be a card that one day advertises a price checkout does not
 * charge.
 *
 * The buttons are links back up to the form. They used to be the submit
 * buttons, which meant the plans had to live inside the form and the decision
 * "which messenger" was made twice on one page — once here and once at the
 * bottom of the card. It is made once now, next to the button that acts on it.
 */
export function Plans({
  offers,
  whatsappReady,
}: {
  offers: Record<"telegram" | "whatsapp", Offer>;
  whatsappReady: boolean;
}) {
  return (
    <section id="pricing" className="band plans-band">
      <div className="band-head">
        <div>
          <p className="eyebrow">Pricing</p>
          <h2>Start with a free trial.</h2>
        </div>
        <p className="band-lede">
          Cancel anytime. No credit card required to start.
        </p>
      </div>

      <div className={whatsappReady ? "plan-cards" : "plan-cards plan-cards-one"}>
        <PlanCard
          channel="telegram"
          offer={offers.telegram}
          // Free to deliver on, so it is the one we would rather people used —
          // and saying so is more honest than pricing them towards it quietly.
          flag="Highly recommended"
        />
        {whatsappReady && <PlanCard channel="whatsapp" offer={offers.whatsapp} />}
      </div>
    </section>
  );
}

function PlanCard({
  channel,
  offer,
  flag,
}: {
  channel: "telegram" | "whatsapp";
  offer: Offer;
  flag?: string;
}) {
  const { trialDays, giftDays, prices } = offer;
  const app = channel === "whatsapp" ? "WhatsApp" : "Telegram";
  // What is metered, said per period: the WhatsApp week includes two hundred
  // messages and the month nine hundred, and one figure standing for both
  // would be wrong about one of them. Nothing metered at all is "unlimited",
  // which is every Telegram plan — delivery there is free, so there is nothing
  // to count.
  const metered = prices.filter((price) => price.allowance !== null);

  return (
    <div className={`plan-card plan-card-${channel}`}>
      <div className="plan-card-head">
        <span className="plan-app">
          {channel === "whatsapp" ? <WhatsAppMark /> : <TelegramMark />}
          {app}
        </span>
        {flag && <span className="plan-badge">{flag}</span>}
      </div>

      {prices.length > 0 && (
        <div className="plan-prices">
          {prices.map((price) => (
            <p key={price.unit}>
              <strong>{pounds(price.pence)}</strong>
              <span>/ {price.unit}</span>
            </p>
          ))}
        </div>
      )}

      {/* Under the price and in small type, because it qualifies the figure
          above it rather than being a feature beside it. */}
      <p className="plan-allowance">
        {metered.length === 0
          ? "Unlimited alerts — every match is sent."
          : `Up to ${metered
              .map((price) => `${price.allowance} alerts a ${price.unit}`)
              .join(" · ")}.`}
      </p>

      <ul className="plan-feats">
        {/* Omitted rather than guessed at when no plan is configured: a trial
            this page invented is a promise the bot would not keep. */}
        {trialDays !== null && (
          <li>
            <Tick /> {trialDays} {trialDays === 1 ? "day" : "days"} free trial
          </li>
        )}
        {/* The extra day, as its own claim. It is a day the bot offers at the
            end of a trial that delivered almost nothing, so it belongs next to
            the trial and not inside it: added together they would read as a
            length everybody gets. */}
        {giftDays !== null && (
          <li>
            <Tick /> + {giftDays} extra free{" "}
            {giftDays === 1 ? "day" : "days"} if the first is quiet
          </li>
        )}
        <li>
          <Tick /> Cancel anytime
        </li>
        <li>
          <Tick /> Real-time alerts
        </li>
        <li>
          <Tick /> All {PORTALS.length} portals in one stream
        </li>
      </ul>

      <a href="#start" className={`cta cta-${channel}`}>
        {channel === "whatsapp" ? <WhatsAppMark /> : <TelegramMark />}
        Start free trial
      </a>
    </div>
  );
}

// ── questions ──────────────────────────────────────────────────────────────

/**
 * The three questions the support inbox actually gets, with the answers the
 * bot gives.
 *
 * Exported as data because the page also hands them to a crawler as a
 * `FAQPage`: a second copy written into the structured data is a second copy
 * to keep true, and the one nobody reads is the one that goes stale.
 *
 * `<details>` rather than a toggle of our own — it opens, closes, prints and
 * is reachable by keyboard without a line of JavaScript, and the browser's
 * find-in-page can open it.
 */
export const QUESTIONS = [
  {
    q: "Do I need a credit card for the trial?",
    a:
      "No. The free trial starts with no card details and no commitment. You " +
      "only pay if you decide to keep the alerts running after the trial ends.",
  },
  {
    q: "Can I change my criteria later?",
    a:
      "Yes. Send /update to the bot and it replies with a link that opens this " +
      "form on your current filter — a new area, a lower rent ceiling, " +
      "pet-friendly only. Saving applies it straight away, and there is no " +
      "limit on edits.",
  },
  {
    q: "How quickly do alerts arrive?",
    a:
      "As soon as the listing is published on the portal. New listings are " +
      "read continuously and matched against your filter as they appear, so " +
      "the alert is usually with you within minutes of the advert going live.",
  },
];

export function Faq() {
  return (
    <section id="faq" className="band faq">
      <div className="band-head">
        <div>
          <p className="eyebrow">FAQ</p>
          <h2>Before you start.</h2>
        </div>
        <p className="band-lede">
          Anything else — write to us and a person answers.
        </p>
      </div>

      <div className="faq-list">
        {QUESTIONS.map(({ q, a }) => (
          <details key={q}>
            <summary>
              <span>{q}</span>
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
                <path
                  d="M12 5v14M5 12h14"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </summary>
            <p>{a}</p>
          </details>
        ))}
      </div>
    </section>
  );
}
