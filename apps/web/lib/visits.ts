import { createHash } from "node:crypto";

import { query } from "@/lib/db";

// Automation that names itself. Kept, because it is cheap and catches the
// well-behaved half — but it can only ever catch what admits to being a robot,
// and "python-requests" or "facebookexternalhit" contain no such word.
const ROBOT =
  /bot|crawl|spider|slurp|preview|monitor|curl|wget|headless|lighthouse|python|requests|scrapy|okhttp|java|go-http|axios|libwww|httpclient|postman|insomnia|facebookexternalhit|whatsapp|telegram|discord|slack|embedly|feedfetcher|pingdom|uptime|semrush|ahrefs|dataprovider|petal|gpt|claude|perplexity|anthropic|openai/i;

// Three words, not a device database. The only question worth answering from a
// user agent is whether the page was read on something held in one hand.
const TABLET = /ipad|tablet|playbook|silk|(android(?!.*mobile))/i;
const MOBILE = /iphone|ipod|android.*mobile|windows phone|blackberry|opera mini/i;

export type Device = "mobile" | "tablet" | "desktop";

export function deviceFrom(userAgent: string): Device {
  if (TABLET.test(userAgent)) return "tablet";
  if (MOBILE.test(userAgent)) return "mobile";
  return "desktop";
}

// In order, because these strings lie about each other on purpose: Edge claims
// Chrome, Chrome claims Safari, and almost everything claims Mozilla. The first
// match down this list is the real answer.
//
// No version numbers. A version is a fingerprint, and the question was which
// browser.
const BROWSERS: [string, RegExp][] = [
  ["Edge", /\bedg(?:e|a|ios)?\//i],
  ["Opera", /\bopr\/|\bopera\b/i],
  ["Samsung Internet", /samsungbrowser\//i],
  ["Firefox", /\bfirefox\/|\bfxios\//i],
  ["Chrome", /\bchrome\/|\bcrios\/|\bchromium\//i],
  ["Safari", /\bsafari\//i],
];

// The browser, or nothing when the string names none.
//
// Nothing is the interesting answer: it is the strongest single signal that a
// request was not a person reading the page, and it catches the automation that
// the blocklist above cannot — anything sending a bare or invented user agent.
export function browserFrom(userAgent: string): string | null {
  for (const [name, pattern] of BROWSERS) {
    if (pattern.test(userAgent)) return name;
  }
  return null;
}

// Two letters from the edge, which knows the address without us storing it.
// Absent when the site runs anywhere but Vercel, and "XX" when the network it
// came from cannot be placed.
export function countryFrom(header: string | null): string | null {
  const code = (header ?? "").trim().toUpperCase();
  return /^[A-Z]{2}$/.test(code) && code !== "XX" ? code : null;
}

export async function recordVisit(
  addressHeader: string | null,
  userAgent: string | null,
  countryHeader: string | null = null,
): Promise<void> {
  if (!userAgent || ROBOT.test(userAgent)) return;

  // No recognisable browser, no visit. This is the filter that does the work:
  // the blocklist catches what announces itself, and this catches the rest.
  //
  // The cost is a person on something genuinely obscure, who is not counted.
  // That is the right way round: a missing visitor understates the number,
  // while a counted robot makes every figure on the page a guess.
  const browser = browserFrom(userAgent);
  if (!browser) return;

  const secret = process.env.ADMIN_SESSION_SECRET ?? "";

  if (secret.length < 32) return;

  const address = (addressHeader ?? "").split(",")[0]?.trim() || "unknown";
  const day = new Date().toISOString().slice(0, 10);
  // The day is part of the hash on purpose: the same person tomorrow is a
  // different row, so this counts people without being able to follow one.
  const visitor = createHash("sha256")
    .update(`${secret}:${day}:${address}:${userAgent}`)
    .digest("hex")
    .slice(0, 32);

  await query(
    `INSERT INTO site_visits (day, visitor_hash, country, device, browser)
     VALUES ($1::date, $2, $3, $4, $5)
     ON CONFLICT (day, visitor_hash)
     DO UPDATE SET hits = site_visits.hits + 1,
                   last_at = now(),
                   -- Only ever filled in, never changed: it is the same visit.
                   country = coalesce(site_visits.country, excluded.country),
                   device  = coalesce(site_visits.device,  excluded.device),
                   browser = coalesce(site_visits.browser, excluded.browser)`,
    [day, visitor, countryFrom(countryHeader), deviceFrom(userAgent), browser],
  ).catch(() => {
    // Deliberately swallowed. A counter must never be the reason the page a
    // visitor came for fails to render.
  });
}
