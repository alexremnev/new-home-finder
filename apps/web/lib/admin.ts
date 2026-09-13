import { createHash, randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

import { query } from "@/lib/db";

const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 64 } as const;

const MAX_FAILURES = 5;
const WINDOW_MINUTES = 15;

function verifyPassword(password: string, stored: string): boolean {
  const [scheme, saltHex, keyHex] = stored.split("$");
  if (scheme !== "scrypt" || !saltHex || !keyHex) return false;
  const expected = Buffer.from(keyHex, "hex");
  const actual = scryptSync(password, Buffer.from(saltHex, "hex"), expected.length, SCRYPT);

  return timingSafeEqual(expected, actual);
}

export function callerHash(request: Request): string {

  const forwarded = request.headers.get("x-forwarded-for") ?? "";
  const address = forwarded.split(",")[0]?.trim() || "unknown";
  return createHash("sha256")
    .update(`${process.env.ADMIN_SESSION_SECRET ?? ""}:${address}`)
    .digest("hex")
    .slice(0, 32);
}

export async function tooManyFailures(ipHash: string): Promise<boolean> {
  const rows = await query<{ failures: number }>(
    `SELECT count(*)::int AS failures FROM admin_logins
      WHERE ip_hash = $1 AND NOT ok
        AND created_at > now() - make_interval(mins => $2::int)`,
    [ipHash, WINDOW_MINUTES],
  );
  return (rows[0]?.failures ?? 0) >= MAX_FAILURES;
}

export async function recordAttempt(ipHash: string, ok: boolean): Promise<void> {
  await query(`INSERT INTO admin_logins (ip_hash, ok) VALUES ($1, $2)`, [ipHash, ok]);
}

export function passwordIsCorrect(password: string): boolean {
  const stored = process.env.ADMIN_PASSWORD_HASH;
  if (!stored || !password) return false;
  try {
    return verifyPassword(password, stored);
  } catch {
    return false;
  }
}

export const LOCKOUT_WINDOW_MINUTES = WINDOW_MINUTES;
