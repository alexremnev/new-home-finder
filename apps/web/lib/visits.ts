import { createHash } from "node:crypto";

import { query } from "@/lib/db";

const ROBOT = /bot|crawl|spider|slurp|preview|monitor|curl|wget|headless|lighthouse/i;

// Three words, not a device database. A user agent is a claim rather than a
// fact, and the only question worth answering from it is whether the page was
// read on something held in one hand.
const TABLET = /ipad|tablet|playbook|silk|(android(?!.*mobile))/i;
const MOBILE = /iphone|ipod|android.*mobile|windows phone|blackberry|opera mini/i;

export type Device = "mobile" | "tablet" | "desktop";

export function deviceFrom(userAgent: string): Device {
  if (TABLET.test(userAgent)) return "tablet";
  if (MOBILE.test(userAgent)) return "mobile";
  return "desktop";
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
    `INSERT INTO site_visits (day, visitor_hash, country, device)
     VALUES ($1::date, $2, $3, $4)
     ON CONFLICT (day, visitor_hash)
     DO UPDATE SET hits = site_visits.hits + 1,
                   last_at = now(),
                   -- Only ever filled in, never changed: it is the same visit.
                   country = coalesce(site_visits.country, excluded.country),
                   device  = coalesce(site_visits.device,  excluded.device)`,
    [day, visitor, countryFrom(countryHeader), deviceFrom(userAgent)],
  ).catch(() => {
    // Deliberately swallowed. A counter must never be the reason the page a
    // visitor came for fails to render.
  });
}
