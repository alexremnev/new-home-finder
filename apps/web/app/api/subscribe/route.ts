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
  whatsappLink,
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

    return NextResponse.json({ error: "no districts are being covered yet" }, { status: 503 });
  }

  const plan = await signupPlan();

  // The button they pressed. One search goes to one app, so there is one token
  // and one link — the other messenger is a separate search, filled in again.
  const channel = form.channel === "whatsapp" ? "whatsapp" : "telegram";
  // Both, because the form draws the button on both and the link is built from
  // WHATSAPP_NUMBER: one without the other would return a null url and surface
  // as "something went wrong".
  if (
    channel === "whatsapp" &&
    !(process.env.WA_PHONE_NUMBER_ID && process.env.WHATSAPP_NUMBER)
  ) {
    return NextResponse.json(
      { error: "WhatsApp is not switched on yet — please use Telegram" },
      { status: 400 },
    );
  }

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
       VALUES ('pending', 'web_form', $1, NULL, $2)
       RETURNING id`,
      [plan.key, paymentRef()],
    );
    const userId = users[0]?.id;
    if (userId === undefined) throw new Error("user row was not created");

    // Inactive, and the trial not begun: both start when a messenger is
    // connected, so an abandoned form leaves nothing that counts as a
    // subscriber and nobody loses trial days waiting to press the button.
    await run(
      `INSERT INTO subscriptions (user_id, label, criteria, backfill_from, active)
       VALUES ($1, $2, $3::jsonb, now(), false)`,
      [
        userId,
        (criteria.areas?.postcode_districts ?? []).join(", ") || "London",
        JSON.stringify(criteria),
      ],
    );
    await run(
      `INSERT INTO user_tokens (token, user_id, purpose, expires_at)
       VALUES ($1, $2, $4, now() + make_interval(mins => $3::int))`,
      [token, userId, START_TTL_MINUTES, channel === "whatsapp" ? "whatsapp" : "start"],
    );
  });

  return NextResponse.json({
    ok: true,

    note: "Only listings that appear from now on will be sent.",
    plan: plan.display_name,
    trial_days: plan.duration_days,
    channel,
    url: channel === "whatsapp" ? whatsappLink(token) : botLink(token),
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

    out[key] = values.length > 1 ? values : values[0];
  }
  return out;
}
