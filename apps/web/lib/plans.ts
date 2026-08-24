// Plans, tokens, and the queries both the form and the bot need.
//
// Every limit is read from the `plans` table rather than written here, so a price
// change or a new tier is an UPDATE. Nothing in this file knows that the trial is
// one district or that the paid plan costs £10.

import { randomBytes } from "node:crypto";

import type { Limits } from "./criteria";
import { query } from "./db";

export type Plan = {
  key: string;
  display_name: string;
  max_districts: number;
  duration_days: number | null;
  price_pence: number;
  /** The Stripe Price this plan is bought with. NULL means it cannot be bought. */
  stripe_price_id: string | null;
};

export type Account = {
  user_id: number;
  status: string;
  plan: string;
  plan_until: Date | null;
  payment_ref: string | null;
  subscription_id: number | null;
  criteria: Record<string, unknown> | null;
  plan_name: string;
  max_districts: number;
  price_pence: number;
};

export async function signupPlan(): Promise<Plan> {
  const rows = await query<Plan>(
    `SELECT key, display_name, max_districts, duration_days, price_pence
       FROM plans WHERE is_signup_default AND enabled`,
  );
  const plan = rows[0];
  // Refusing beats guessing: a sign-up granted limits nobody configured is a
  // subscription whose entitlements are an accident.
  if (!plan) throw new Error("no default sign-up plan is configured in `plans`");
  return plan;
}

export async function paidPlans(): Promise<Plan[]> {
  return query<Plan>(
    // Cheapest first, because that is the order somebody comparing two prices
    // reads them in, and the shorter plan is the lower commitment to offer first.
    `SELECT key, display_name, max_districts, duration_days, price_pence,
            stripe_price_id
       FROM plans WHERE enabled AND price_pence > 0 ORDER BY price_pence`,
  );
}

/** The person behind a Telegram chat, with their plan's limits resolved. */
export async function accountForChat(chatId: string): Promise<Account | null> {
  const rows = await query<Account>(
    `SELECT u.id AS user_id, u.status, u.plan, u.plan_until, u.payment_ref,
            s.id AS subscription_id, s.criteria,
            p.display_name AS plan_name, p.max_districts, p.price_pence
       FROM users u
       JOIN user_channels uc ON uc.user_id = u.id
       JOIN plans p          ON p.key = u.plan
       LEFT JOIN subscriptions s ON s.user_id = u.id AND s.active
      WHERE uc.channel = 'telegram' AND uc.address = $1
      ORDER BY s.id DESC
      LIMIT 1`,
    [chatId],
  );
  return rows[0] ?? null;
}

export function limitsOf(account: Account): Limits {
  return { maxDistricts: account.max_districts };
}

export function planIsLive(account: Account): boolean {
  return account.plan_until === null || account.plan_until.getTime() > Date.now();
}

/** The districts a subscription may name: what is actually being collected. */
export async function enabledDistricts(): Promise<string[]> {
  const rows = await query<{ code: string }>(
    `SELECT DISTINCT l.code
       FROM source_locations sl
       JOIN locations l ON l.id = sl.location_id
       JOIN sources s   ON s.key = sl.source_key AND s.enabled
      WHERE sl.enabled
      ORDER BY l.code`,
  );
  return rows.map((r) => r.code.toUpperCase());
}

/**
 * Neighbourhood name to district code — "leytonstone" -> "E11".
 *
 * Drawn from listings already seen rather than from a hand-kept gazetteer, for the
 * same reason `enabledDistricts` is: the feed states a location name on every
 * message, so the names people recognise arrive with the data and cannot go stale
 * against it. A name nobody has posted a listing for is absent here, which is the
 * honest answer — nothing would match it anyway.
 *
 * `DISTINCT ON` because one name reaches several districts over time ("Hackney" is
 * E5, E8 and E9); the most-recent listing decides, which is as good a tie-break as
 * any and is at least stable between page loads.
 */
export async function districtNames(): Promise<Record<string, string>> {
  const rows = await query<{ name: string; code: string }>(
    `SELECT DISTINCT ON (lower(raw->>'location'))
            raw->>'location' AS name, postcode_district AS code
       FROM listings
      WHERE raw->>'location' IS NOT NULL
        AND postcode_district IS NOT NULL
        AND status = 'active'
      ORDER BY lower(raw->>'location'), first_seen_at DESC`,
  );
  const map: Record<string, string> = {};
  for (const row of rows) {
    // Collapsed whitespace, lower case: the lookup in `readDistricts` normalises
    // the same way, and "Camden  Town" typed with two spaces has to find it.
    const key = row.name.trim().toLowerCase().replace(/\s+/g, " ");
    if (key) map[key] = row.code.toUpperCase();
  }
  return map;
}

// ── tokens ────────────────────────────────────────────────────────────────

const TOKEN_BYTES = 24;
export const START_TTL_MINUTES = 60;
export const EDIT_TTL_MINUTES = 30;
export const UPGRADE_TTL_MINUTES = 60;

export function newToken(): string {
  return randomBytes(TOKEN_BYTES).toString("base64url");
}

export type TokenPurpose = "start" | "edit" | "upgrade";

export async function issueToken(
  userId: number,
  purpose: TokenPurpose,
  ttlMinutes: number,
): Promise<string> {
  const token = newToken();
  // Any earlier token for the same purpose is dropped, so a link that was shared
  // or left in a chat stops working as soon as a new one is asked for.
  await query(`DELETE FROM user_tokens WHERE user_id = $1 AND purpose = $2`, [userId, purpose]);
  await query(
    `INSERT INTO user_tokens (token, user_id, purpose, expires_at)
     VALUES ($1, $2, $3, now() + make_interval(mins => $4::int))`,
    [token, userId, purpose, ttlMinutes],
  );
  return token;
}

/**
 * A short, unambiguous reference a person can quote with a bank transfer.
 *
 * No vowels and no 0/O/1/I, because this gets read off a screen and typed into a
 * payment reference field by hand.
 */
export function paymentRef(): string {
  const alphabet = "23456789BCDFGHJKMNPQRSTVWXZ";
  const bytes = randomBytes(6);
  const body = Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
  return `LRA-${body}`;
}

export function siteUrl(): string {
  return (process.env.SITE_URL ?? "https://london-rent-alerts.vercel.app").replace(/\/+$/, "");
}

export function botLink(token: string): string {
  const bot = process.env.TELEGRAM_BOT_USERNAME;
  if (!bot) throw new Error("TELEGRAM_BOT_USERNAME is not set");
  return `https://t.me/${bot}?start=${token}`;
}


/**
 * The account a token belongs to, without spending it.
 *
 * Used by the pages that have to render something before anything is changed. A
 * token is spent only by the request that acts on it.
 */
export async function accountForToken(
  token: string,
  purpose: TokenPurpose,
): Promise<Account | null> {
  const rows = await query<Account>(
    `SELECT u.id AS user_id, u.status, u.plan, u.plan_until, u.payment_ref,
            s.id AS subscription_id, s.criteria,
            p.display_name AS plan_name, p.max_districts, p.price_pence
       FROM user_tokens t
       JOIN users u ON u.id = t.user_id
       JOIN plans p ON p.key = u.plan
       LEFT JOIN subscriptions s ON s.user_id = u.id AND s.active
      WHERE t.token = $1 AND t.purpose = $2
        AND t.used_at IS NULL AND t.expires_at > now()
      ORDER BY s.id DESC
      LIMIT 1`,
    [token, purpose],
  );
  return rows[0] ?? null;
}


/** One purchasable plan by key, or null. Used by checkout, which is handed a key
 *  from a URL and must not trust it. */
export async function paidPlan(key: string): Promise<Plan | null> {
  const rows = await query<Plan>(
    `SELECT key, display_name, max_districts, duration_days, price_pence,
            stripe_price_id
       FROM plans WHERE key = $1 AND enabled AND price_pence > 0`,
    [key],
  );
  return rows[0] ?? null;
}
