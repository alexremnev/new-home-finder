import { randomBytes } from "node:crypto";

import type { Limits } from "./criteria";
import { query } from "./db";

export type Plan = {
  key: string;
  display_name: string;
  max_districts: number;
  duration_days: number | null;
  price_pence: number;

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

  if (!plan) throw new Error("no default sign-up plan is configured in `plans`");
  return plan;
}

export async function paidPlans(): Promise<Plan[]> {
  return query<Plan>(

    `SELECT key, display_name, max_districts, duration_days, price_pence,
            stripe_price_id
       FROM plans WHERE enabled AND price_pence > 0 ORDER BY price_pence`,
  );
}

// What an ended plan drops back to, as a percentage. Read rather than written
// into the copy: the number is a product decision that lives in `plans`, and a
// sentence repeating it is a sentence that will one day be wrong.
export async function lapsedShare(): Promise<number | null> {
  const rows = await query<{ share: number }>(
    `SELECT p.delivery_share AS share
       FROM plan_settings ps
       JOIN plans p ON p.key = ps.lapsed_plan AND p.enabled
      LIMIT 1`,
  );
  const share = rows[0]?.share;
  return share === undefined || share === null ? null : Number(share);
}

export async function accountForChat(
  address: string,
  channel: "telegram" | "whatsapp" = "telegram",
): Promise<Account | null> {
  const rows = await query<Account>(
    `SELECT u.id AS user_id, u.status, u.plan, u.plan_until, u.payment_ref,
            s.id AS subscription_id, s.criteria,
            p.display_name AS plan_name, p.max_districts, p.price_pence
       FROM users u
       JOIN user_channels uc ON uc.user_id = u.id
       JOIN plans p          ON p.key = u.plan
       LEFT JOIN subscriptions s ON s.user_id = u.id AND s.active
      WHERE uc.channel = $2 AND uc.address = $1
      ORDER BY s.id DESC
      LIMIT 1`,
    [address, channel],
  );
  return rows[0] ?? null;
}

export function limitsOf(account: Account): Limits {
  return { maxDistricts: account.max_districts };
}

export function planIsLive(account: Account): boolean {
  return account.plan_until === null || account.plan_until.getTime() > Date.now();
}

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

    const key = row.name.trim().toLowerCase().replace(/\s+/g, " ");
    if (key) map[key] = row.code.toUpperCase();
  }
  return map;
}

const TOKEN_BYTES = 24;
export const START_TTL_MINUTES = 60;

export const UPGRADE_TTL_MINUTES = 60;

export function newToken(): string {
  return randomBytes(TOKEN_BYTES).toString("base64url");
}

export type TokenPurpose = "start" | "upgrade";

export async function issueToken(
  userId: number,
  purpose: TokenPurpose,
  ttlMinutes: number,
): Promise<string> {
  const token = newToken();

  await query(`DELETE FROM user_tokens WHERE user_id = $1 AND purpose = $2`, [userId, purpose]);
  await query(
    `INSERT INTO user_tokens (token, user_id, purpose, expires_at)
     VALUES ($1, $2, $3, now() + make_interval(mins => $4::int))`,
    [token, userId, purpose, ttlMinutes],
  );
  return token;
}

// The mirror of botLink. wa.me opens WhatsApp with the message already typed,
// so linking a number costs one tap — and the inbound message is what proves the
// number belongs to the person, exactly as /start <token> does on Telegram.
export function whatsappLink(token: string): string | null {
  const number = (process.env.WHATSAPP_NUMBER ?? "").replace(/\D/g, "");
  if (!number) return null;
  return `https://wa.me/${number}?text=${encodeURIComponent(`Link my alerts: ${token}`)}`;
}

export function paymentRef(): string {
  const alphabet = "23456789BCDFGHJKMNPQRSTVWXZ";
  const bytes = randomBytes(6);
  const body = Array.from(bytes, (b) => alphabet[b % alphabet.length]).join("");
  return `LRA-${body}`;
}

export function siteUrl(): string {
  return (process.env.SITE_URL ?? "https://londonhomefinder.co.uk").replace(/\/+$/, "");
}

export function botLink(token: string): string {
  const bot = process.env.TELEGRAM_BOT_USERNAME;
  if (!bot) throw new Error("TELEGRAM_BOT_USERNAME is not set");
  return `https://t.me/${bot}?start=${token}`;
}

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

export async function paidPlan(key: string): Promise<Plan | null> {
  const rows = await query<Plan>(
    `SELECT key, display_name, max_districts, duration_days, price_pence,
            stripe_price_id
       FROM plans WHERE key = $1 AND enabled AND price_pence > 0`,
    [key],
  );
  return rows[0] ?? null;
}
