import { NextResponse } from "next/server";

import type { Criteria } from "@/lib/criteria";
import { beginSubscription } from "@/lib/activate";
import { query, transaction } from "@/lib/db";
import { COMMAND_HELP, parseCommand } from "@/lib/commands";
import {
  CHOOSE_PLAN_BUTTON,
  FOUND_A_PLACE,
  NO_FILTER_YET,
  NOTHING_TO_PAUSE,
  NOTHING_TO_RESUME,
  NOTHING_TO_STOP,
  PAUSED,
  RESUMED,
  STOPPED,
  CHANGE_FILTER,
  FILTERS_BUTTON,
  criteriaCard,
  LINK_EXPIRED,
  criteriaSet,
  alertsBackOn,
  ALERTS_STOPPED,
  FULL_ACCESS_BUTTON,
  planLine,
  upgradeInvitation,
  upgradeUrl,
} from "@/lib/messages";
import {
  accountForChat, filterUrl, issueToken, planIsLive, siteUrl,
  UPGRADE_TTL_MINUTES,
} from "@/lib/plans";
import { dismiss } from "@/lib/dismiss";
import { deleteFilter, resumeFilter, stopFilter } from "@/lib/stopping";
import { SUPPORT_REPLY } from "@/lib/support";
import { sendWhatsApp, type WaLink } from "@/lib/whatsapp";

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

/**
 * What one inbound message is answered with, or null to say nothing.
 *
 * `button` is a link drawn under the words rather than written out among them.
 * Every reply that points anywhere carries one — WhatsApp holds exactly one url
 * button per message, which is why this is a single link and not a keyboard.
 *
 * A bare string is the common case and stays a bare string; `linked` is how a
 * reply says it has somewhere to send them.
 */
type Reply = string | { text: string; button: WaLink };

const linked = (text: string, label: string, url: string): Reply => ({
  text,
  button: { label, url },
});

// The emoji come off the button labels: a cta_url label is cut at twenty
// characters and WhatsApp spends some of them on the glyph.
const plainLabel = (label: string) => label.replace(/[^\p{L}\p{N} ]/gu, "").trim();

/**
 * The two template quick replies, by the words printed on them.
 *
 * A belt to the payload's braces. A quick reply on a template carries whatever
 * payload the send put in it, which is what `tapped` reads and what everything
 * downstream matches on — but the payload is only there if the sender included
 * the button component, and the one thing it is certain to come back with is
 * the label, because that is fixed in the approved template itself.
 *
 * Worth the duplication for these two and no others. They are the buttons on
 * the notice a lapsed subscriber gets and on the receipt for money already
 * taken, so "the button did nothing" is the most expensive outcome in the
 * product. Everything else here is a free-form reply button, where the id is
 * set by the same code that draws it and cannot go missing.
 *
 * Kept in step with the templates by hand, which is why the words are quoted
 * from them exactly and the mismatch is harmless: a label that has drifted
 * falls through to the payload, which is the normal route anyway.
 */
const TEMPLATE_BUTTONS: Record<string, string> = {
  "get full access": "pay",
  "start my alerts": "paid",
};

function tapped(message: Message): string | null {
  const label = message.button?.text?.trim().toLowerCase() ?? "";
  return (
    message.interactive?.button_reply?.id ??
    message.button?.payload ??
    TEMPLATE_BUTTONS[label] ??
    null
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
    // listing can be sent as free-form text with a photo. Not for nothing —
    // Meta charges for every message now, which is why what goes back out
    // through here is metered. See 0063.
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
      // Every reply carries the site, unless it has a button of its own. One
      // place, so no command can be the one that forgets.
      const { text, button } = await withSite(number, reply);
      const sent = await sendWhatsApp(number, text, button).catch((error) => {
        console.error("wa reply threw", { from: number.slice(-4), error: String(error) });
        return false;
      });
      if (!sent) console.error("wa reply not delivered", { from: number.slice(-4) });
    }
  }

  return ok();
}

/**
 * A reply with a way back to the site on it, pointing at this person's own
 * filter rather than at the landing page.
 *
 * A button, not a line of url: a link written out in a chat message is a link
 * somebody has to select and copy. WhatsApp allows one per message, so a reply
 * that already has a button of its own — the payment link, the form — keeps it
 * and gets nothing added.
 *
 * The url is built only when it is going to be used: it costs a query and a
 * write.
 */
async function withSite(
  number: string,
  reply: Reply,
): Promise<{ text: string; button?: WaLink }> {
  if (typeof reply !== "string") return reply;
  if (reply.includes(siteUrl())) return { text: reply };
  return {
    text: reply,
    button: { label: "Open the site", url: await filterUrl(number, "whatsapp") },
  };
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
): Promise<Reply | null> {
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
      if (!account || account.subscription_id === null) return NO_FILTER_YET;
      return [
        criteriaCard((account.criteria ?? {}) as Criteria),
        "",
        planLine(account.plan_name, account.plan_until, planIsLive(account)),
        "",
        COMMAND_HELP,
      ].join("\n");
    }

    case "upgrade":
      return payOffer(number);

    case "start":
    case "update": {
      // A token on the link, so the form knows who is editing: it opens on
      // their current criteria and offers "back to WhatsApp" rather than the
      // sign-up offer. Without an account — or if issuing it fails — the plain
      // form is still the right page.
      return linked(
        CHANGE_FILTER,
        plainLabel(FILTERS_BUTTON),
        await filterUrl(number, "whatsapp"),
      );
    }

    default:
      return COMMAND_HELP;
  }
}

/**
 * What they are on now, what a plan costs, and the checkout link as a button.
 *
 * One function because two things ask for it and they must not drift: /pay
 * typed into the chat, and a tap on the notice template's quick reply. The tap
 * is the one that matters — see `NOTICE_TAP` in the worker's notifier — and it
 * arrives from somebody whose window was shut a moment ago, which is precisely
 * why the answer has to be built here rather than baked into the template: the
 * token is minted now and lives an hour, where a template button's token is
 * minted on the drain that sent it and may be most of a day old by the tap.
 */
async function payOffer(number: string): Promise<Reply> {
  const account = await accountForChat(number, "whatsapp");
  if (!account) return NO_FILTER_YET;
  const token = await issueToken(account.user_id, "upgrade", UPGRADE_TTL_MINUTES);
  return linked(
    await upgradeInvitation(account, token),
    plainLabel(CHOOSE_PLAN_BUTTON),
    upgradeUrl(token),
  );
}

async function pressed(number: string, id: string): Promise<Reply | null> {
  console.log("wa tap", { from: number.slice(-4), id });

  // The quick reply on the plan notice — the one button somebody whose plan
  // has ended is given, and the reason the notice carries a button that sends
  // something back rather than one that opens a browser.
  //
  // The tap has already reopened the 24-hour window, at the top of this route,
  // and that is worth more than the message it is about to be answered with: a
  // url button sends us nothing, so the window stayed shut through the payment
  // and Meta refused the receipt and held every alert behind it. By the time
  // the card clears here, free-form text works again.
  if (id === "pay") return payOffer(number);

  // The quick reply under the payment receipt — the template that reached them
  // when the window was shut, which is where a payment lands whenever the plan
  // it renews had already ended.
  //
  // The tap is the whole mechanism. A template cannot reopen the window and
  // sending one does not; only something inbound does, and this is it. So by
  // the time this runs the alerts held behind that window are already free to
  // go out on the next drain, and the answer only has to say so and show what
  // was bought.
  if (id === "paid") {
    const account = await accountForChat(number, "whatsapp").catch(() => null);
    if (!account) return NO_FILTER_YET;
    return alertsBackOn(
      account.plan_name,
      account.plan_until,
      (account.criteria ?? {}) as Criteria,
    );
  }

  // The answer to the check-in sent five minutes before the window shut. The
  // tap itself already reopened it — every inbound does, at the top of this
  // route — so everything held back goes out on the next drain, and this only
  // has to say so and name what is being searched for. Held includes the
  // alerts withheld while the question waited: the worker stops delivering
  // once it has asked, so that the question stays the last thing on screen.
  if (id === "continue") {
    const account = await accountForChat(number, "whatsapp").catch(() => null);
    if (!account || account.subscription_id === null) return NO_FILTER_YET;

    // The button outlives the message it is on. A digest from last week and a
    // check-in from yesterday both still carry it, and a plan can have run out
    // in between — at which point "anything held is on its way" is a promise of
    // a delivery the worker will not make, because a finished WhatsApp plan is
    // sent nothing at all. The tap still reopened the window, so there is one
    // thing worth doing with it: say the plan has ended and offer the plan.
    if (!planIsLive(account)) {
      const token = await issueToken(account.user_id, "upgrade", UPGRADE_TTL_MINUTES);
      return linked(ALERTS_STOPPED, FULL_ACCESS_BUTTON, upgradeUrl(token));
    }

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
    return linked(
      CHANGE_FILTER,
      plainLabel(FILTERS_BUTTON),
      await filterUrl(number, "whatsapp"),
    );
  }

  // Unknown. The tap has already reopened the window, which was most of the
  // value, but an id nobody handles means a button that visibly does nothing —
  // worth a log line rather than a silent 200.
  console.error("wa tap not handled", { id });
  return null;
}

async function handle(number: string, text: string): Promise<Reply | null> {
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

  // `replaced` is for the message at the end: whether a filter that was already
  // running has just been taken out of service by this one. Answered by the
  // UPDATE that does it rather than guessed at from the merge — a number that
  // was linked before is not the same thing as a filter that was live.
  type Claim = null | { criteria: Criteria; replaced: boolean };

  const claimed: Claim = await transaction<Claim>(async (run) => {
    let replaced = false;

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
      const stood = await run(
        `UPDATE subscriptions SET active = false
          WHERE user_id = $1 AND active
          RETURNING id`,
        [existing],
      );
      replaced = stood.length > 0;

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
    return { criteria: (found[0]?.criteria ?? {}) as Criteria, replaced };
  });

  if (claimed === null) {
    console.log("wa inbound: token not claimable — used, expired, or not ours");
    return LINK_EXPIRED;
  }
  console.log("wa inbound: linked");

  return criteriaSet(claimed.criteria, claimed.replaced);
}
