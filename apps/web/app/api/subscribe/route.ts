// The form's endpoint: create a pending subscription and hand back the link that
// connects it to a Telegram chat.
//
// Nothing here is sendable yet. The user is `pending` and has no channel, so the
// matcher's join excludes them entirely until the webhook sees a START. That is
// the point: a subscription that could receive messages before its owner has
// messaged the bot would be a subscription without provable consent.
//
// The sign-up plan and its limits come from the `plans` table. Nothing in this
// file knows how many districts a trial covers or how long it lasts.

import { NextResponse } from "next/server";

import { enforceLimits, InvalidForm, parseForm } from "@/lib/criteria";
import { transaction } from "@/lib/db";
import {
  botLink,
  enabledDistricts,
  newToken,
  paymentRef,
  signupPlan,
  START_TTL_MINUTES,
} from "@/lib/plans";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

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

  const plan = await signupPlan();

  let criteria;
  try {
    criteria = enforceLimits(parseForm(form, districts), {
      maxDistricts: plan.max_districts,
    });
  } catch (error) {
    if (error instanceof InvalidForm) {
      return NextResponse.json({ error: error.message }, { status: 400 });
    }
    throw error;
  }

  const token = newToken();

  await transaction(async (run) => {
    const users = await run(
      `INSERT INTO users (status, consent_source, plan, plan_until, payment_ref)
       VALUES ('pending', 'web_form', $1,
               CASE WHEN $2::int IS NULL THEN NULL
                    ELSE now() + make_interval(days => $2::int) END,
               $3)
       RETURNING id`,
      [plan.key, plan.duration_days, paymentRef()],
    );
    const userId = users[0]?.id;
    if (userId === undefined) throw new Error("user row was not created");

    await run(
      `INSERT INTO subscriptions (user_id, label, criteria, backfill_from)
       VALUES ($1, $2, $3::jsonb, now())`,
      [
        userId,
        (criteria.areas?.postcode_districts ?? []).join(", ") || "London",
        JSON.stringify(criteria),
      ],
    );
    await run(
      `INSERT INTO user_tokens (token, user_id, purpose, expires_at)
       VALUES ($1, $2, 'start', now() + make_interval(mins => $3::int))`,
      [token, userId, START_TTL_MINUTES],
    );
  });

  return NextResponse.json({
    ok: true,
    // `backfill_from` is now, so the existing market is not replayed. Said out
    // loud because the first thing a new subscriber notices is silence.
    note: "Only listings that appear from now on will be sent.",
    plan: plan.display_name,
    trial_days: plan.duration_days,
    url: botLink(token),
    expires_in_minutes: START_TTL_MINUTES,
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
