// Setting a filter up inside the bot, one tap at a time.
//
// The whole file is arranged around one constraint: the webhook is a serverless
// function, so between the tap that picks SE16 and the tap that picks a price
// there is no process and no memory. Every step therefore has to be reconstructible
// from a row in `wizard_sessions` plus the tap that just arrived, and nothing may
// be held anywhere else.
//
// Two consequences shape the code.
//
// 1. The transition is a pure function. `apply()` takes a session, an action and
//    the context the plan supplies, and returns the next session — no database, no
//    network. That is what makes a five-step conversation testable as a list of
//    button presses instead of only against a live bot.
//
// 2. `callback_data` is tiny. Telegram caps it at 64 bytes, which is nowhere near
//    a draft, so it carries an action and never state: `w:d:SE16`, not the list of
//    districts chosen so far.
//
// The draft is a `Criteria` object from the start, not a bag of form fields turned
// into one at the end. It goes through the same `enforceLimits` the web form does
// before it is written, so there is one definition of what a filter is and one
// place that decides whether it is allowed.

import { parseRange } from "./commands";
import type { Criteria } from "./criteria";
import { describeCriteria, enforceLimits, FURNISHED } from "./criteria";
import { query, transaction } from "./db";
import { paymentRef } from "./plans";
import type { Button, Keyboard } from "./telegram";

// ── the shape of a session ────────────────────────────────────────────────

export const STEPS = ["districts", "bedrooms", "price", "pets", "furnished", "confirm"] as const;
export type Step = (typeof STEPS)[number] | "overwrite";

export type Session = {
  chatId: string;
  userId: number | null;
  step: Step;
  draft: Criteria;
  promptMsgId: string | null;
};

/** What the plan and the coverage allow. Read from the database, never assumed. */
export type Context = {
  districts: string[];
  maxDistricts: number;
};

export const SESSION_TTL_MINUTES = 60;

// The rent floor. There is no such thing as a £50 pcm London flat, so a number
// below this is a typo — usually a weekly rent — and accepting it produces a
// filter that matches nothing and looks like it should match everything.
export const PRICE_FLOOR = 300;
export const PRICE_CEILING = 20_000;

// Deliberately capped at 4 rather than following BEDROOM_LIMIT. This is a
// *minimum*, and "at least 5 bedrooms" is not a search this product serves; five
// buttons that all get used beat ten where six never do.
export const MAX_BEDROOMS_CHOICE = 4;

// How many districts get a button. The rest are typed, because a keyboard cannot
// hold London: there are around 300 outward codes and Telegram will not render a
// hundred rows — and nobody scrolls three hundred buttons to find SE16.
export const DISTRICT_BUTTONS = 3;

// A UK outward code: E1, E11, SW17, EC1A, W1A. Deliberately not the full postcode
// pattern — a subscription names a district, and "E11 4EG" is reduced to "E11"
// before it gets here so that someone who pastes their whole postcode is not told
// they are wrong.
const OUTWARD = /^[A-Z]{1,2}\d{1,2}[A-Z]?$/;

/**
 * Read a typed list of districts: "SE16, E14 N1" or a pasted "E11 4EG".
 *
 * Returns what was recognised and what was not, separately, so the reply can name
 * the rejects. Silently dropping one would hand back a filter that is not the one
 * they asked for — the same reasoning as `districtList` in criteria.ts.
 */
export function readDistricts(
  text: string,
  allowed: string[],
): { codes: string[]; badFormat: string[]; notCovered: string[] } {
  const codes: string[] = [];
  const badFormat: string[] = [];
  const notCovered: string[] = [];
  const permitted = new Set(allowed.map((code) => code.toUpperCase()));

  // Split on commas and whitespace, then take the outward code off anything that
  // looks like a full postcode: "E11 4EG" arrives as two tokens, and "4EG" alone
  // is not a district.
  const tokens = text.toUpperCase().split(/[,;\s]+/).map((t) => t.trim()).filter(Boolean);
  for (const token of tokens) {
    const cleaned = token.replace(/[^A-Z0-9]/g, "");
    if (!cleaned) continue;
    // An inward code — "4EG", "2ED" — is the second half of a postcode whose first
    // half we have already taken. Skipped rather than reported: complaining about
    // it would make pasting a full postcode feel like an error.
    if (/^\d[A-Z]{2}$/.test(cleaned)) continue;
    if (!OUTWARD.test(cleaned)) {
      badFormat.push(token);
    } else if (!permitted.has(cleaned)) {
      notCovered.push(cleaned);
    } else if (!codes.includes(cleaned)) {
      codes.push(cleaned);
    }
  }
  return { codes, badFormat, notCovered };
}

/** Districts typed at the first step, merged with whatever was already chosen. */
export function applyDistrictText(
  session: Session,
  text: string,
  context: Context,
): { ok: true; session: Session; added: string[] } | { ok: false; reason: string } {
  if (session.step !== "districts") return { ok: false, reason: "not at the district step" };

  const { codes, badFormat, notCovered } = readDistricts(text, context.districts);
  const complaints: string[] = [];
  if (badFormat.length) complaints.push(`not a district: ${badFormat.join(", ")}`);
  if (notCovered.length) complaints.push(`not covered yet: ${notCovered.join(", ")}`);

  if (!codes.length) {
    return {
      ok: false,
      reason: complaints.join("\n") || `I couldn't find a district in "${text.trim()}".`,
    };
  }

  const chosen = session.draft.areas?.postcode_districts ?? [];
  const merged = [...chosen];
  const added: string[] = [];
  for (const code of codes) {
    if (merged.includes(code)) continue;
    if (merged.length >= context.maxDistricts) {
      complaints.push(`your plan covers ${context.maxDistricts}, so ${code} was not added`);
      continue;
    }
    merged.push(code);
    added.push(code);
  }
  if (!added.length) {
    return { ok: false, reason: complaints.join("\n") || "those are already chosen." };
  }
  return { ok: true, session: { ...session, draft: withDistricts(session.draft, merged) }, added };
}

// ── actions, and the callback data that encodes them ──────────────────────

export type Action =
  | { kind: "district"; code: string }
  | { kind: "districtsDone" }
  | { kind: "bedrooms"; min: number }
  | { kind: "pets"; on: boolean }
  | { kind: "furnished"; value: string }
  | { kind: "skip" }
  | { kind: "finish" }
  | { kind: "restart" }
  | { kind: "overwrite"; yes: boolean }
  | { kind: "cancel" };

const FURNISHED_CODES: Record<string, string> = { f: "furnished", u: "unfurnished", p: "part" };
const FURNISHED_LETTER: Record<string, string> = { furnished: "f", unfurnished: "u", part: "p" };

/**
 * Decode `callback_data`. Returns null for anything unrecognised.
 *
 * Null rather than a throw, and null rather than a default: an unknown tap is
 * almost always a keyboard from an older version of the bot that is still sitting
 * in someone's chat, and guessing what its buttons used to mean would act on an
 * intention nobody had.
 */
export function parseCallback(data: string | undefined): Action | null {
  if (!data || !data.startsWith("w:")) return null;
  const [, verb = "", argument = ""] = data.split(":");
  switch (verb) {
    case "d":
      return argument ? { kind: "district", code: argument.toUpperCase() } : null;
    case "dn":
      return { kind: "districtsDone" };
    case "b": {
      const min = Number(argument);
      return Number.isInteger(min) && min >= 0 && min <= MAX_BEDROOMS_CHOICE
        ? { kind: "bedrooms", min }
        : null;
    }
    case "pe":
      return { kind: "pets", on: argument === "1" };
    case "f":
      return FURNISHED_CODES[argument] ? { kind: "furnished", value: FURNISHED_CODES[argument]! } : null;
    case "sk":
      return { kind: "skip" };
    case "ok":
      return { kind: "finish" };
    case "re":
      return { kind: "restart" };
    case "ow":
      return { kind: "overwrite", yes: argument === "1" };
    case "x":
      return { kind: "cancel" };
    default:
      return null;
  }
}

// ── the transition ────────────────────────────────────────────────────────

export type Outcome =
  /** Show the session's current step. */
  | { kind: "render"; session: Session }
  /** The tap was refused. The step does not move; the reason goes on the button. */
  | { kind: "reject"; session: Session; reason: string }
  /** Write the draft and end the wizard. */
  | { kind: "commit"; session: Session }
  /** Abandon without writing anything. */
  | { kind: "abandon"; session: Session };

/**
 * One tap, applied.
 *
 * Pure: no database, no network, no clock. Everything it needs about the plan and
 * the coverage arrives in `context`.
 *
 * A tap that belongs to a step the session is no longer on is refused rather than
 * applied. Old keyboards stay live in Telegram — clearing them is best-effort — so
 * this is the guard that stops a stale tap rewinding the conversation.
 */
export function apply(session: Session, action: Action, context: Context): Outcome {
  if (action.kind === "cancel") return { kind: "abandon", session };
  if (action.kind === "restart") {
    return { kind: "render", session: { ...session, step: "districts", draft: {} } };
  }

  switch (session.step) {
    case "overwrite": {
      if (action.kind !== "overwrite") return stale(session);
      if (!action.yes) return { kind: "abandon", session };
      // Starting from {} rather than from the current filter. "Set it up again"
      // means answering the questions again; carrying the old answers in silently
      // would make Skip mean "keep what was there", which is not what it says.
      return { kind: "render", session: { ...session, step: "districts", draft: {} } };
    }

    case "districts": {
      if (action.kind === "district") {
        if (!context.districts.includes(action.code)) {
          return { kind: "reject", session, reason: `${action.code} isn't covered yet` };
        }
        const chosen = session.draft.areas?.postcode_districts ?? [];
        const next = chosen.includes(action.code)
          ? chosen.filter((code) => code !== action.code)
          : [...chosen, action.code];
        if (next.length > context.maxDistricts) {
          return {
            kind: "reject",
            session,
            reason: `Your plan covers ${context.maxDistricts}. Tap one to remove it first.`,
          };
        }
        return {
          kind: "render",
          session: { ...session, draft: withDistricts(session.draft, next) },
        };
      }
      if (action.kind === "districtsDone") {
        if (!(session.draft.areas?.postcode_districts ?? []).length) {
          return { kind: "reject", session, reason: "Choose at least one district" };
        }
        return { kind: "render", session: { ...session, step: "bedrooms" } };
      }
      // Districts are the one thing with no "doesn't matter": a filter with no
      // area is every listing in London, which is not a filter.
      return stale(session);
    }

    case "bedrooms": {
      if (action.kind === "skip") return { kind: "render", session: advance(session) };
      if (action.kind !== "bedrooms") return stale(session);
      return {
        kind: "render",
        session: advance({ ...session, draft: { ...session.draft, bedrooms: { min: action.min } } }),
      };
    }

    case "price":
      // Answered by typing, not tapping — see `applyPriceText`. The only button on
      // this step is Skip.
      if (action.kind !== "skip") return stale(session);
      return { kind: "render", session: advance(session) };

    case "pets": {
      if (action.kind === "skip") return { kind: "render", session: advance(session) };
      if (action.kind !== "pets") return stale(session);
      const draft = { ...session.draft };
      // Only ever true or absent. `pets_allowed: false` means "listings that state
      // pets are NOT allowed", which is a search almost nobody wants and is not
      // what a person means by answering no.
      if (action.on) draft.pets_allowed = true;
      else delete draft.pets_allowed;
      return { kind: "render", session: advance({ ...session, draft }) };
    }

    case "furnished": {
      if (action.kind === "skip") return { kind: "render", session: advance(session) };
      if (action.kind !== "furnished") return stale(session);
      if (!FURNISHED.includes(action.value as (typeof FURNISHED)[number])) return stale(session);
      return {
        kind: "render",
        session: advance({ ...session, draft: { ...session.draft, furnished: [action.value] } }),
      };
    }

    case "confirm":
      if (action.kind !== "finish") return stale(session);
      return { kind: "commit", session };
  }
}

/**
 * The price step, which is typed rather than tapped.
 *
 * Kept separate from `apply` because its input is a message and not a button, and
 * because it is the only step that can be answered wrongly — a range needs a
 * reason when it is refused, where a button cannot be pressed wrongly at all.
 */
export function applyPriceText(
  session: Session,
  text: string,
  ): { ok: true; session: Session } | { ok: false; reason: string } {
  if (session.step !== "price") return { ok: false, reason: "not at the price step" };

  const range = parseRange(text);
  if (range === null) {
    return { ok: false, reason: `I couldn't read "${text.trim()}" as a price.` };
  }
  // `{}` is what "any" parses to. It means no price criterion, which is exactly
  // what Skip does, so it is accepted rather than refused.
  if (range.min === undefined && range.max === undefined) {
    return { ok: true, session: advance({ ...session, draft: stripPrice(session.draft) }) };
  }
  for (const value of [range.min, range.max]) {
    if (value === undefined) continue;
    if (value < PRICE_FLOOR || value > PRICE_CEILING) {
      return {
        ok: false,
        reason: `Rent has to be between £${PRICE_FLOOR} and £${PRICE_CEILING.toLocaleString("en-GB")} a month.`,
      };
    }
  }
  if (range.min !== undefined && range.max !== undefined && range.min > range.max) {
    return { ok: false, reason: "The minimum is above the maximum." };
  }
  return {
    ok: true,
    session: advance({ ...session, draft: { ...session.draft, price_pcm: range } }),
  };
}

function advance(session: Session): Session {
  const order = STEPS as readonly string[];
  const index = order.indexOf(session.step);
  const next = index < 0 || index === order.length - 1 ? "confirm" : order[index + 1]!;
  return { ...session, step: next as Step };
}

function stale(session: Session): Outcome {
  return {
    kind: "reject",
    session,
    reason: "That button is from an earlier step — use the one below.",
  };
}

function withDistricts(draft: Criteria, districts: string[]): Criteria {
  const next = { ...draft };
  if (districts.length) next.areas = { postcode_districts: districts };
  else delete next.areas;
  return next;
}

function stripPrice(draft: Criteria): Criteria {
  const next = { ...draft };
  delete next.price_pcm;
  return next;
}

// ── what each step looks like ─────────────────────────────────────────────

const SKIP: Button = { text: "Doesn't matter", callback_data: "w:sk" };

/**
 * The message and keyboard for a step.
 *
 * Pure, so the wording and the button layout are testable. The step number is in
 * the text on purpose: a wizard with no visible end is one people abandon.
 */
export function render(session: Session, context: Context): { text: string; keyboard: Keyboard } {
  const chosen = session.draft.areas?.postcode_districts ?? [];

  switch (session.step) {
    case "overwrite":
      return {
        text: [
          "You already have a filter:",
          "",
          describeCriteria(session.draft),
          "",
          "Setting up again replaces it. Your alerts so far are unaffected —",
          "you won't be sent listings you have already seen.",
        ].join("\n"),
        keyboard: [
          [{ text: "Replace it", callback_data: "w:ow:1" }],
          [{ text: "Keep it as it is", callback_data: "w:ow:0" }],
        ],
      };

    case "districts": {
      // A sample, not the list. Any already chosen come first so they stay visible
      // and removable once the sample no longer contains them.
      const sample = [
        ...chosen,
        ...context.districts.filter((code) => !chosen.includes(code)).slice(0, DISTRICT_BUTTONS),
      ];
      return {
        text: [
          "Step 1 of 5 — where?",
          "",
          chosen.length
            ? `Chosen: ${chosen.join(", ")}  (${chosen.length} of ${context.maxDistricts})`
            : `Choose up to ${context.maxDistricts} districts.`,
          "",
          "Type them and send — for example:",
          `${sample.slice(0, 2).join(", ") || "SE16, E14"}`,
          "",
          "A full postcode works too: E11 4EG counts as E11.",
          "Or tap one below. Tapping again removes it.",
        ].join("\n"),
        keyboard: [
          ...rows(
            sample.map((code) => ({
              text: chosen.includes(code) ? `✓ ${code}` : code,
              callback_data: `w:d:${code}`,
            })),
            3,
          ),
          [{ text: chosen.length ? "Done →" : "Choose at least one", callback_data: "w:dn" }],
        ],
      };
    }

    case "bedrooms":
      return {
        text: [
          "Step 2 of 5 — how many bedrooms, at least?",
          "",
          "0 includes studios.",
        ].join("\n"),
        keyboard: [
          rows(
            Array.from({ length: MAX_BEDROOMS_CHOICE + 1 }, (_, n) => ({
              text: String(n),
              callback_data: `w:b:${n}`,
            })),
            5,
          )[0]!,
          [SKIP],
        ],
      };

    case "price":
      return {
        text: [
          "Step 3 of 5 — rent per month?",
          "",
          "Send a range, for example:",
          "1500-2200   ·   2000 (means up to £2000)   ·   1500-",
          "",
          `Between £${PRICE_FLOOR} and £${PRICE_CEILING.toLocaleString("en-GB")}.`,
        ].join("\n"),
        keyboard: [[SKIP]],
      };

    case "pets":
      return {
        text: [
          "Step 4 of 5 — pets?",
          "",
          "This leaves out listings that say pets are not allowed.",
          "Listings that say nothing either way still come through, marked",
          "\"not stated\" — most of them say nothing, and excluding those would",
          "leave you with almost none.",
        ].join("\n"),
        keyboard: [
          [{ text: "Must allow pets", callback_data: "w:pe:1" }],
          [SKIP],
        ],
      };

    case "furnished":
      return {
        text: "Step 5 of 5 — furnishing?",
        keyboard: [
          ...rows(
            FURNISHED.map((value) => ({
              text: value === "part" ? "Part furnished" : value[0]!.toUpperCase() + value.slice(1),
              callback_data: `w:f:${FURNISHED_LETTER[value]}`,
            })),
            2,
          ),
          [SKIP],
        ],
      };

    case "confirm":
      return {
        text: [
          "This is your search:",
          "",
          describeCriteria(session.draft),
          "",
          "Only listings posted from now on are sent — nothing that is already",
          "on the market.",
        ].join("\n"),
        keyboard: [
          [{ text: "Finish — start my alerts", callback_data: "w:ok" }],
          [{ text: "Start over", callback_data: "w:re" }],
        ],
      };
  }
}

function rows(buttons: Button[], perRow: number): Keyboard {
  const out: Keyboard = [];
  for (let index = 0; index < buttons.length; index += perRow) {
    out.push(buttons.slice(index, index + perRow));
  }
  return out;
}

// ── persistence ───────────────────────────────────────────────────────────

type SessionRow = {
  chat_id: string;
  user_id: string | number | null;
  step: string;
  draft: Criteria | null;
  prompt_msg_id: string | null;
};

/** The live session for a chat, or null. An expired one counts as absent. */
export async function loadSession(chatId: string): Promise<Session | null> {
  const rows = await query<SessionRow>(
    `SELECT chat_id, user_id, step, draft, prompt_msg_id
       FROM wizard_sessions WHERE chat_id = $1 AND expires_at > now()`,
    [chatId],
  );
  const row = rows[0];
  if (!row) return null;
  return {
    chatId: row.chat_id,
    userId: row.user_id === null ? null : Number(row.user_id),
    step: row.step as Step,
    draft: row.draft ?? {},
    promptMsgId: row.prompt_msg_id,
  };
}

/**
 * Write the session, replacing whatever was there.
 *
 * `expires_at` is pushed forward on every save, so the hour is an hour of silence
 * rather than an hour from the start — being timed out halfway through answering
 * would be indefensible.
 */
export async function saveSession(session: Session): Promise<void> {
  await query(
    `INSERT INTO wizard_sessions
            (chat_id, user_id, step, draft, prompt_msg_id, expires_at)
     VALUES ($1, $2, $3, $4::jsonb, $5, now() + make_interval(mins => $6::int))
     ON CONFLICT (chat_id) DO UPDATE
        SET user_id       = EXCLUDED.user_id,
            step          = EXCLUDED.step,
            draft         = EXCLUDED.draft,
            prompt_msg_id = EXCLUDED.prompt_msg_id,
            updated_at    = now(),
            expires_at    = EXCLUDED.expires_at`,
    [
      session.chatId,
      session.userId,
      session.step,
      JSON.stringify(session.draft),
      session.promptMsgId,
      SESSION_TTL_MINUTES,
    ],
  );
}

export async function dropSession(chatId: string): Promise<void> {
  await query(`DELETE FROM wizard_sessions WHERE chat_id = $1`, [chatId]);
}

export type Committed = {
  userId: number;
  /** False when the filter is saved but the plan has run out, so nothing will be sent. */
  live: boolean;
  planName: string;
  planUntil: Date | null;
};

/**
 * Turn a finished draft into an account, a channel and a subscription.
 *
 * One transaction, because a subscription without a channel is a filter that can
 * never be delivered and a channel without a subscription is a chat that receives
 * nothing — either half on its own is a support conversation.
 *
 * `enforceLimits` runs again here, against the plan as it is at this instant. The
 * wizard already caps the district count, but the wizard is not the only way in
 * and a draft can sit for an hour while a plan changes underneath it.
 */
export async function commitSession(session: Session): Promise<Committed> {
  return transaction(async (run) => {
    const plans = await run(
      `SELECT key, display_name, max_districts, duration_days, price_pence
         FROM plans WHERE is_signup_default AND enabled`,
    );
    const signup = plans[0];
    if (!signup) throw new Error("no default sign-up plan is configured in `plans`");

    let userId = session.userId;
    let planName = String(signup.display_name);
    let planUntil: Date | null = null;
    let live = true;

    if (userId === null) {
      const created = await run(
        `INSERT INTO users (status, consent_at, consent_source, plan, plan_until, payment_ref)
         VALUES ('active', now(), 'telegram', $1,
                 CASE WHEN $2::int IS NULL THEN NULL
                      ELSE now() + make_interval(days => $2::int) END,
                 $3)
         RETURNING id, plan_until`,
        [signup.key, signup.duration_days, paymentRef()],
      );
      userId = Number(created[0]?.id);
      if (!Number.isFinite(userId)) throw new Error("user row was not created");
      planUntil = (created[0]?.plan_until as Date | null) ?? null;
    } else {
      // A returning subscriber. Reactivated, but the plan is deliberately left
      // alone: handing out a fresh trial on every /start would make the trial
      // unlimited for anyone who noticed, and the filter they are setting up is
      // worth keeping either way.
      const rows = await run(
        `UPDATE users u
            SET status = 'active',
                consent_at = coalesce(u.consent_at, now()),
                consent_source = coalesce(u.consent_source, 'telegram'),
                stopped_at = NULL
          WHERE u.id = $1
        RETURNING u.plan, u.plan_until,
                  (SELECT display_name FROM plans WHERE key = u.plan) AS plan_name`,
        [userId],
      );
      planUntil = (rows[0]?.plan_until as Date | null) ?? null;
      planName = String(rows[0]?.plan_name ?? planName);
    }

    live = planUntil === null || planUntil.getTime() > Date.now();

    const limits = await run(
      `SELECT p.max_districts FROM users u JOIN plans p ON p.key = u.plan WHERE u.id = $1`,
      [userId],
    );
    const criteria = enforceLimits(session.draft, {
      maxDistricts: Number(limits[0]?.max_districts ?? signup.max_districts),
    });

    // The chat may already be attached to a different account — the same person
    // returning after a /stop, or a chat reused. Moved rather than duplicated,
    // because `(channel, address)` is unique and an insert would simply fail.
    await run(
      `UPDATE user_channels SET user_id = $1, verified_at = now()
        WHERE channel = 'telegram' AND address = $2 AND user_id <> $1`,
      [userId, session.chatId],
    );
    await run(
      `INSERT INTO user_channels (user_id, channel, address, is_primary, verified_at)
       VALUES ($1, 'telegram', $2, true, now())
       ON CONFLICT (user_id, channel)
         DO UPDATE SET address = EXCLUDED.address, verified_at = now()`,
      [userId, session.chatId],
    );

    const label = (criteria.areas?.postcode_districts ?? []).join(", ") || "London";
    const existing = await run(
      `SELECT id FROM subscriptions WHERE user_id = $1 AND active ORDER BY id DESC LIMIT 1`,
      [userId],
    );
    if (existing[0]?.id === undefined) {
      await run(
        `INSERT INTO subscriptions (user_id, label, criteria, backfill_from)
         VALUES ($1, $2, $3::jsonb, now())`,
        [userId, label, JSON.stringify(criteria)],
      );
    } else {
      // `backfill_from` is left as it was. Moving it forward on an edit would be
      // defensible, but leaving it means a widened filter can still pick up
      // something posted an hour ago that now qualifies — and `notifications`
      // already prevents sending anything twice.
      await run(
        `UPDATE subscriptions SET criteria = $1::jsonb, label = $2 WHERE id = $3`,
        [JSON.stringify(criteria), label, existing[0].id],
      );
    }

    await run(`DELETE FROM wizard_sessions WHERE chat_id = $1`, [session.chatId]);
    return { userId: userId!, live, planName, planUntil };
  });
}

