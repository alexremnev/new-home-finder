import { randomBytes } from "node:crypto";

import type { Criteria, Limits } from "./criteria";
import { query } from "./db";
import { stripeMode } from "./stripe";

export type Channel = "telegram" | "whatsapp";

export type Plan = {
  key: string;
  display_name: string;
  max_districts: number;
  duration_days: number | null;
  price_pence: number;

  // The id for whichever Stripe account is in use. Both are read from the row
  // and `priced` picks one, so nothing downstream has to know about modes.
  stripe_price_id: string | null;
};

type PlanRow = Plan & { stripe_price_id_live: string | null };

// A Price id belongs to one Stripe account. Selecting it here, next to the
// mode, is what makes going live a single variable.
function priced(row: PlanRow): Plan {
  const { stripe_price_id_live, ...plan } = row;
  return {
    ...plan,
    stripe_price_id:
      stripeMode() === "live" ? stripe_price_id_live : row.stripe_price_id,
  };
}

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
  // Which messenger this person is actually on, so the prices they are shown
  // are the ones their channel is priced at. Null only for an account with no
  // channel connected yet, which cannot reach a paid page anyway.
  channel: Channel | null;
  // Whether the plan is live, answered by `user_entitlement` rather than here.
  // A WhatsApp month ends at thirty days OR at nine hundred alerts, so the
  // date alone stopped being the answer — see 0057.
  live: boolean;
  alert_allowance: number | null;
  alerts_used: number;
};

export type SignupPlan = Plan & { duration_days_whatsapp: number | null };

export async function signupPlan(): Promise<SignupPlan> {
  const rows = await query<SignupPlan>(
    `SELECT key, display_name, max_districts, duration_days, price_pence,
            duration_days_whatsapp
       FROM plans WHERE is_signup_default AND enabled`,
  );
  const plan = rows[0];

  if (!plan) throw new Error("no default sign-up plan is configured in `plans`");
  return plan;
}

/** How many days the trial lasts on one messenger. */
export function trialDaysOn(plan: SignupPlan, channel: Channel): number | null {
  const days =
    channel === "whatsapp"
      ? plan.duration_days_whatsapp ?? plan.duration_days
      : plan.duration_days;
  return days && days > 0 ? days : null;
}

// What somebody on this messenger may buy. A plan with no channel is for
// either; one with a channel is only for that one, because what it costs us to
// deliver differs. Passing no channel asks for the whole list, which is what the
// admin wants and nobody buying wants.
export async function paidPlans(channel?: Channel): Promise<Plan[]> {
  const rows = await query<PlanRow>(
    `SELECT key, display_name, max_districts, duration_days, price_pence,
            stripe_price_id, stripe_price_id_live
       FROM plans
      WHERE enabled AND price_pence > 0
        AND ($1::text IS NULL OR channel IS NULL OR channel = $1)
      ORDER BY price_pence`,
    [channel ?? null],
  );
  return rows.map(priced);
}

export type Price = { pence: number; unit: string };

// "week" reads better than "7 days" and is what the price is actually thought
// of as. Anything that is not a round period falls back to saying the days.
function unitOf(days: number | null): string {
  if (days === null) return "no end date";
  if (days === 1) return "day";
  if (days === 7) return "week";
  if (days >= 28 && days <= 31) return "month";
  return `${days} days`;
}

/**
 * Every price a messenger is sold at, cheapest first — what the sign-up card
 * shows. Read rather than written into the copy, so a card cannot advertise a
 * figure that `/pay` then contradicts.
 */
export async function channelPrices(channel: Channel): Promise<Price[]> {
  const plans = await paidPlans(channel).catch(() => []);
  return plans.map((plan) => ({
    pence: plan.price_pence,
    unit: unitOf(plan.duration_days),
  }));
}

/**
 * How many alerts a messenger's paid plan includes, or null when it includes
 * as many as there are.
 *
 * Read from `plans.alert_allowance` and never written into the copy, for the
 * reason `lapsedShare` gives below: the number is a product decision that
 * lives in the table, and a card repeating it is a card that will one day
 * promise nine hundred to somebody whose plan says something else.
 *
 * The largest of a channel's plans, where there is more than one — the card
 * speaks for the offer, and the offer is the best of them.
 */
export async function channelAllowance(channel: Channel): Promise<number | null> {
  const rows = await query<{ allowance: number | null }>(
    `SELECT max(alert_allowance) AS allowance
       FROM plans
      WHERE enabled AND price_pence > 0
        AND ($1::text IS NULL OR channel IS NULL OR channel = $1)`,
    [channel],
  ).catch(() => []);
  const allowance = rows[0]?.allowance;
  return allowance === undefined || allowance === null ? null : Number(allowance);
}

/**
 * What an ended plan drops back to, as a percentage, on this messenger.
 *
 * Read rather than written into the copy: the number is a product decision
 * that lives in `plans`, and a sentence repeating it is a sentence that will
 * one day be wrong.
 *
 * It is nought on WhatsApp, and the channel is therefore not optional. The
 * fifth of the listings a finished plan keeps receiving costs nothing on
 * Telegram and is a standing bill on a channel Meta charges per message for,
 * so 0057 stops it there — and a page promising a share that is not delivered
 * is exactly the drift that view exists to prevent.
 */
export async function lapsedShare(channel?: Channel | null): Promise<number | null> {
  if (channel === "whatsapp") return 0;
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
            p.display_name AS plan_name, p.max_districts, p.price_pence,
            uc.channel AS channel,
            e.live, e.alert_allowance, e.alerts_used
       FROM users u
       JOIN user_channels uc    ON uc.user_id = u.id
       JOIN plans p             ON p.key = u.plan
       JOIN user_entitlement e  ON e.user_id = u.id
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

// Read, not worked out. The date was the whole answer until a WhatsApp month
// gained an allowance as well, and a second copy of that rule in TypeScript is
// a second place for it to drift from the one the worker delivers by.
export function planIsLive(account: Account): boolean {
  return account.live;
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

// A checkout link lives in a chat message, and a chat message is read whenever
// it is read. This was an hour, on the reasoning that somebody who has just
// sent /pay is about to tap it — true of the tap, false of the message, which
// stays in the conversation and is the thing people come back to when they have
// decided. Thirty-six hours, the same as `PUSHED_TOKEN_MINUTES` in
// `worker/store.py`: the worker's checkout links and the bot's are the same
// link, and two answers to how long it lasts is one too many.
export const UPGRADE_TTL_MINUTES = 36 * 60;

// An edit link is followed straight away, from a chat the person is already in.
// Long enough to fill the form without rushing, short enough that one left in a
// screenshot stops working.
export const EDIT_TTL_MINUTES = 60;

export function newToken(): string {
  return randomBytes(TOKEN_BYTES).toString("base64url");
}

// "edit" is for somebody who already has a filter and is changing it. The form
// then knows who they are, so it can drop the sign-up offer — prices and a free
// trial are not what a returning subscriber came to read.
export type TokenPurpose = "start" | "upgrade" | "edit";

/**
 * A link token for this person and this purpose — the live one where there is
 * one, extended rather than replaced.
 *
 * This used to delete the account's previous token and insert a new one, which
 * meant every link already sitting in their chat stopped working the moment
 * another was handed out. That is the one failure the person can do nothing
 * about: the button looks fine and lands on "that link has expired". The worker
 * pushes a thirty-six hour checkout link into a listing alert, the site then
 * hands out an hour-long one for /pay, and whichever ran last used to take the
 * other's away.
 *
 * So: one token per account per purpose, and `greatest` on the expiry so the
 * life only ever grows. Both sides agree on this — see `upgrade_token` in
 * `worker/store.py`, which does the same from the other direction.
 *
 * The purposes that must not be reusable do not come through here: a 'start'
 * token is claimed once, and `/api/subscribe` inserts those itself.
 */
export async function issueToken(
  userId: number,
  purpose: TokenPurpose,
  ttlMinutes: number,
): Promise<string> {
  const kept = await query<{ token: string }>(
    `UPDATE user_tokens
        SET expires_at = greatest(expires_at, now() + make_interval(mins => $3::int))
      WHERE user_id = $1 AND purpose = $2
        AND used_at IS NULL AND expires_at > now()
      RETURNING token`,
    [userId, purpose, ttlMinutes],
  );
  const live = kept[0]?.token;
  if (live) return live;

  const token = newToken();
  // Only dead rows are left to clear: anything live was returned above.
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

/**
 * The conversation itself, with nothing typed into it.
 *
 * Where somebody who has just changed a filter is sent: they are already
 * connected, so there is no token to carry and nothing for them to send. A
 * `?text=` here would put "Link my alerts: …" in the box of a person who
 * linked a fortnight ago.
 */
export function whatsappChat(): string | null {
  const number = (process.env.WHATSAPP_NUMBER ?? "").replace(/\D/g, "");
  return number ? `https://wa.me/${number}` : null;
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

/**
 * The filter form, for somebody a bot already knows.
 *
 * Carries an `e=` token whenever the address belongs to an account, which is
 * what lets the page open on their current criteria instead of the defaults —
 * and what tells it to show "save and go back" rather than a price list. Every
 * link a bot hands out goes through here, so there is no second route to the
 * form that forgets the token and silently offers somebody a blank page.
 *
 * `c=` is which messenger the link was handed out in. The token alone cannot
 * answer that: an account that has connected both has one primary channel, and
 * the page was reading it to decide which app to send people back to — so
 * somebody writing from WhatsApp was offered Telegram, named on the only
 * button on the page. The bot knows where it is being spoken to, so it says.
 * It is a hint and not a credential: `returningFor` only honours it when the
 * account really is verified on that messenger.
 *
 * The plain form on anything unknown, and on any failure: a link without a
 * token still works, where no link at all is a dead end.
 */
export async function filterUrl(
  address: string,
  channel: Channel = "telegram",
): Promise<string> {
  const plain = `${siteUrl()}/`;
  const account = await accountForChat(address, channel).catch(() => null);
  // Said out loud, both times. A link without a token is the sign-up page, so
  // the symptom is a subscriber being shown a blank form and a free trial —
  // which reads as the page being wrong rather than as this falling back, and
  // there was nothing in the log to tell the two apart.
  if (!account) {
    console.error("filterUrl: no account for this address — the plain form it is", {
      channel,
      address: address.slice(-4),
    });
    return plain;
  }

  const token = await issueToken(account.user_id, "edit", EDIT_TTL_MINUTES).catch(
    () => null,
  );
  if (!token) {
    console.error("filterUrl: could not issue an edit token", {
      channel,
      user: account.user_id,
    });
    return plain;
  }
  return `${siteUrl()}/?e=${encodeURIComponent(token)}&c=${channel}`;
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
            p.display_name AS plan_name, p.max_districts, p.price_pence,
            (SELECT uc.channel
               FROM user_channels uc
               JOIN channels c ON c.key = uc.channel AND c.enabled
              WHERE uc.user_id = u.id
              ORDER BY uc.is_primary DESC, uc.channel
              LIMIT 1) AS channel,
            -- Whether the plan is live, and the allowance it is live against.
            -- Missing here while accountForChat selected them, which left
            -- planIsLive undefined for every token-based caller: a paying
            -- subscriber opening /update was told their plan had ended.
            e.live, e.alert_allowance, e.alerts_used
       FROM user_tokens t
       JOIN users u ON u.id = t.user_id
       JOIN plans p ON p.key = u.plan
       JOIN user_entitlement e ON e.user_id = u.id
       LEFT JOIN subscriptions s ON s.user_id = u.id AND s.active
      WHERE t.token = $1 AND t.purpose = $2
        AND t.used_at IS NULL AND t.expires_at > now()
      ORDER BY s.id DESC
      LIMIT 1`,
    [token, purpose],
  );
  return rows[0] ?? null;
}

export type Returning = {
  channel: Channel;
  /**
   * The edit token this was read from, handed back so saving can be done
   * against the account it belongs to. Already in the page's url, so it is
   * nothing the browser did not have.
   */
  token: string;
  /**
   * The filter they already have, so the form opens on it rather than on the
   * defaults. Empty for an account with no active subscription — somebody who
   * used /stop and came back — which is the blank form, correctly.
   */
  criteria: Criteria;
  /** Whether every match is being delivered, rather than a lapsed share. */
  full: boolean;
  /**
   * A checkout link, minted here, for somebody whose plan has run out. Null
   * when they are on full delivery and there is nothing to offer — and also
   * when issuing it failed, in which case the page simply says nothing rather
   * than offering a link that cannot work.
   */
  upgradeUrl: string | null;
};

/**
 * Which messenger to treat this person as being in.
 *
 * The one the link was handed out in where they are verified on it, and their
 * primary channel otherwise — which is all there was to go on before, and is
 * still right for a link that has lost its `c=`.
 */
async function standingOn(
  userId: number,
  standing?: string | null,
): Promise<Channel | null> {
  const rows = await query<{ channel: Channel; is_primary: boolean }>(
    `SELECT uc.channel, uc.is_primary
       FROM user_channels uc
       JOIN channels c ON c.key = uc.channel AND c.enabled
      WHERE uc.user_id = $1 AND uc.verified_at IS NOT NULL
      ORDER BY uc.is_primary DESC, uc.channel`,
    [userId],
  ).catch(() => []);

  const here = rows.find((row) => row.channel === standing);
  return here?.channel ?? rows[0]?.channel ?? null;
}

/**
 * Who is changing their filter, for a token issued by /update.
 *
 * Only what the page needs: the filter to open the form on, which messenger to
 * send them back to, whether they are still getting everything, and — if not —
 * a link that can actually take the payment. Anything more would be a sign-up
 * page wearing a different hat.
 *
 * `standing` is the messenger the link was handed out in, off the url's `c=`.
 * Honoured only where the account is verified on it, which makes it useless to
 * anybody who edits it and authoritative where it counts: somebody who has
 * connected both messengers has one primary channel, and the primary is not
 * necessarily the chat they are writing from.
 */
export async function returningFor(
  token: string,
  standing?: string | null,
): Promise<Returning | null> {
  const account = await accountForToken(token, "edit").catch(() => null);
  if (!account) return null;

  const channel = await standingOn(account.user_id, standing);
  // No messenger at all is no "go back" to offer, and the sign-up page is then
  // the honest one.
  if (!channel) return null;

  // Both halves from the one place: whether the plan is live, and what share
  // follows from that. Worked out here, the two could disagree with what the
  // worker is actually delivering.
  const rows = await query<{ share: number | null }>(
    `SELECT e.delivery_share AS share FROM user_entitlement e WHERE e.user_id = $1`,
    [account.user_id],
  ).catch(() => []);
  const share = Number(rows[0]?.share ?? 0);

  const criteria = (account.criteria ?? {}) as Criteria;

  const full = planIsLive(account) && share >= 100;
  if (full) return { channel, token, full, upgradeUrl: null, criteria };

  // Its own token, because /upgrade needs one to know whose plan is being
  // bought — a bare /upgrade can only answer "that link has expired".
  const paying = await issueToken(account.user_id, "upgrade", UPGRADE_TTL_MINUTES)
    .catch(() => null);
  return {
    channel,
    token,
    full,
    upgradeUrl: paying ? `${siteUrl()}/upgrade?t=${encodeURIComponent(paying)}` : null,
    criteria,
  };
}

export async function paidPlan(key: string): Promise<Plan | null> {
  const rows = await query<PlanRow>(
    `SELECT key, display_name, max_districts, duration_days, price_pence,
            stripe_price_id, stripe_price_id_live
       FROM plans WHERE key = $1 AND enabled AND price_pence > 0`,
    [key],
  );
  const row = rows[0];
  return row ? priced(row) : null;
}
