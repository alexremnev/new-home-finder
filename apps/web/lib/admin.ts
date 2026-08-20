// Proving you are the admin.
//
// Node runtime only — `scrypt` is not in Web Crypto, and the whole point of using
// it is that it is deliberately slow. Session *checking* lives in
// `admin-session.ts` so that it can also run in middleware.
//
// ── why a hash and not a password in the environment ─────────────────────────
//
// A plain password in an environment variable is readable by anybody who can read
// the deployment's configuration — which on a hosted platform is a wider set of
// people and processes than the one person who should be able to log in. A scrypt
// hash is useless to all of them.
//
// scrypt rather than SHA-256: one admin password is exactly the thing an offline
// attacker brute-forces, and a fast hash makes that cheap. scrypt with these
// parameters costs ~100ms and 16MB per guess.

import { createHash, randomBytes, scryptSync, timingSafeEqual } from "node:crypto";

import { query } from "@/lib/db";

// N=16384 (2^14) is the Node default and the usual interactive-login setting: a
// tenth of a second per attempt here, and a wall in front of a dictionary.
const SCRYPT = { N: 16384, r: 8, p: 1, keylen: 64 } as const;

// Five wrong answers from one caller within the window, then that caller waits.
// Deliberately per-caller and not global: a global lockout means anybody can lock
// the real admin out by guessing badly on purpose.
const MAX_FAILURES = 5;
const WINDOW_MINUTES = 15;

/** `scrypt$<salt>$<key>`, both hex. The format names the algorithm so a future
 *  change can be told from a corrupted value rather than guessed at. */
export function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const key = scryptSync(password, salt, SCRYPT.keylen, SCRYPT);
  return `scrypt$${salt.toString("hex")}$${key.toString("hex")}`;
}

function verifyPassword(password: string, stored: string): boolean {
  const [scheme, saltHex, keyHex] = stored.split("$");
  if (scheme !== "scrypt" || !saltHex || !keyHex) return false;
  const expected = Buffer.from(keyHex, "hex");
  const actual = scryptSync(password, Buffer.from(saltHex, "hex"), expected.length, SCRYPT);
  // timingSafeEqual, not ===: a comparison that stops at the first wrong byte
  // leaks how many bytes were right, one request at a time.
  return timingSafeEqual(expected, actual);
}

/**
 * The caller, as a hash.
 *
 * Salted with the session secret so the values are meaningless outside this
 * deployment, and so the table cannot be turned into a list of addresses by
 * anybody who obtains it.
 */
export function callerHash(request: Request): string {
  // `x-forwarded-for` is the only address a hosted function sees; the first entry
  // is the client, the rest are proxies.
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

/**
 * Is this the password?
 *
 * Returns false rather than throwing when the hash is missing, so a deployment
 * without `ADMIN_PASSWORD_HASH` has an admin console nobody can enter — which is
 * the safe direction for a missing secret to fail in.
 */
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
