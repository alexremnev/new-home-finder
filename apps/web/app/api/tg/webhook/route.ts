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
  alreadyOnAnotherChannel,
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
  EDIT_TTL_MINUTES,
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
    await sendMessage(chatId, "Something went wrong on my side. Please try again in a moment.")
      .catch(() => undefined);
  }
  return ok();
}

async function handle(chatId: string, text: string | undefined): Promise<void> {
  const said = (text ?? "").trim();

  // A command always wins. An unfinished ticket swallows plain messages, and
  const command = parseCommand(text);

  if (command.kind === "support") {
    await sendMessage(chatId, SUPPORT_REPLY);
    return;
  }
  if (command.kind === "cancel") {
    await sendMessage(chatId, COMMAND_HELP);
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
        await sendMessage(chatId, noFilterYet(siteUrl()));
        return;
      }
      const body = [
        criteriaCard((account.criteria ?? {}) as Criteria),
        "",
        planLine(account.plan_name, account.plan_until, planIsLive(account)),
        "",
        COMMAND_HELP,
      ].join("\n");
      await sendMessage(chatId, body);
      return;
    }

    case "upgrade":
      return offerUpgrade(chatId, account);

    case "grant":
      await grant(chatId, command.ref, command.plan, command.days);
      return;

    default:
      await sendMessage(chatId, command.reason ? `${command.reason}\n\n${COMMAND_HELP}` : COMMAND_HELP);
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

  type Claim = null | { taken: string } | { userId: number };

  const claimed: Claim = await transaction<Claim>(async (run) => {

    const rows = await run(
      `UPDATE user_tokens SET used_at = now()
        WHERE token = $1 AND purpose = 'start' AND used_at IS NULL AND expires_at > now()
        RETURNING user_id`,
      [token],
    );
    let userId = rows[0]?.user_id;
    if (userId === undefined) return null;

    // One search, one destination. Without this the model is a convention: two
    // verified channels on one account would double the digest and the notices.
    const elsewhere = await run(
      `SELECT channel FROM user_channels
        WHERE user_id = $1 AND channel <> 'telegram' AND verified_at IS NOT NULL
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
        WHERE channel = 'telegram' AND address = $2 AND user_id <> $1`,
      [userId, chatId],
    );
    await run(
      `INSERT INTO user_channels (user_id, channel, address, is_primary, verified_at)
       VALUES ($1, 'telegram', $2, true, now())
       ON CONFLICT (user_id, channel)
         DO UPDATE SET address = EXCLUDED.address, verified_at = now()`,
      [userId, chatId],
    );

    await beginSubscription(run, Number(userId));
    return { userId: Number(userId) };
  });

  if (claimed === null) {
    await sendMessage(chatId, LINK_EXPIRED);
    return;
  }
  if ("taken" in claimed) {
    await sendMessage(chatId, alreadyOnAnotherChannel(claimed.taken));
    return;
  }

  // Read back rather than trust the form: this is what the filter will actually
  // match on, after the district limit and the rest of enforceLimits.
  const account = await accountForChat(chatId);
  await sendMessage(chatId, criteriaSet((account?.criteria ?? {}) as Criteria));
}

async function offerUpgrade(chatId: string, account: Account): Promise<void> {
  const token = await issueToken(account.user_id, "upgrade", UPGRADE_TTL_MINUTES);
  const keyboard: Keyboard = [
    [{ text: "💎 Choose a plan", url: `${siteUrl()}/upgrade?t=${token}` }],
  ];
  await sendMessage(chatId, await upgradeInvitation(account, token), keyboard);
}

async function sendToForm(chatId: string, existing: boolean): Promise<void> {
  // A returning subscriber gets a token on the link, so the form knows who
  // they are and shows "back to Telegram" rather than a price list. Without
  // one — or if issuing it fails — the plain form is still the right page.
  let where = `${siteUrl()}/`;
  if (existing) {
    const account = await accountForChat(chatId).catch(() => null);
    if (account) {
      const token = await issueToken(account.user_id, "edit", EDIT_TTL_MINUTES)
        .catch(() => null);
      if (token) where = `${siteUrl()}/?e=${encodeURIComponent(token)}`;
    }
  }
  await sendMessage(chatId, existing ? CHANGE_FILTER : SET_FILTERS, [
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
    await sendMessage(chatId, NOTHING_TO_PAUSE);
    return;
  }
  await sendMessage(chatId, reason === "found_a_place" ? FOUND_A_PLACE : PAUSED);
}

async function resume(chatId: string): Promise<void> {
  const woken = await resumeFilter("telegram", chatId);
  await sendMessage(chatId, woken ? RESUMED : NOTHING_TO_RESUME);
}

async function pushMenu(chatId: string): Promise<void> {
  const admin = process.env.TELEGRAM_ADMIN_CHAT;
  if (!admin || chatId !== admin) {
    await sendMessage(chatId, COMMAND_HELP);
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
  await sendMessage(chatId, stopped ? STOPPED : NOTHING_TO_STOP);
}

async function grant(chatId: string, ref: string, plan: string, days: number): Promise<void> {
  const admin = process.env.TELEGRAM_ADMIN_CHAT;
  if (!admin || chatId !== admin) {
    await sendMessage(chatId, COMMAND_HELP);
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
