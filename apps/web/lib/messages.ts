import type { Criteria } from "./criteria";
import type { Account } from "./plans";
import { lapsedShare, paidPlans, siteUrl } from "./plans";

export function alreadyOnAnotherChannel(channel: string): string {
  const other = channel === "telegram" ? "Telegram" : "WhatsApp";
  return [
    `This search already sends its alerts to ${other}.`,
    "",
    "One search goes to one app, so that a listing never arrives twice. If you",
    "want alerts here as well, set up a second search — it can use the same",
    "filter or a different one.",
  ].join("\n");
}

const MONTHS = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function prettyDay(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  const name = month === undefined ? undefined : MONTHS[month - 1];
  return year && day && name ? `${day} ${name} ${year}` : iso;
}

function pounds(amount: number): string {
  return "£" + amount.toLocaleString("en-GB");
}

// A studio has no bedrooms, and "0" reads like a mistake rather than a choice.
const bedroom = (count: number) => (count === 0 ? "studio" : String(count));

// The words the form shows, not the words stored: "room" on its own reads as a
// bedroom count rather than as what it is.
const TYPE_NAMES: Record<string, string> = {
  flat: "flat",
  house: "house",
  studio: "studio",
  room: "room in a shared flat",
};

const typeName = (key: string) => TYPE_NAMES[key] ?? key;

function span(
  value: { min?: number; max?: number } | undefined,
  render: (n: number) => string,
): string | null {
  const min = value?.min;
  const max = value?.max;
  if (min === undefined && max === undefined) return null;
  if (min !== undefined && max !== undefined) {
    return min === max ? render(min) : `${render(min)}\u2013${render(max)}`;
  }
  if (max !== undefined) return `up to ${render(max)}`;
  return `${render(min as number)} or more`;
}

// The criteria as the person chose them, one line each. Separate from
// `describeCriteria`, which the admin uses: a table of accounts wants the facts
// dense and unadorned, a message to one person wants them readable.
export function criteriaCard(criteria: Criteria): string {
  const lines: string[] = [];

  const areas = criteria.areas?.postcode_districts ?? [];
  lines.push(`📍 Areas: ${areas.length ? areas.join(", ") : "everywhere covered"}`);

  const rent = span(criteria.price_pcm, pounds);
  if (rent) lines.push(`💷 Rent: ${rent} a month`);

  const bedrooms = span(criteria.bedrooms, bedroom);
  if (bedrooms) lines.push(`🛏 Bedrooms: ${bedrooms}`);

  const bathrooms = span(criteria.bathrooms, String);
  if (bathrooms) lines.push(`🛁 Bathrooms: ${bathrooms}`);

  if (criteria.property_types?.length) {
    lines.push(`🏘 Type: ${criteria.property_types.map(typeName).join(", ")}`);
  }

  if (criteria.furnished?.length) {
    lines.push(`🛋 Furnishing: ${criteria.furnished.join(", ")}`);
  }

  const after = criteria.available_from?.after;
  const before = criteria.available_from?.before;
  if (after || before) {
    const when =
      after && before
        ? `${prettyDay(after)} \u2013 ${prettyDay(before)}`
        : after
          ? `from ${prettyDay(after)}`
          : `by ${prettyDay(before as string)}`;
    lines.push(`📅 Available: ${when}`);
  }

  if (criteria.pets_allowed) lines.push("🐾 Pets must be allowed");

  return lines.join("\n");
}

// The same message whether this is a first filter or a replacement. Saving the
// form sets backfill_from to now either way, so the closing sentence is equally
// true of both, and a person who has just changed their criteria wants to read
// the criteria rather than be told that they changed them.
export function criteriaSet(criteria: Criteria): string {
  return [
    "✅ Your search criteria are set",
    "",
    criteriaCard(criteria),
    "",
    "I'll message you as soon as a new listing matches. Nothing arrives for listings",
    "that were already on the market — only what appears from now on.",
    "",
    "/current — show this again",
    "/stop — delete my filter and stop",
  ].join("\n");
}

export const LINK_EXPIRED = [
  "That link has expired. Please fill the form again and use the new link —",
  "it takes a moment.",
  "",
  process.env.SITE_URL ?? "https://londonhomefinder.co.uk",
].join("\n");

export const STOPPED = [
  "Done. Your filter is deleted and no further messages will be sent.",
  "",
  "Anything already queued has been discarded, so nothing will arrive after this.",
  "You can subscribe again any time.",
].join("\n");

export const NOTHING_TO_STOP = "You have no active filter, so there is nothing to stop.";

export const SET_FILTERS = "Set up your search criteria";

export const CHANGE_FILTER = "\u270f\ufe0f Change my filter";

export const FILTERS_BUTTON = "\ud83c\udfaf Set Filters";

export const PAUSED = [
  "Paused. No further alerts will be sent.",
  "",
  "Your filter is kept exactly as it is — /resume turns the alerts back on with",
  "nothing to set up again.",
].join("\n");

export const NOTHING_TO_PAUSE = "You have no active filter, so there is nothing to pause.";

export function paymentReceived(planName: string, until: Date | null): string {
  const lines = [
    "✅ Payment received — full access is on.",
    "",
    "Every listing that matches your filter now arrives the moment it appears,",
    "with nothing held back.",
  ];
  if (until) {
    lines.push("", `Runs until ${until.toISOString().slice(0, 10)} · ${planName}`);
  }
  return lines.join("\n");
}

export const FOUND_A_PLACE = [
  "🎉 That is the whole point — congratulations.",
  "",
  "Alerts are off and your filter is kept. If it falls through, /resume brings",
  "them straight back with nothing to set up again.",
].join("\n");

export const RESUMED = "Alerts are back on. Your filter is unchanged.";

export const NOTHING_TO_RESUME = [
  "You have no paused filter to turn back on.",
  "",
  "If you stopped with /stop, the filter was deleted — set one up again and the",
  "alerts start.",
].join("\n");

export function noFilterYet(site: string): string {
  return [
    "You don't have a filter yet — set one up here and the alerts start:",
    site,
  ].join("\n");
}

export function planLine(name: string, until: Date | null, live: boolean): string {
  if (until === null) return `Plan: ${name}`;
  const date = until.toISOString().slice(0, 10);
  return live ? `Plan: ${name}, until ${date}` : `Plan: ${name} — ended ${date}, alerts are off`;
}

function money(pence: number): string {
  return pence % 100 === 0 ? `£${pence / 100}` : `£${(pence / 100).toFixed(2)}`;
}

export async function upgradeInvitation(account: Account, token?: string): Promise<string> {
  const plans = await paidPlans();
  const share = await lapsedShare().catch(() => null);
  const lines = [
    planLine(account.plan_name, account.plan_until, true),
  ];
  if (share !== null && share < 100) {
    lines.push(
      `Once a plan ends you receive ${share}% of what matches. A paid plan sends all of it.`,
    );
  }
  lines.push("");
  if (!plans.length) {
    lines.push("There is nothing to upgrade to at the moment.");
    return lines.join("\n");
  }
  for (const plan of plans) {
    const span = plan.duration_days ? `${plan.duration_days} days` : "no time limit";
    lines.push(`${plan.display_name} — ${money(plan.price_pence)} for ${span}`);
  }
  lines.push("", token ? `${siteUrl()}/upgrade?t=${token}` : `${siteUrl()}/upgrade`);
  if (account.payment_ref) {

    lines.push("", `Your payment reference: ${account.payment_ref}`);
  }
  return lines.join("\n");
}
