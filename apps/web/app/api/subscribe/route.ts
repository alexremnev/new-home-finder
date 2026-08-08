// The form's endpoint: create a pending subscription and hand back the link that
// connects it to a Telegram chat.
//
// Nothing here is sendable yet. The user is `pending` and has no channel, so the
// matcher's join excludes them entirely until the webhook sees a START. That is
// the point: a subscription that could receive messages before its owner has
// messaged the bot would be a subscription without provable consent.

import { randomBytes } from "node:crypto";

import { NextResponse } from "next/server";

import { InvalidForm, parseForm } from "@/lib/criteria";
import { query, transaction } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// Long enough that guessing one is hopeless, short enough to sit in a URL.
const TOKEN_BYTES = 24;
const TOKEN_TTL_MINUTES = 60;

export async function POST(request: Request): Promise<NextResponse> {
  let form: Record<string, unknown>;
  try {
    form = await readForm(request);
  } catch {
    return NextResponse.json({ error: "could not read the form" }, { status: 400 });
  }

  const districts = await enabledDistricts();
  if (!districts.length) {
    // Better than accepting a subscription that can never match: nothing is
    // being collected, so there is nothing to promise.
    return NextResponse.json({ error: "no districts are being covered yet" }, { status: 503 });
  }

  let parsed;
  try {
    parsed = parseForm(form, districts);
  } catch (error) {
    if (error instanceof InvalidForm) {
      return NextResponse.json({ error: error.message }, { status: 400 });
    }
    throw error;
  }

  const token = randomBytes(TOKEN_BYTES).toString("base64url");

  await transaction(async (run) => {
    const users = await run(
      `INSERT INTO users (status, consent_source, start_token, token_expires_at)
       VALUES ('pending', 'web_form', $1, now() + make_interval(mins => $2))
       RETURNING id`,
      [token, TOKEN_TTL_MINUTES],
    );
    const userId = users[0]?.id;
    if (userId === undefined) throw new Error("user row was not created");

    await run(
      `INSERT INTO subscriptions (user_id, label, criteria, max_alerts_per_day, backfill_from)
       VALUES ($1, $2, $3::jsonb, $4, now())`,
      [
        userId,
        (parsed.criteria.areas?.postcode_districts ?? []).join(", ") || "London",
        JSON.stringify(parsed.criteria),
        parsed.maxAlertsPerDay,
      ],
    );
  });

  const bot = process.env.TELEGRAM_BOT_USERNAME;
  if (!bot) throw new Error("TELEGRAM_BOT_USERNAME is not set");

  return NextResponse.json({
    ok: true,
    // `backfill_from` is now, so the existing market is not replayed. Said out
    // loud because the first thing a new subscriber notices is silence.
    note: "Only listings that appear from now on will be sent.",
    url: `https://t.me/${bot}?start=${token}`,
    expires_in_minutes: TOKEN_TTL_MINUTES,
  });
}

async function readForm(request: Request): Promise<Record<string, unknown>> {
  const type = request.headers.get("content-type") ?? "";
  if (type.includes("application/json")) {
    return (await request.json()) as Record<string, unknown>;
  }
  const data = await request.formData();
  const out: Record<string, unknown> = {};
  for (const key of new Set(data.keys())) {
    const values = data.getAll(key).map((v) => String(v));
    // Checkbox groups arrive repeated; a single value must not become a
    // one-element array, because the parser distinguishes the two.
    out[key] = values.length > 1 ? values : values[0];
  }
  return out;
}

/**
 * The districts a subscription may name.
 *
 * Read from the database, not from a constant here, so that widening coverage
 * stays an UPDATE to source_locations rather than a deploy of the web app.
 */
async function enabledDistricts(): Promise<string[]> {
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
