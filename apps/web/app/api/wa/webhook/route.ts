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
  CHANGE_FILTER,
  criteriaCard,
  LINK_EXPIRED,
  withSiteLink,
  criteriaSet,
  noFilterYet,
  planLine,
  upgradeInvitation,
} from "@/lib/messages";
import {
  accountForChat, filterUrl, issueToken, planIsLive, siteUrl,
  UPGRADE_TTL_MINUTES,
} from "@/lib/plans";
import { dismiss } from "@/lib/dismiss";
import { deleteFilter, resumeFilter, stopFilter } from "@/lib/stopping";
import { SUPPORT_REPLY } from "@/lib/support";
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
      // Every reply carries the site, unless it already names it. One place,
      // so no command can be the one that forgets.
      const body = await withSite(number, reply);
      const sent = await sendWhatsApp(number, body).catch((error) => {
        console.error("wa reply threw", { from: number.slice(-4), error: String(error) });
        return false;
      });
      if (!sent) console.error("wa reply not delivered", { from: number.slice(-4) });
    }
  }

  return ok();
}

/**
 * A reply with the site on the end of it, the link pointing at this person's
 * own filter rather than at the landing page.
 *
 * The url is built only when it is going to be used: it costs a query and a
 * write, and a message that already names the address — /update, which carries
 * its own link — has no use for a second one.
 */
async function withSite(number: string, text: string): Promise<string> {
  if (text.includes(siteUrl())) return text;
  return withSiteLink(text, await filterUrl(number, "whatsapp"));
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

  switch (command.kind) {
    case "stop": {
      const gone = await deleteFilter("whatsapp", number);
      return gone ? STOPPED : NOTHING_TO_STOP;
    }

    case "support":
      return SUPPORT_REPLY;

    case "cancel":
      return COMMAND_HELP;

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
    case "update": {
      // A token on the link, so the form knows who is editing: it opens on
      // their current criteria and offers "back to WhatsApp" rather than the
      // sign-up offer. Without an account — or if issuing it fails — the plain
      // form is still the right page.
      return `${CHANGE_FILTER}\n${await filterUrl(number, "whatsapp")}`;
    }

    default:
      return COMMAND_HELP;
  }
}

async function pressed(number: string, id: string): Promise<string | null> {
  console.log("wa tap", { from: number.slice(-4), id });

  // The answer to the check-in sent five minutes before the window shut. The
  // tap itself already reopened it — every inbound does, at the top of this
  // route — so everything held back goes out on the next drain, and this only
  // has to say so and name what is being searched for. Held includes the
  // alerts withheld while the question waited: the worker stops delivering
  // once it has asked, so that the question stays the last thing on screen.
  if (id === "continue") {
    const account = await accountForChat(number, "whatsapp").catch(() => null);
    if (!account || account.subscription_id === null) return noFilterYet(siteUrl());
    return [
      "✅ Alerts are back on — anything held while it was quiet is on its way.",
      "",
      criteriaCard((account.criteria ?? {}) as Criteria),
      "",
      COMMAND_HELP,
    ].join("\n");
  }

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
      ? "👍 Noted — that one will not come up again."
      : "That listing is no longer one of yours.";
  }

  if (id === "change") {
    // The same tokenised link as /update. Without it, the one button on the
    // evening digest that offers to change the search landed on a blank
    // sign-up form.
    return `${CHANGE_FILTER}\n${await filterUrl(number, "whatsapp")}`;
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
        "🎯 I don't have a search for this number yet — set one up and press " +
        "Connect WhatsApp."
      );
    }
    return commanded(number, Number(known[0]?.id), text);
  }

  type Claim = null | { criteria: Criteria };

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
                       is_primary = true,
                       verified_at = now(),
                       last_inbound_at = now()`,
      [userId, number],
    );
    // Exactly one primary per account — see the same clause in the Telegram
    // webhook. Two primaries would match one search twice and deliver it twice.
    await run(
      `UPDATE user_channels SET is_primary = false
        WHERE user_id = $1 AND channel <> 'whatsapp' AND is_primary`,
      [userId],
    );

    await beginSubscription(run, userId, "whatsapp");

    const found = await run(
      `SELECT criteria FROM subscriptions
        WHERE user_id = $1 AND active ORDER BY created_at DESC LIMIT 1`,
      [userId],
    );
    return { criteria: (found[0]?.criteria ?? {}) as Criteria };
  });

  if (claimed === null) {
    console.log("wa inbound: token not claimable — used, expired, or not ours");
    return LINK_EXPIRED;
  }
  console.log("wa inbound: linked");

  return criteriaSet(claimed.criteria);
}
