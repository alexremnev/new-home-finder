// Counting visitors without keeping track of anybody.
//
// ── the shape of the compromise ──────────────────────────────────────────────
//
// The question is "is the site being found", which needs a count of people rather
// than of page loads. Counting people usually means giving each one an identifier
// and keeping it — which is a small surveillance system built for a number on a
// dashboard.
//
// Instead: the identifier is a hash of the caller's address and user agent together
// with a salt that changes every day. Within a day the same person hashes the same,
// so they are counted once. Across days they are unrelated, so the table cannot be
// read as one person's history — by us or by anybody who obtains it.
//
// What that costs, stated plainly: "visitors this month" is the sum of daily
// uniques, not distinct people. Somebody visiting on five days counts five times.
// For the question being asked that is fine, and the alternative is a permanent
// identifier per person, which is not.

import { createHash } from "node:crypto";

import { query } from "@/lib/db";

// Anything that says it is a robot is one. Not a defence — a crawler that lies is
// counted, and the number is a trend rather than a measurement.
const ROBOT = /bot|crawl|spider|slurp|preview|monitor|curl|wget|headless|lighthouse/i;

export async function recordVisit(
  addressHeader: string | null,
  userAgent: string | null,
): Promise<void> {
  if (!userAgent || ROBOT.test(userAgent)) return;

  const secret = process.env.ADMIN_SESSION_SECRET ?? "";
  // No secret configured means no salt worth the name, so nothing is written
  // rather than something weakly hashed.
  if (secret.length < 32) return;

  const address = (addressHeader ?? "").split(",")[0]?.trim() || "unknown";
  const day = new Date().toISOString().slice(0, 10);
  const visitor = createHash("sha256")
    .update(`${secret}:${day}:${address}:${userAgent}`)
    .digest("hex")
    .slice(0, 32);

  // One statement: the insert counts a new visitor, the conflict counts a return
  // visit. Nothing to read first, so two requests at once cannot both think they
  // are the first.
  await query(
    `INSERT INTO site_visits (day, visitor_hash) VALUES ($1::date, $2)
     ON CONFLICT (day, visitor_hash)
     DO UPDATE SET hits = site_visits.hits + 1, last_at = now()`,
    [day, visitor],
  ).catch(() => {
    // Deliberately swallowed. A counter must never be the reason the page a
    // visitor came for fails to render.
  });
}
