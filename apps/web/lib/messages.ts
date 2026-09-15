import type { Account } from "./plans";
import { paidPlans, siteUrl } from "./plans";

export const WELCOME = [
  "You're subscribed. I'll message you when a new listing matches your filter.",
  "",
  "Nothing arrives for listings that were already on the market when you signed up —",
  "only what appears from now on.",
  "",
  "/stop — delete my filter and stop the messages",
].join("\n");

export const LINK_EXPIRED = [
  "That link has expired. Please fill the form again and use the new link —",
  "it takes a moment.",
  "",
  process.env.SITE_URL ?? "https://london-rent-alerts.vercel.app",
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
  const lines = [
    planLine(account.plan_name, account.plan_until, true),
    `Districts you can watch now: ${account.max_districts}`,
    "",
  ];
  if (!plans.length) {
    lines.push("There is nothing to upgrade to at the moment.");
    return lines.join("\n");
  }
  for (const plan of plans) {
    const span = plan.duration_days ? `${plan.duration_days} days` : "no time limit";
    lines.push(
      `${plan.display_name} — ${money(plan.price_pence)} for ${span}: ` +
        `${plan.max_districts} districts`,
    );
  }
  lines.push("", token ? `${siteUrl()}/upgrade?t=${token}` : `${siteUrl()}/upgrade`);
  if (account.payment_ref) {

    lines.push("", `Your payment reference: ${account.payment_ref}`);
  }
  return lines.join("\n");
}
