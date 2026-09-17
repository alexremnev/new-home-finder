import { NextResponse } from "next/server";

import type { Criteria } from "@/lib/criteria";
import { beginSubscription } from "@/lib/activate";
import { query, transaction } from "@/lib/db";
import { COMMAND_HELP, parseCommand } from "@/lib/commands";
import {
  FOUND_A_PLACE,
  NOTHING_TO_PAUSE,
  NOTHING_TO_RESUME,
  NOTHING_TO_STOP,
  PAUSED,
  RESUMED,
  STOPPED,
  alreadyOnAnotherChannel,
  criteriaCard,
  criteriaSet,
  noFilterYet,
  planLine,
  upgradeInvitation,
} from "@/lib/messages";
import {
  accountForChat, issueToken, planIsLive, siteUrl, UPGRADE_TTL_MINUTES,
} from "@/lib/plans";
import { dismiss } from "@/lib/dismiss";
import { deleteFilter, resumeFilter, stopFilter } from "@/lib/stopping";
import {
  BODY_LIMIT,
  SUPPORT_ASK_EMAIL,
  SUPPORT_BAD_EMAIL,
  SUPPORT_CANCELLED,
  SUPPORT_DONE,
  SUPPORT_PROMPT,
  SUPPORT_TOO_LONG,
  abandonDraft,
  openDraft,
  readEmail,
  recordBody,
  startDraft,
  submit,
} from "@/lib/support";
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

type Message = {
  from?: string;
  type?: string;
  text?: { body?: string };
  // A free-form message's reply button.
  interactive?: { type?: string; button_reply?: { id?: string; title?: string } };
  // A template's quick reply.
  button?: { payload?: string; text?: string };
};

function tapped(message: Message): string | null {
  return (
    message.interactive?.button_reply?.id ?? message.button?.payload ?? null
  );
}

// No word boundaries. newToken() is base64url, whose alphabet includes "-",
// which is not a word character — so \b chopped a leading or trailing hyphen off
// roughly one token in thirty and claimed the wrong one. The token is 32
// characters; the longest run of its alphabet in the message is it.
const TOKEN_RUN = /[A-Za-z0-9_-]{20,}/g;

function tokenIn(text: string): string | undefined {
  const runs = text.match(TOKEN_RUN);
  if (!runs) return undefined;
  return runs.reduce((longest, one) => (one.length > longest.length ? one : longest));
}

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

  // A delivery receipt carries `statuses`, not `messages`, and produces a 200
  // with nothing done — indistinguishable in the log from a message that was
  // handled, which is an unpleasant hour to spend.
  console.log("wa webhook", { messages: messages.length, bytes: raw.length });
  if (messages.length === 0) {
    console.log("wa webhook: nothing to act on (a status callback, most likely)");
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

    // A tap is the only thing that reopens the free window, so it is handled
    // before anything else and always answered.
    const tap = tapped(message);
    const reply = tap
      ? await pressed(number, tap)
      : await handle(number, message.text?.body ?? "");
    if (reply) {
      // Never swallowed. A tap that was understood but whose answer could not
      // be delivered is indistinguishable, from the outside, from a button
      // that does nothing — and the webhook still returns 200 either way.
      const sent = await sendWhatsApp(number, reply).catch((error) => {
        console.error("wa reply threw", { from: number.slice(-4), error: String(error) });
        return false;
      });
      if (!sent) console.error("wa reply not delivered", { from: number.slice(-4) });
    }
  }

  return ok();
}

async function signed(request: Request, raw: string): Promise<boolean> {
  const secret = process.env.WA_APP_SECRET;
  if (!secret) {
    // Silently refusing every delivery is indistinguishable from Meta never
    // calling, which is a bad hour to spend. Say which it is.
    console.error("wa webhook refused: WA_APP_SECRET is not set in this deployment");
    return false;
  }
  const header = request.headers.get("x-hub-signature-256") ?? "";
  if (!header.startsWith("sha256=")) {
    console.error("wa webhook refused: no x-hub-signature-256 header", {
      headers: [...request.headers.keys()].join(","),
    });
    return false;
  }

  const { createHmac, timingSafeEqual } = await import("node:crypto");
  const expected = createHmac("sha256", secret).update(raw).digest();
  const given = Buffer.from(header.slice("sha256=".length), "hex");
  const same = given.length === expected.length && timingSafeEqual(given, expected);
  if (!same) console.error("wa webhook refused: signature does not match WA_APP_SECRET");
  return same;
}

// Everything a subscriber can say. This did not exist: whatever they typed was
// met with silence, which meant /stop from WhatsApp did nothing at all — an
// opt-out that works on one channel only is not an opt-out.
async function commanded(
  number: string,
  userId: number,
  text: string,
): Promise<string | null> {
  const command = parseCommand(text);
  console.log("wa command", { from: number.slice(-4), kind: command.kind });

  // An unfinished support ticket swallows plain messages, and a command must
  // always win — the same rule as Telegram.
  if (!text.trim().startsWith("/")) {
    const draft = await openDraft("whatsapp", number);
    if (draft) return continueTicket(draft, text.trim());
  }
  if (text.trim().startsWith("/")) await abandonDraft("whatsapp", number);

  switch (command.kind) {
    case "stop": {
      const gone = await deleteFilter("whatsapp", number);
      return gone ? STOPPED : NOTHING_TO_STOP;
    }

    case "support":
      await startDraft("whatsapp", number, userId);
      return SUPPORT_PROMPT;

    case "cancel": {
      const had = await abandonDraft("whatsapp", number);
      return had ? SUPPORT_CANCELLED : COMMAND_HELP;
    }

    case "resume": {
      const woken = await resumeFilter("whatsapp", number);
      return woken ? RESUMED : NOTHING_TO_RESUME;
    }

    case "show": {
      const account = await accountForChat(number, "whatsapp");
      if (!account || account.subscription_id === null) return noFilterYet(siteUrl());
      return [
        criteriaCard((account.criteria ?? {}) as Criteria),
        "",
        planLine(account.plan_name, account.plan_until, planIsLive(account)),
        "",
        COMMAND_HELP,
      ].join("\n");
    }

    case "upgrade": {
      const account = await accountForChat(number, "whatsapp");
      if (!account) return noFilterYet(siteUrl());
      const token = await issueToken(account.user_id, "upgrade", UPGRADE_TTL_MINUTES);
      return await upgradeInvitation(account, token);
    }

    case "start":
    case "update":
      return [
        "Change your search here — it takes a minute:",
        "",
        `${siteUrl()}/`,
        "",
        "Saving replaces this filter. Until you do, it carries on as it is.",
      ].join("\n");

    default:
      return COMMAND_HELP;
  }
}

async function continueTicket(
  draft: { id: number; status: "awaiting_body" | "awaiting_email" },
  said: string,
): Promise<string | null> {
  if (draft.status === "awaiting_body") {
    if (!said) return null;
    if (said.length > BODY_LIMIT) return SUPPORT_TOO_LONG;
    await recordBody(draft.id, said);
    return SUPPORT_ASK_EMAIL;
  }

  const email = readEmail(said);
  if (email === "invalid") return SUPPORT_BAD_EMAIL;
  await submit(draft.id, email);
  return SUPPORT_DONE;
}

async function pressed(number: string, id: string): Promise<string | null> {
  console.log("wa tap", { from: number.slice(-4), id });

  if (id === "pause" || id === "found") {
    const stopped = await stopFilter(
      "whatsapp",
      number,
      id === "found" ? "found_a_place" : "paused",
    );
    if (!stopped) return NOTHING_TO_PAUSE;
    return id === "found" ? FOUND_A_PLACE : PAUSED;
  }

  if (id.startsWith("ignore:")) {
    const noted = await dismiss("whatsapp", number, Number(id.slice("ignore:".length)));
    // WhatsApp has no way to delete a message it has already delivered — the
    // Cloud API simply does not offer it — so the listing stays on screen and
    // the reply says what actually happened instead of pretending.
    return noted
      ? "Noted — that one will not come up again."
      : "That listing is no longer one of yours.";
  }

  if (id === "change") {
    return [
      "Change your search here — it takes a minute:",
      "",
      `${siteUrl()}/`,
      "",
      "Saving replaces this filter. Until you do, it carries on as it is.",
    ].join("\n");
  }

  // Unknown. The tap has already reopened the window, which was most of the
  // value, but an id nobody handles means a button that visibly does nothing —
  // worth a log line rather than a silent 200.
  console.error("wa tap not handled", { id });
  return null;
}

async function handle(number: string, text: string): Promise<string | null> {
  const token = tokenIn(text);
  // The token itself is a live credential and is never logged; its length is
  // enough to tell "they typed something else" from "the link was mangled".
  console.log("wa inbound", { from: number.slice(-4), chars: text.length,
                              token: token ? token.length : 0 });

  if (!token) {
    const known = await query<{ id: string }>(
      `SELECT user_id AS id FROM user_channels
        WHERE channel = 'whatsapp' AND address = $1 AND verified_at IS NOT NULL`,
      [number],
    );
    if (known.length === 0) {
      return (
        "I do not have a search for this number yet. Set one up at " +
        "londonhomefinder.co.uk and press Connect to WhatsApp."
      );
    }
    return commanded(number, Number(known[0]?.id), text);
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
    console.log("wa inbound: token not claimable — used, expired, or not ours");
    return (
      "That link has expired. Please fill the form in again at " +
      "londonhomefinder.co.uk and use the new link — it takes a moment."
    );
  }
  if ("taken" in claimed) {
    console.log("wa inbound: that search already goes to", claimed.taken);
    return alreadyOnAnotherChannel(claimed.taken);
  }

  console.log("wa inbound: linked");

  return criteriaSet(claimed.criteria);
}
