// The Telegram webhook: START links a chat to a pending subscription, STOP
// deletes the filter.
//
// Three decisions here are worth stating, because each one is a place this route
// could look correct and be wrong.
//
// 1. The secret header is checked first. The URL is the only thing protecting a
//    public endpoint that writes to the database, and a URL leaks — into logs,
//    into screenshots. `setWebhook` takes a `secret_token` and Telegram sends it
//    back on every update; anything without it is not from Telegram.
//
// 2. The work happens before the response, not after. §22 asks for an immediate
//    200 with processing afterwards, which is right on a long-lived server and
//    wrong here: a serverless instance may be frozen the moment it responds, so
//    deferred work is work that silently never happens. The queries are two or
//    three round trips, well inside Telegram's tolerance.
//
// 3. Every path answers 200, including failures. A non-2xx makes Telegram retry
//    the same update repeatedly and, sustained, drop the webhook. A failure is
//    logged for us and acknowledged to them.

import { NextResponse } from "next/server";

import { query, transaction } from "@/lib/db";
import {
  ALREADY_ACTIVE,
  HELP,
  LINK_EXPIRED,
  NEEDS_LINK,
  NOTHING_TO_STOP,
  STOPPED,
  WELCOME,
} from "@/lib/messages";
import { chatIdOf, parseCommand, sendMessage, type Update } from "@/lib/telegram";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const ok = () => NextResponse.json({ ok: true });

export async function POST(request: Request): Promise<NextResponse> {
  const secret = process.env.TELEGRAM_WEBHOOK_SECRET;
  if (!secret || request.headers.get("x-telegram-bot-api-secret-token") !== secret) {
    // 401, not 200: this is not Telegram, so there is no retry behaviour to
    // protect and no reason to pretend the update was accepted.
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
    await handle(chatId, update.message?.text);
  } catch (error) {
    console.error("webhook failed", { update_id: update.update_id, error: String(error) });
  }
  return ok();
}

async function handle(chatId: string, text: string | undefined): Promise<void> {
  const command = parseCommand(text);

  if (command.kind === "stop") {
    await stop(chatId);
    return;
  }
  if (command.kind === "start") {
    await start(chatId, command.token);
    return;
  }
  await sendMessage(chatId, HELP);
}

// ── START ─────────────────────────────────────────────────────────────────

async function start(chatId: string, token: string | null): Promise<void> {
  if (!token) {
    // A bare /start from someone who found the bot directly. There is no
    // subscription to attach and no consent on record, so nothing is created.
    const existing = await query<{ status: string }>(
      `SELECT u.status FROM users u
         JOIN user_channels uc ON uc.user_id = u.id
        WHERE uc.channel = 'telegram' AND uc.address = $1`,
      [chatId],
    );
    await sendMessage(chatId, existing[0]?.status === "active" ? ALREADY_ACTIVE : NEEDS_LINK);
    return;
  }

  const claimed = await transaction(async (run) => {
    // The token is spent in the same statement that reads it, so two taps on the
    // link cannot attach two chats to one subscription.
    const rows = await run(
      `UPDATE users
          SET status = 'active',
              consent_at = coalesce(consent_at, now()),
              consent_source = 'telegram',
              stopped_at = NULL,
              start_token = NULL,
              token_expires_at = NULL
        WHERE start_token = $1
          AND (token_expires_at IS NULL OR token_expires_at > now())
        RETURNING id`,
      [token],
    );
    const userId = rows[0]?.id;
    if (userId === undefined) return null;

    // The chat id is the address. ON CONFLICT on both keys: the same person may
    // re-subscribe from a different chat, and the same chat may return with a new
    // subscription.
    await run(
      `INSERT INTO user_channels (user_id, channel, address, is_primary, verified_at)
       VALUES ($1, 'telegram', $2, true, now())
       ON CONFLICT (user_id, channel)
         DO UPDATE SET address = EXCLUDED.address, verified_at = now()`,
      [userId, chatId],
    );
    await run(
      `UPDATE user_channels SET user_id = $1, verified_at = now()
        WHERE channel = 'telegram' AND address = $2 AND user_id <> $1`,
      [userId, chatId],
    );
    return userId;
  });

  if (claimed === null) {
    await sendMessage(chatId, LINK_EXPIRED);
    return;
  }
  await sendMessage(chatId, WELCOME);
}

// ── STOP ──────────────────────────────────────────────────────────────────

async function stop(chatId: string): Promise<void> {
  const stopped = await transaction(async (run) => {
    const users = await run(
      `SELECT u.id FROM users u
         JOIN user_channels uc ON uc.user_id = u.id
        WHERE uc.channel = 'telegram' AND uc.address = $1
        FOR UPDATE OF u`,
      [chatId],
    );
    const userId = users[0]?.id;
    if (userId === undefined) return false;

    // The filter is deleted, not deactivated: the matcher works from the
    // existence of an active subscription, so this stops delivery immediately
    // with no extra flag to check and nothing left holding the criteria.
    await run(`DELETE FROM subscriptions WHERE user_id = $1`, [userId]);

    // In the same transaction. Anything already queued would otherwise be sent by
    // the next run to someone who has just unsubscribed.
    await run(`DELETE FROM notifications WHERE user_id = $1 AND status = 'queued'`, [userId]);

    // The user row survives with what was sent, so a returning subscriber is not
    // shown listings they have already seen. Erasure on request is a separate
    // route with a cascade.
    await run(
      `UPDATE users SET status = 'stopped', stopped_at = now(),
                        start_token = NULL, token_expires_at = NULL
        WHERE id = $1`,
      [userId],
    );
    return true;
  });

  await sendMessage(chatId, stopped ? STOPPED : NOTHING_TO_STOP);
}
