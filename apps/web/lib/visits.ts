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

// The operating system, as the user agent claims it. Order matters again:
// Android says Linux, and iPadOS says Mac.
const SYSTEMS: [string, RegExp][] = [
  ["iOS", /\biphone\b|\bipod\b|\bipad\b|\bcros?ios\b/i],
  ["Android", /\bandroid\b/i],
  ["Windows", /\bwindows nt\b|\bwin64\b|\bwindows\b/i],
  ["macOS", /\bmac os x\b|\bmacintosh\b/i],
  ["Chrome OS", /\bcros\b/i],
  ["Linux", /\blinux\b|\bx11\b|\bubuntu\b/i],
];

export function systemFrom(userAgent: string): string | null {
  for (const [name, pattern] of SYSTEMS) {
    if (pattern.test(userAgent)) return name;
  }
  return null;
}

// Search engines and the places a link gets posted, named rather than left as a
// bare host: "google" is what somebody reads, "www.google.co.uk" is noise, and
// the two dozen Google domains are one answer.
const SOURCES: [string, RegExp][] = [
  ["google", /(^|\.)google\./i],
  ["bing", /(^|\.)bing\./i],
  ["duckduckgo", /(^|\.)duckduckgo\./i],
  ["yandex", /(^|\.)yandex\./i],
  ["instagram", /(^|\.)instagram\./i],
  ["facebook", /(^|\.)(facebook|fb)\./i],
  ["reddit", /(^|\.)reddit\./i],
  ["x", /(^|\.)(twitter|t)\.co/i],
  ["tiktok", /(^|\.)tiktok\./i],
  ["youtube", /(^|\.)youtube\./i],
  ["linkedin", /(^|\.)linkedin\./i],
  ["telegram", /(^|\.)(telegram|t\.me)/i],
  ["whatsapp", /(^|\.)whatsapp\./i],
];

/** The referring host, or null when the header is absent or unreadable. */
export function hostOf(referer: string | null): string | null {
  if (!referer) return null;
  try {
    const host = new URL(referer).hostname.toLowerCase();
    return host.replace(/^www\./, "").slice(0, 120) || null;
  } catch {
    return null;
  }
}

/**
 * Where the visit came from, in one word where there is one.
 *
 * `utm_source` beats the header: a campaign saying what it is beats a guess
 * from the host, and some apps strip the referrer entirely. Our own domain is
 * "direct" — a visitor moving between our pages did not arrive from anywhere.
 */
export function sourceOf(
  referer: string | null,
  utmSource: string | null,
  ownHost: string | null,
): { source: string; referrer: string | null } {
  const host = hostOf(referer);
  const tagged = (utmSource ?? "").trim().toLowerCase().slice(0, 60);

  if (tagged) return { source: tagged, referrer: host };
  if (!host) return { source: "direct", referrer: null };
  if (ownHost && (host === ownHost || host.endsWith(`.${ownHost}`))) {
    return { source: "direct", referrer: host };
  }
  for (const [name, pattern] of SOURCES) {
    if (pattern.test(host)) return { source: name, referrer: host };
  }
  return { source: host, referrer: host };
}

/** The first language tag, which is the one the browser prefers. */
export function languageOf(header: string | null): string | null {
  const first = (header ?? "").split(",")[0]?.split(";")[0]?.trim();
  return first && /^[A-Za-z-]{2,20}$/.test(first) ? first : null;
}

// A place name from the edge. Percent-encoded there, because a city name is not
// always ASCII.
export function placeOf(header: string | null): string | null {
  const raw = (header ?? "").trim();
  if (!raw) return null;
  try {
    return decodeURIComponent(raw).slice(0, 80) || null;
  } catch {
    return raw.slice(0, 80) || null;
  }
}

// Two letters from the edge, which knows the address without us storing it.
// Absent when the site runs anywhere but Vercel, and "XX" when the network it
// came from cannot be placed.
export function countryFrom(header: string | null): string | null {
  const code = (header ?? "").trim().toUpperCase();
  return /^[A-Z]{2}$/.test(code) && code !== "XX" ? code : null;
}

/** Everything a request offers that is worth recording. All of it optional. */
export type Arrival = {
  address: string | null;
  userAgent: string | null;
  country: string | null;
  city: string | null;
  region: string | null;
  referer: string | null;
  language: string | null;
  /** utm_source from the landing url, when the link carried one. */
  utmSource?: string | null;
  /** utm_campaign, or utm_medium when only that was given. */
  campaign?: string | null;
};

export async function recordVisit(arrival: Arrival): Promise<void> {
  const userAgent = arrival.userAgent;
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

  const address = (arrival.address ?? "").split(",")[0]?.trim() || "unknown";
  const day = new Date().toISOString().slice(0, 10);
  // The day is part of the hash on purpose: the same person tomorrow is a
  // different row, so this counts people without being able to follow one.
  const visitor = createHash("sha256")
    .update(`${secret}:${day}:${address}:${userAgent}`)
    .digest("hex")
    .slice(0, 32);

  // Our own domain reads as "direct": somebody moving between our pages did
  // not arrive from anywhere.
  const ownHost = hostOf(process.env.SITE_URL ?? "https://londonhomefinder.co.uk");
  const { source, referrer } = sourceOf(
    arrival.referer,
    arrival.utmSource ?? null,
    ownHost,
  );

  await query(
    `INSERT INTO site_visits
            (day, visitor_hash, country, device, browser,
             source, referrer, campaign, city, region, os, language)
     VALUES ($1::date, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
     ON CONFLICT (day, visitor_hash)
     DO UPDATE SET hits = site_visits.hits + 1,
                   last_at = now(),
                   -- Only ever filled in, never changed: it is the same visit,
                   -- and the first page of it is the one that says where the
                   -- person came from. A second page would say "direct".
                   country  = coalesce(site_visits.country,  excluded.country),
                   device   = coalesce(site_visits.device,   excluded.device),
                   browser  = coalesce(site_visits.browser,  excluded.browser),
                   source   = coalesce(site_visits.source,   excluded.source),
                   referrer = coalesce(site_visits.referrer, excluded.referrer),
                   campaign = coalesce(site_visits.campaign, excluded.campaign),
                   city     = coalesce(site_visits.city,     excluded.city),
                   region   = coalesce(site_visits.region,   excluded.region),
                   os       = coalesce(site_visits.os,       excluded.os),
                   language = coalesce(site_visits.language, excluded.language)`,
    [
      day,
      visitor,
      countryFrom(arrival.country),
      deviceFrom(userAgent),
      browser,
      source,
      referrer,
      (arrival.campaign ?? "").trim().toLowerCase().slice(0, 80) || null,
      placeOf(arrival.city),
      placeOf(arrival.region),
      systemFrom(userAgent),
      languageOf(arrival.language),
    ],
  ).catch(() => {
    // Deliberately swallowed. A counter must never be the reason the page a
    // visitor came for fails to render.
  });
}
