// The Telegram webhook: setting a filter up, changing it, paying, and stopping.
//
// Four decisions here are worth stating, because each is a place this route
// could look correct and be wrong.
//
// 0. Button taps only arrive if the webhook was registered with `callback_query`
//    in `allowed_updates`. Registered with just `["message"]` — as it was before
//    the wizard existed — every tap is dropped by Telegram before it reaches
//    here, and nothing in this file or in the logs says so. See CHEATSHEET.md.
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

import { BOT_MENU, COMMAND_HELP, parseCommand } from "@/lib/commands";
import {
  applyPatch,
  describeCriteria,
  enforceLimits,
  InvalidForm,
  type Criteria,
} from "@/lib/criteria";
import { query, transaction } from "@/lib/db";
import {
  LINK_EXPIRED,
  NOTHING_TO_STOP,
  STOPPED,
  WELCOME,
  noFilterYet,
  planLine,
  upgradeInvitation,
} from "@/lib/messages";
import {
  type Account,
  accountForChat,
  EDIT_TTL_MINUTES,
  enabledDistricts,
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
    if (update.callback_query) {
      // Nothing in this bot has a callback button any more — the form's buttons are
      // links and the upgrade button is a link. But a keyboard already sitting in
      // somebody's chat stays tappable for ever, so a tap is answered rather than
      // ignored: an unacknowledged callback leaves a spinner, which reads as a
      // broken bot rather than an old message.
      if (update.callback_query.id) {
        await answerCallback(update.callback_query.id, "That message is out of date")
          .catch(() => undefined);
      }
      await sendToForm(chatId, false);
    } else {
      await handle(chatId, update.message?.text);
    }
  } catch (error) {
    console.error("webhook failed", { update_id: update.update_id, error: String(error) });
    // The tap is acknowledged even on this path. An unanswered callback leaves a
    // spinner on the button, so a server error would look like a frozen bot.
    if (update.callback_query?.id) {
      await answerCallback(update.callback_query.id, "Something went wrong").catch(() => undefined);
    }
    await sendMessage(chatId, "Something went wrong on my side. Please try again in a moment.")
      .catch(() => undefined);
  }
  return ok();
}

async function handle(chatId: string, text: string | undefined): Promise<void> {
  const command = parseCommand(text);

  // Stop and start are answered without needing an account loaded first: stop
  // must work under every condition, and start is what creates the link.
  if (command.kind === "stop") return stop(chatId);
  if (command.kind === "start") return start(chatId, command.token);
  if (command.kind === "menu") return pushMenu(chatId);

  if (command.kind === "update") return sendToForm(chatId, true);

  const account = await accountForChat(chatId);
  if (!account) {
    // No account and no wizard: the useful answer is to start one, not to explain
    // that they have nothing.
    return sendToForm(chatId, false);
  }

  switch (command.kind) {
    case "show": {
      if (account.subscription_id === null) {
        await sendMessage(chatId, noFilterYet(siteUrl()));
        return;
      }
      const body = [
        describeCriteria((account.criteria ?? {}) as Criteria),
        "",
        planLine(account.plan_name, account.plan_until, planIsLive(account)),
        "",
        COMMAND_HELP,
      ].join("\n");
      await sendMessage(chatId, body);
      return;
    }

    case "edit": {
      const token = await issueToken(account.user_id, "edit", EDIT_TTL_MINUTES);
      await sendMessage(
        chatId,
        [
          "Change your filter here:",
          `${siteUrl()}/edit?t=${token}`,
          "",
          `The link works for ${EDIT_TTL_MINUTES} minutes and only for you.`,
          "",
          "Or change one thing at a time — /help lists the commands.",
        ].join("\n"),
      );
      return;
    }

    case "upgrade":
      return offerUpgrade(chatId, account);

    case "patch": {
      if (account.subscription_id === null) {
        await sendMessage(chatId, noFilterYet(siteUrl()));
        return;
      }
      try {
        const next = enforceLimits(
          applyPatch(
            (account.criteria ?? {}) as Criteria,
            command.patch,
            await enabledDistricts(),
          ),
          limitsOf(account),
        );
        await query(`UPDATE subscriptions SET criteria = $1::jsonb WHERE id = $2`, [
          JSON.stringify(next),
          account.subscription_id,
        ]);
        // The whole filter is echoed back, not just the field that changed. People
        // ask for one thing and want to see they have not lost another.
        await sendMessage(
          chatId,
          ["Updated.", "", describeCriteria(next)].join("\n"),
        );
      } catch (error) {
        if (error instanceof InvalidForm) {
          const hint =
            command.patch.field === "areas" && /plan covers/.test(error.message)
              ? "\n\n/upgrade — cover more districts"
              : "";
          await sendMessage(chatId, `That didn't work: ${error.message}${hint}`);
          return;
        }
        throw error;
      }
      return;
    }

    case "grant":
      await grant(chatId, command.ref, command.plan, command.days);
      return;

    default:
      await sendMessage(chatId, command.reason ? `${command.reason}\n\n${COMMAND_HELP}` : COMMAND_HELP);
  }
}

// ── START ─────────────────────────────────────────────────────────────────

async function start(chatId: string, token: string | null): Promise<void> {
  // `t.me/<bot>?start=pay` — the upgrade button on an alert. A deep link rather than
  // a link straight to the checkout page, and the difference matters: a link in a
  // message that is minted once would have to carry a token that stays valid for as
  // long as the message survives in the chat, which is for ever. This way the alert
  // carries no secret at all, and the token is made when somebody actually taps.
  if (token === "pay") {
    const account = await accountForChat(chatId);
    if (account) return offerUpgrade(chatId, account);
    // No account for this chat, so there is nothing to upgrade. The useful answer is
    // the form, not an apology.
    return sendToForm(chatId, false);
  }

  if (!token) {
    // A bare /start from someone who found the bot directly. This used to answer
    // "go and fill in the form"; it now starts the wizard, which is the whole point
    // of the wizard existing. Someone who already has a filter is asked before it
    // is replaced — /start from a subscriber is usually curiosity, not intent.
    const account = await accountForChat(chatId);
    if (account?.subscription_id != null) return sendToForm(chatId, true);
    return sendToForm(chatId, false);
  }

  const claimed = await transaction(async (run) => {
    // The token is spent in the same statement that reads it, so two taps on the
    // link cannot attach two chats to one subscription.
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

    // ── the same person filling the form again ──────────────────────────────
    //
    // The form cannot know who is filling it — it has no session and asks for no
    // identity — so it always creates a fresh `users` row. When that row's token is
    // claimed by a chat that already belongs to somebody, there are two rows for one
    // person: the old one keeps the send history, the payments and the plan, and the
    // new one keeps nothing but the new filter.
    //
    // Left alone, the old row stayed "active" with no channel attached. It sent
    // nothing — `active_subscriptions` joins `user_channels`, so an account with no
    // channel drops out — but it was counted, which is why changing a filter added a
    // subscriber. The count was the symptom; two identities for one person was the
    // fault.
    //
    // So the rows are merged, and the OLD one survives. That direction matters:
    //
    //   * The history lives there. `notifications` is keyed by user, and it is the
    //     only thing that stops somebody being re-sent what they have already seen.
    //   * The plan lives there. Keeping the new row would reset the trial on every
    //     filter change, which is a free subscription for anybody who noticed.
    // `run` is untyped by design — it returns rows as records — so the one field
    // needed here is narrowed at the point of use rather than by a type argument.
    const owner = await run(
      `SELECT user_id FROM user_channels
        WHERE channel = 'telegram' AND address = $1 AND user_id <> $2
        LIMIT 1`,
      [chatId, userId],
    );
    const existing = owner[0]?.user_id as number | undefined;

    if (existing !== undefined) {
      // The new filter moves across, and the person's earlier filters are retired:
      // a filter change is a replacement, not an addition.
      await run(`UPDATE subscriptions SET active = false WHERE user_id = $1 AND active`, [
        existing,
      ]);
      await run(`UPDATE subscriptions SET user_id = $1 WHERE user_id = $2`, [existing, userId]);

      // The shell row goes, and only if it is genuinely a shell. A row with a
      // payment or a delivery against it is somebody's real account that happens to
      // share a chat, and deleting it would erase both.
      await run(
        `DELETE FROM users u
          WHERE u.id = $1
            AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.user_id = u.id)
            AND NOT EXISTS (SELECT 1 FROM notifications n WHERE n.user_id = u.id)`,
        [userId],
      );
      userId = existing;
    }

    // The chat id is the address. Both keys can already exist: the same person may
    // re-subscribe from a different chat, and the same chat may return with a new
    // subscription.
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
    return userId;
  });

  if (claimed === null) {
    await sendMessage(chatId, LINK_EXPIRED);
    return;
  }
  await sendMessage(chatId, WELCOME);
}


/** Start, or restart, a wizard and show its first step. */
/**
 * Send somebody to the form instead of asking here.
 *
 * A six-step wizard used to live at these two commands, and it worked. It was
 * replaced rather than improved because of a limit no amount of work on it would
 * have moved: the state was a database row, the questions were Telegram keyboards
 * and the answers were callback queries, so it could only ever exist in Telegram.
 * A form is the one setup surface every channel can link to.
 *
 * It is gone as of 0019 — the code, the tests and the table.
 */
/**
 * Offer the plans.
 *
 * Reached two ways: `/pay` typed in the chat, and the upgrade button on an alert,
 * which is a `t.me/<bot>?start=pay` link. Both land here, because a person tapping a
 * button and a person typing a command are asking the same question and should not
 * get two different answers.
 *
 * One button, to the page that lists the plans — not one button per plan. Two
 * buttons here would put the prices in two places: in this message and on the page.
 * The day one changes they disagree, in the direction where somebody is charged what
 * they were not shown. The page reads its prices from the same table the checkout
 * charges from, so there is one number.
 */
async function offerUpgrade(chatId: string, account: Account): Promise<void> {
  const token = await issueToken(account.user_id, "upgrade", UPGRADE_TTL_MINUTES);
  const keyboard: Keyboard = [
    [{ text: "💎 Choose a plan", url: `${siteUrl()}/upgrade?t=${token}` }],
  ];
  await sendMessage(chatId, await upgradeInvitation(account, token), keyboard);
}


async function sendToForm(chatId: string, existing: boolean): Promise<void> {
  const where = `${siteUrl()}/`;
  await sendMessage(
    chatId,
    [
      existing
        ? "Set your search up again here:"
        : "Set your search up here — it takes a minute:",
      "",
      where,
      "",
      existing
        ? "Saving replaces your current filter. Until you do, it keeps working as it is."
        : "Then press Connect Telegram at the end and the alerts start.",
    ].join("\n"),
    [[{ text: existing ? "Change my search" : "Set up my search", url: where }]],
  );
}








/** Push the command list to Telegram. Admin only; there is no second factor in a chat. */
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
    await run(`DELETE FROM user_tokens WHERE user_id = $1`, [userId]);

    // The user row survives with what was sent, so a returning subscriber is not
    // shown listings they have already seen. Erasure on request is a separate
    // route with a cascade.
    await run(`UPDATE users SET status = 'stopped', stopped_at = now() WHERE id = $1`, [userId]);
    return true;
  });

  await sendMessage(chatId, stopped ? STOPPED : NOTHING_TO_STOP);
}

// ── granting a plan by hand ───────────────────────────────────────────────

/**
 * `/grant <payment ref> <plan> <days>`, for payments that arrive without a
 * webhook — a bank transfer, or anything settled by conversation.
 *
 * Restricted to one chat id. An admin command that anyone can run is not an admin
 * command, and there is no second factor available inside a chat.
 */
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

    // Extended from whichever is later: someone who renews early should not lose
    // the days they have already paid for.
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
