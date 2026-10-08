import { NextResponse } from "next/server";

import { BOT_MENU, COMMAND_HELP, parseCommand } from "@/lib/commands";
import { enforceLimits, InvalidForm, type Criteria } from "@/lib/criteria";
import { beginSubscription } from "@/lib/activate";
import { dismiss } from "@/lib/dismiss";
import {
  deleteFilter, resumeFilter, stopFilter, type Reason,
} from "@/lib/stopping";
import { query, transaction } from "@/lib/db";
import { SUPPORT_REPLY } from "@/lib/support";
import {
  CHANGE_FILTER,
  FILTERS_BUTTON,
  FOUND_A_PLACE,
  LINK_EXPIRED,
  NOTHING_TO_PAUSE,
  NOTHING_TO_RESUME,
  NOTHING_TO_STOP,
  PAUSED,
  RESUMED,
  SET_FILTERS,
  STOPPED,
  criteriaCard,
  criteriaSet,
  noFilterYet,
  planLine,
  upgradeInvitation,
} from "@/lib/messages";
import {
  type Account,
  accountForChat,
  enabledDistricts,
  filterUrl,
  issueToken,
  limitsOf,
  planIsLive,
  signupPlan,
  siteUrl,
  UPGRADE_TTL_MINUTES,
} from "@/lib/plans";
import {
  answerCallback,
  chatIdOf,
  deleteMessage,
  type Keyboard,
  sendMessage,
  setMyCommands,
  type Update,
} from "@/lib/telegram";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ok = () => NextResponse.json({ ok: true });

export async function POST(request: Request): Promise<NextResponse> {
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
  if (!secret || request.headers.get("x-telegram-bot-api-secret-token") !== secret) {

    return NextResponse.json({ ok: false }, { status: 401 });
  }

  let update: Update;
  try {
    update = (await request.json()) as Update;
  } catch {
    return ok();
  }

  const chatId = chatIdOf(update);
  if (!chatId) return ok();

  try {
    if (update.callback_query) {
      await pressed(chatId, update.callback_query);
    } else {
      await handle(chatId, update.message?.text);
    }
  } catch (error) {
    console.error("webhook failed", { update_id: update.update_id, error: String(error) });

    if (update.callback_query?.id) {
      await answerCallback(update.callback_query.id, "Something went wrong").catch(() => undefined);
    }
    await reply(chatId, "Something went wrong on my side. Please try again in a moment.")
      .catch(() => undefined);
  }
  return ok();
}

// Every message this bot sends, except a listing alert, offers a way back to
// the site — as a button, because a url in the body is a url somebody has to
// copy. Done here rather than at each call site, so no command can be the one
// that forgets.
//
// Two things are left alone: a message that already carries a url button (the
// upgrade offer, the filter form), because a second button to the same place is
// noise; and a body that already names the address.
//
// The link carries an edit token, so the form opens on the filter this person
// already has: arriving from the bot and being shown the defaults is being
// asked to type a search out again from memory. Built only when the button is
// actually added, because it costs a query and a write and a message that
// already points at the site has no use for it.
async function reply(
  chatId: string,
  text: string,
  keyboard?: Keyboard,
): Promise<boolean> {
  const site = siteUrl();
  const hasLink =
    text.includes(site) ||
    (keyboard ?? []).some((row) => row.some((button) => Boolean(button.url)));
  const rows: Keyboard = [...(keyboard ?? [])];
  if (!hasLink) {
    rows.push([{ text: "🌐 Open the site", url: await filterUrl(chatId) }]);
  }
  return sendMessage(chatId, text, rows.length ? rows : undefined);
}

async function handle(chatId: string, text: string | undefined): Promise<void> {
  const said = (text ?? "").trim();

  // A command always wins. An unfinished ticket swallows plain messages, and
  const command = parseCommand(text);

  if (command.kind === "support") {
    await reply(chatId, SUPPORT_REPLY);
    return;
  }
  if (command.kind === "cancel") {
    await reply(chatId, COMMAND_HELP);
    return;
  }

  if (command.kind === "stop") return stop(chatId);
  if (command.kind === "start") return start(chatId, command.token);
  if (command.kind === "menu") return pushMenu(chatId);

  if (command.kind === "update") return sendToForm(chatId, true);

  if (command.kind === "resume") return resume(chatId);

  const account = await accountForChat(chatId);
  if (!account) {

    return sendToForm(chatId, false);
  }

  switch (command.kind) {
    case "show": {
      if (account.subscription_id === null) {
        await reply(chatId, noFilterYet(siteUrl()));
        return;
      }
      const body = [
        criteriaCard((account.criteria ?? {}) as Criteria),
        "",
        planLine(account.plan_name, account.plan_until, planIsLive(account)),
        "",
        COMMAND_HELP,
      ].join("\n");
      await reply(chatId, body);
      return;
    }

    case "upgrade":
      return offerUpgrade(chatId, account);

    case "grant":
      await grant(chatId, command.ref, command.plan, command.days);
      return;

    default:
      await reply(chatId, command.reason ? `${command.reason}\n${COMMAND_HELP}` : COMMAND_HELP);
  }
}

async function start(chatId: string, token: string | null): Promise<void> {

  if (token === "pay") {
    const account = await accountForChat(chatId);
    if (account) return offerUpgrade(chatId, account);

    return sendToForm(chatId, false);
  }

  if (!token) {

    const account = await accountForChat(chatId);
    if (account?.subscription_id != null) return sendToForm(chatId, true);
    return sendToForm(chatId, false);
  }

  // `replaced` is for the message at the end: whether a filter that was already
  // running has just been taken out of service by this one. Answered by the
  // UPDATE that does it rather than guessed at from the merge — a chat that was
  // linked before is not the same thing as a filter that was live.
  type Claim = null | { userId: number; replaced: boolean };

  const claimed: Claim = await transaction<Claim>(async (run) => {
    let replaced = false;

    const rows = await run(
      `UPDATE user_tokens SET used_at = now()
        WHERE token = $1 AND purpose = 'start' AND used_at IS NULL AND expires_at > now()
        RETURNING user_id`,
      [token],
    );
    let userId = rows[0]?.user_id;
    if (userId === undefined) return null;

    await run(
      `UPDATE users
          SET status = 'active',
              consent_at = coalesce(consent_at, now()),
              consent_source = 'telegram',
              stopped_at = NULL
        WHERE id = $1`,
      [userId],
    );

    const owner = await run(
      `SELECT user_id FROM user_channels
        WHERE channel = 'telegram' AND address = $1 AND user_id <> $2
        LIMIT 1`,
      [chatId, userId],
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
        WHERE channel = 'telegram' AND address = $2 AND user_id <> $1`,
      [userId, chatId],
    );
    await run(
      `INSERT INTO user_channels (user_id, channel, address, is_primary, verified_at)
       VALUES ($1, 'telegram', $2, true, now())
       ON CONFLICT (user_id, channel)
         DO UPDATE SET address = EXCLUDED.address,
                       is_primary = true,
                       verified_at = now()`,
      [userId, chatId],
    );
    // Exactly one primary per account. Connecting a second messenger is no
    // longer refused, so this is what stops one search being matched twice and
    // therefore delivered twice: the messenger just connected takes over, and
    // the other stops receiving. Two independent searches are still two
    // accounts, each with its own channel — which is what the separate trials
    // and prices are for.
    await run(
      `UPDATE user_channels SET is_primary = false
        WHERE user_id = $1 AND channel <> 'telegram' AND is_primary`,
      [userId],
    );

    await beginSubscription(run, Number(userId));
    return { userId: Number(userId), replaced };
  });

  if (claimed === null) {
    await reply(chatId, LINK_EXPIRED);
    return;
  }
  // Read back rather than trust the form: this is what the filter will actually
  // match on, after the district limit and the rest of enforceLimits.
  const account = await accountForChat(chatId);
  await reply(
    chatId,
    criteriaSet((account?.criteria ?? {}) as Criteria, claimed.replaced),
  );
}

async function offerUpgrade(chatId: string, account: Account): Promise<void> {
  const token = await issueToken(account.user_id, "upgrade", UPGRADE_TTL_MINUTES);
  const keyboard: Keyboard = [
    [{ text: "💎 Choose a plan", url: `${siteUrl()}/upgrade?t=${token}` }],
  ];
  await reply(chatId, await upgradeInvitation(account, token), keyboard);
}

async function sendToForm(chatId: string, existing: boolean): Promise<void> {
  // A returning subscriber gets a token on the link, so the form knows who they
  // are: it opens on their current criteria and offers "back to Telegram"
  // rather than a price list. Without one — or if issuing it fails — the plain
  // form is still the right page.
  const where = existing ? await filterUrl(chatId) : `${siteUrl()}/`;
  await reply(chatId, existing ? CHANGE_FILTER : SET_FILTERS, [
    [{ text: FILTERS_BUTTON, url: where }],
  ]);
}

async function pressed(
  chatId: string,
  press: NonNullable<Update["callback_query"]>,
): Promise<void> {
  const data = press.data ?? "";
  const messageId = press.message?.message_id;

  if (data.startsWith("ignore:")) {
    const id = Number(data.slice("ignore:".length));
    return ignoreListing(chatId, press.id, messageId, id);
  }
  if (data === "pause" || data === "found") {
    if (press.id) await answerCallback(press.id).catch(() => undefined);
    return pauseAlerts(chatId, data === "found" ? "found_a_place" : "paused");
  }
  if (data === "change") {
    if (press.id) await answerCallback(press.id).catch(() => undefined);
    return sendToForm(chatId, true);
  }

  if (press.id) {
    await answerCallback(press.id, "That message is out of date").catch(() => undefined);
  }
  await sendToForm(chatId, false);
}

async function ignoreListing(
  chatId: string,
  callbackId: string | undefined,
  messageId: number | undefined,
  notificationId: number,
): Promise<void> {
  if (!Number.isInteger(notificationId) || notificationId < 1) {
    if (callbackId) await answerCallback(callbackId, "That button is malformed").catch(() => undefined);
    return;
  }

  // Hiding the message comes first and depends on nothing else. Telegram only
  // lets a bot delete its own message in the chat the press came from, so this
  // cannot reach anybody else's alert however the callback data was forged, and
  // the button keeps working when the bookkeeping below cannot.
  const removed = messageId === undefined ? false : await deleteMessage(chatId, messageId);

  if (callbackId) {
    await answerCallback(
      callbackId,
      removed ? undefined : "Telegram will not let me delete a message this old.",
    ).catch(() => undefined);
  }

  // Worth having, not worth failing over: the message is already gone.
  await dismiss("telegram", chatId, notificationId);
}

async function pauseAlerts(chatId: string, reason: Reason): Promise<void> {
  const stopped = await stopFilter("telegram", chatId, reason);
  if (!stopped) {
    await reply(chatId, NOTHING_TO_PAUSE);
    return;
  }
  await reply(chatId, reason === "found_a_place" ? FOUND_A_PLACE : PAUSED);
}

async function resume(chatId: string): Promise<void> {
  const woken = await resumeFilter("telegram", chatId);
  await reply(chatId, woken ? RESUMED : NOTHING_TO_RESUME);
}

async function pushMenu(chatId: string): Promise<void> {
  const admin = process.env.TELEGRAM_ADMIN_CHAT;
  if (!admin || chatId !== admin) {
    await reply(chatId, COMMAND_HELP);
    return;
  }
  const ok = await setMyCommands(BOT_MENU);
  await sendMessage(
    chatId,
    ok
      ? `Menu set:\n${BOT_MENU.map((c) => `/${c.command} — ${c.description}`).join("\n")}`
      : "setMyCommands failed — the log has the reason.",
  );
}

async function stop(chatId: string): Promise<void> {
  const stopped = await deleteFilter("telegram", chatId);
  await reply(chatId, stopped ? STOPPED : NOTHING_TO_STOP);
}

async function grant(chatId: string, ref: string, plan: string, days: number): Promise<void> {
  const admin = process.env.TELEGRAM_ADMIN_CHAT;
  if (!admin || chatId !== admin) {
    await reply(chatId, COMMAND_HELP);
    return;
  }
  if (days < 1 || days > 400) {
    await sendMessage(chatId, "Days must be between 1 and 400.");
    return;
  }

  const result = await transaction(async (run) => {
    const found = await run(`SELECT id FROM users WHERE payment_ref = $1`, [ref]);
    const userId = found[0]?.id;
    if (userId === undefined) return null;

    const plans = await run(`SELECT key, price_pence FROM plans WHERE key = $1 AND enabled`, [plan]);
    if (plans[0] === undefined) return "no-plan";

    const rows = await run(
      `UPDATE users
          SET plan = $1,
              plan_until = greatest(coalesce(plan_until, now()), now())
                           + make_interval(days => $2::int)
        WHERE id = $3
        RETURNING plan_until`,
      [plan, days, userId],
    );
    await run(
      `INSERT INTO payments (user_id, plan, amount_pence, provider, granted_days, granted_by)
       VALUES ($1, $2, $3, 'bank_transfer', $4, 'telegram_admin')`,
      [userId, plan, plans[0].price_pence ?? 0, days],
    );
    return String(rows[0]?.plan_until ?? "");
  });

  if (result === null) {
    await sendMessage(chatId, `No account with reference ${ref}.`);
    return;
  }
  if (result === "no-plan") {
    await sendMessage(chatId, `No enabled plan called "${plan}".`);
    return;
  }
  await sendMessage(chatId, `${ref} is on ${plan} until ${result}.`);
}
