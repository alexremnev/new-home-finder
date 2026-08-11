// Everything the bot says outside a listing alert.
//
// Kept apart from the routes so that the wording can be changed, or translated,
// without touching the logic that decides when each one is sent. Listing alerts
// are rendered by the worker (worker/notify/telegram.py) and deliberately not
// duplicated here.

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

export const ALREADY_ACTIVE = [
  "You're already subscribed — nothing to do.",
  "",
  "/stop — delete my filter and stop the messages",
].join("\n");

export const NEEDS_LINK = [
  "Hello. To set up alerts, choose your filter on the site first — the link there",
  "brings you back here already connected.",
  "",
  process.env.SITE_URL ?? "https://london-rent-alerts.vercel.app",
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

export const HELP = [
  "I only send alerts about new rental listings.",
  "",
  "/stop — delete my filter and stop the messages",
].join("\n");

// ── plans ─────────────────────────────────────────────────────────────────

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

/**
 * What /upgrade says. Prices and limits come from the `plans` table, so this
 * cannot drift out of step with what is actually charged.
 */
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
    // Quoted with the payment so a transfer can be matched to an account without
    // asking who sent it.
    lines.push("", `Your payment reference: ${account.payment_ref}`);
  }
  return lines.join("\n");
}
