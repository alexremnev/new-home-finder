// Everything the bot says outside a listing alert.
//
// Kept apart from the routes so that the wording can be changed, or translated,
// without touching the logic that decides when each one is sent. Listing alerts
// are rendered by the worker (worker/notify/telegram.py) and deliberately not
// duplicated here.

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
