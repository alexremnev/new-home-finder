import { NextResponse } from "next/server";

import type { Criteria } from "@/lib/criteria";
import { beginSubscription } from "@/lib/activate";
import { query, transaction } from "@/lib/db";
import { alreadyOnAnotherChannel, criteriaSet } from "@/lib/messages";
import { sendWhatsApp } from "@/lib/whatsapp";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ok = () => NextResponse.json({ ok: true });

// Meta calls this once with a challenge before it will deliver anything, and the
// answer has to be the bare token it sent.
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const expected = process.env.WA_VERIFY_TOKEN;
  if (
    expected &&
    url.searchParams.get("hub.mode") === "subscribe" &&
    url.searchParams.get("hub.verify_token") === expected
  ) {
    return new Response(url.searchParams.get("hub.challenge") ?? "", { status: 200 });
  }
  return new Response("no", { status: 403 });
}

type Message = { from?: string; type?: string; text?: { body?: string } };

const TOKEN = /\b([A-Za-z0-9_-]{16,})\b/;

export async function POST(request: Request): Promise<NextResponse> {
  // Meta signs the body with the app secret. Without checking it, anyone who
  // learns the url can claim to be any number.
  const raw = await request.text();
  if (!(await signed(request, raw))) {
    return NextResponse.json({ ok: false }, { status: 401 });
  }

  let messages: Message[] = [];
  try {
    const body = JSON.parse(raw) as {
      entry?: { changes?: { value?: { messages?: Message[] } }[] }[];
    };
    messages =
      body.entry?.flatMap((e) => e.changes?.flatMap((c) => c.value?.messages ?? []) ?? []) ??
      [];
  } catch {
    return ok();
  }

  for (const message of messages) {
    const number = (message.from ?? "").replace(/\D/g, "");
    if (!number) continue;

    // Whatever they said, saying anything opens the 24-hour window in which a
    // listing can be sent as free-form text with a photo, and for nothing.
    await query(
      `UPDATE user_channels SET last_inbound_at = now()
        WHERE channel = 'whatsapp' AND address = $1`,
      [number],
    );

    const reply = await handle(number, message.text?.body ?? "");
    if (reply) await sendWhatsApp(number, reply).catch(() => undefined);
  }

  return ok();
}

async function signed(request: Request, raw: string): Promise<boolean> {
  const secret = process.env.WA_APP_SECRET;
  if (!secret) return false;
  const header = request.headers.get("x-hub-signature-256") ?? "";
  if (!header.startsWith("sha256=")) return false;

  const { createHmac, timingSafeEqual } = await import("node:crypto");
  const expected = createHmac("sha256", secret).update(raw).digest();
  const given = Buffer.from(header.slice("sha256=".length), "hex");
  return given.length === expected.length && timingSafeEqual(given, expected);
}

async function handle(number: string, text: string): Promise<string | null> {
  const token = TOKEN.exec(text)?.[1];
  if (!token) {
    const known = await query<{ id: string }>(
      `SELECT user_id AS id FROM user_channels
        WHERE channel = 'whatsapp' AND address = $1 AND verified_at IS NOT NULL`,
      [number],
    );
    if (known.length) return null;

    return (
      "I do not have a search for this number yet. Set one up at " +
      "londonhomefinder.co.uk and press Connect to WhatsApp."
    );
  }

  type Claim = null | { taken: string } | { criteria: Criteria };

  const claimed: Claim = await transaction<Claim>(async (run) => {
    const rows = await run(
      `UPDATE user_tokens SET used_at = now()
        WHERE token = $1 AND purpose = 'whatsapp'
          AND used_at IS NULL AND expires_at > now()
        RETURNING user_id`,
      [token],
    );
    let userId = rows[0]?.user_id as number | undefined;
    if (userId === undefined) return null;

    // One search, one destination — the same rule as /start, enforced on both
    // sides rather than trusted to the interface.
    const elsewhere = await run(
      `SELECT channel FROM user_channels
        WHERE user_id = $1 AND channel <> 'whatsapp' AND verified_at IS NOT NULL
        LIMIT 1`,
      [userId],
    );
    if (elsewhere[0] !== undefined) {
      return { taken: String(elsewhere[0].channel) };
    }

    await run(
      `UPDATE users
          SET status = 'active',
              consent_at = coalesce(consent_at, now()),
              consent_source = coalesce(consent_source, 'whatsapp'),
              stopped_at = NULL
        WHERE id = $1`,
      [userId],
    );

    // The form makes a new user row every time, so a number already linked
    // belongs to somebody coming back. Their plan and payments live on the older
    // row: move the new filter across and drop the new row. Linking stops at the
    // channel boundary — a Telegram subscriber stays a separate account.
    const owner = await run(
      `SELECT user_id FROM user_channels
        WHERE channel = 'whatsapp' AND address = $1 AND user_id <> $2
        LIMIT 1`,
      [number, userId],
    );
    const existing = owner[0]?.user_id as number | undefined;

    if (existing !== undefined) {
      await run(`UPDATE subscriptions SET active = false WHERE user_id = $1 AND active`, [
        existing,
      ]);
      await run(`UPDATE subscriptions SET user_id = $1 WHERE user_id = $2`, [existing, userId]);
      await run(
        `DELETE FROM users u
          WHERE u.id = $1
            AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.user_id = u.id)
            AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.user_id = u.id)`,
        [userId],
      );
      userId = existing;
    }

    await run(
      `UPDATE user_channels SET user_id = $1, verified_at = now()
        WHERE channel = 'whatsapp' AND address = $2 AND user_id <> $1`,
      [userId, number],
    );
    await run(
      `INSERT INTO user_channels
              (user_id, channel, address, is_primary, verified_at, last_inbound_at)
       VALUES ($1, 'whatsapp', $2, true, now(), now())
       ON CONFLICT (user_id, channel)
         DO UPDATE SET address = EXCLUDED.address,
                       verified_at = now(),
                       last_inbound_at = now()`,
      [userId, number],
    );

    await beginSubscription(run, userId);

    const found = await run(
      `SELECT criteria FROM subscriptions
        WHERE user_id = $1 AND active ORDER BY created_at DESC LIMIT 1`,
      [userId],
    );
    return { criteria: (found[0]?.criteria ?? {}) as Criteria };
  });

  if (claimed === null) {
    return (
      "That link has expired. Please fill the form in again at " +
      "londonhomefinder.co.uk and use the new link — it takes a moment."
    );
  }
  if ("taken" in claimed) return alreadyOnAnotherChannel(claimed.taken);

  return criteriaSet(claimed.criteria);
}
