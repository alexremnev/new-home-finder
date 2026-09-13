import { createHash } from "node:crypto";

import { query } from "@/lib/db";

const ROBOT = /bot|crawl|spider|slurp|preview|monitor|curl|wget|headless|lighthouse/i;

export async function recordVisit(
  addressHeader: string | null,
  userAgent: string | null,
): Promise<void> {
  if (!userAgent || ROBOT.test(userAgent)) return;

  const secret = process.env.ADMIN_SESSION_SECRET ?? "";

  if (secret.length < 32) return;

  const address = (addressHeader ?? "").split(",")[0]?.trim() || "unknown";
  const day = new Date().toISOString().slice(0, 10);
  const visitor = createHash("sha256")
    .update(`${secret}:${day}:${address}:${userAgent}`)
    .digest("hex")
    .slice(0, 32);

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
