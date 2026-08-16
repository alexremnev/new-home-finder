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
  clearKeyboard,
  deleteMessage,
  editMessageText,
  type Keyboard,
  sendMessage,
  sendMessageReturningId,
  setMyCommands,
  type Update,
} from "@/lib/telegram";
import {
  apply,
  applyDistrictText,
  applyPriceText,
  commitSession,
  type Context as WizardContext,
  dropSession,
  loadSession,
  parseCallback,
  render,
  saveSession,
  type Session,
} from "@/lib/wizard";

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
    if (update.callback_query) await tapped(chatId, update.callback_query);
    else await handle(chatId, update.message?.text);
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

  // Two steps are answered by typing rather than tapping — districts and price —
  // so plain text that is not a command belongs to whichever one is waiting.
  // Checked before the help fallback, or "SE16, E14" would be answered with a list
  // of commands.
  if (command.kind === "help" && !command.reason) {
    const waiting = await loadSession(chatId);
    if (waiting?.step === "districts") return districtsTyped(chatId, waiting, text ?? "");
    if (waiting?.step === "priceMin" || waiting?.step === "priceMax") {
      return priceTyped(chatId, waiting, text ?? "");
    }
  }

  if (command.kind === "update") return beginUpdate(chatId);

  const account = await accountForChat(chatId);
  if (!account) {
    // No account and no wizard: the useful answer is to start one, not to explain
    // that they have nothing.
    return beginWizard(chatId, null, "districts", {});
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

    case "upgrade": {
      const token = await issueToken(account.user_id, "upgrade", UPGRADE_TTL_MINUTES);
      // A button rather than a bare URL. The checkout link carries a token, and a
      // long opaque URL in a chat is something people hesitate to tap.
      const keyboard: Keyboard = [
        [{ text: "Pay by card", url: `${siteUrl()}/api/checkout?t=${token}` }],
      ];
      await sendMessage(chatId, await upgradeInvitation(account, token), keyboard);
      return;
    }

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
  if (!token) {
    // A bare /start from someone who found the bot directly. This used to answer
    // "go and fill in the form"; it now starts the wizard, which is the whole point
    // of the wizard existing. Someone who already has a filter is asked before it
    // is replaced — /start from a subscriber is usually curiosity, not intent.
    const account = await accountForChat(chatId);
    if (account?.subscription_id != null) return beginUpdate(chatId);
    return beginWizard(chatId, account?.user_id ?? null, "districts", {});
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
    const userId = rows[0]?.user_id;
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

// ── the wizard ────────────────────────────────────────────────────────────

/**
 * What the plan and the coverage allow, for a chat that may not have an account
 * yet. A brand-new person is shown the sign-up plan's allowance, because that is
 * what they will have by the time they press Finish.
 */
async function wizardContext(userId: number | null): Promise<WizardContext> {
  const [districts, plan] = await Promise.all([enabledDistricts(), signupPlan()]);
  let maxDistricts = plan.max_districts;
  if (userId !== null) {
    const rows = await query<{ max_districts: number }>(
      `SELECT p.max_districts FROM users u JOIN plans p ON p.key = u.plan WHERE u.id = $1`,
      [userId],
    );
    if (rows[0]) maxDistricts = Number(rows[0].max_districts);
  }
  return { districts, maxDistricts };
}

/** Start, or restart, a wizard and show its first step. */
async function beginWizard(
  chatId: string,
  userId: number | null,
  step: Session["step"],
  draft: Session["draft"],
): Promise<void> {
  const districtsKnown = await enabledDistricts();
  if (!districtsKnown.length) {
    // Better than a wizard whose first step has no buttons: nothing is being
    // collected, so there is nothing to promise.
    await sendMessage(chatId, "No districts are being covered yet. Please try again later.");
    return;
  }

  // Any earlier wizard's keyboard is taken away before this one appears. Two live
  // keyboards in a chat means the older one can still be tapped, and `apply()`
  // would then be answering a step that has been left behind.
  const previous = await loadSession(chatId);
  if (previous?.promptMsgId) await clearKeyboard(chatId, previous.promptMsgId).catch(() => undefined);

  const context = await wizardContext(userId);
  const session: Session = { chatId, userId, step, draft, promptMsgId: null };
  const view = render(session, context);
  const messageId = await sendMessageReturningId(chatId, view.text, view.keyboard);
  await saveSession({ ...session, promptMsgId: messageId });
}

/** /update, and /start from someone who already has a filter. */
async function beginUpdate(chatId: string): Promise<void> {
  const account = await accountForChat(chatId);
  if (!account || account.subscription_id === null) {
    // Nothing to overwrite, so there is nothing to ask about.
    return beginWizard(chatId, account?.user_id ?? null, "districts", {});
  }
  // The current filter is the draft only so that the overwrite step can show it.
  // Answering "replace" clears it — see `apply`.
  return beginWizard(
    chatId,
    account.user_id,
    "overwrite",
    (account.criteria ?? {}) as Session["draft"],
  );
}

/**
 * Show the session's current step.
 *
 * `fresh` decides where it appears, and the distinction matters more than it looks.
 * A tap is answered by editing in place: the person just touched that message, it is
 * on screen, and a new one would leave a dead copy above it.
 *
 * Typing is answered by a new message at the bottom. An edited message stays where
 * it was, so after two or three typed answers the prompt has scrolled out of sight
 * above the person's own replies — they are left looking at their own text with no
 * question visible. The old prompt is deleted rather than merely stripped of its
 * keyboard, so the chat holds one live wizard and not a column of stale ones.
 */
async function showStep(
  session: Session,
  context: WizardContext,
  fresh = false,
): Promise<Session> {
  const view = render(session, context);
  if (fresh) {
    if (session.promptMsgId) {
      await deleteMessage(session.chatId, session.promptMsgId).catch(() => undefined);
    }
    const messageId = await sendMessageReturningId(session.chatId, view.text, view.keyboard);
    return { ...session, promptMsgId: messageId };
  }
  if (session.promptMsgId) {
    const edited = await editMessageText(
      session.chatId,
      session.promptMsgId,
      view.text,
      view.keyboard,
    );
    if (edited) return session;
  }
  const messageId = await sendMessageReturningId(session.chatId, view.text, view.keyboard);
  return { ...session, promptMsgId: messageId };
}

/** A button tap. */
async function tapped(
  chatId: string,
  callback: NonNullable<Update["callback_query"]>,
): Promise<void> {
  const acknowledge = (text?: string) =>
    callback.id ? answerCallback(callback.id, text) : Promise.resolve(true);

  const session = await loadSession(chatId);
  if (!session) {
    // The keyboard outlived its session — an hour of silence, or a finished wizard.
    await acknowledge("That form has expired");
    await sendMessage(chatId, "That was from an earlier setup. Send /start to begin again.");
    return;
  }

  const action = parseCallback(callback.data);
  if (!action) {
    await acknowledge();
    return;
  }

  const context = await wizardContext(session.userId);
  const outcome = apply(session, action, context);

  switch (outcome.kind) {
    case "reject":
      // The reason goes on the button, not into the chat: a refusal is about the
      // tap that just happened and is stale a second later.
      await acknowledge(outcome.reason);
      return;

    case "render": {
      await acknowledge();
      await saveSession(await showStep(outcome.session, context));
      return;
    }

    case "abandon": {
      await acknowledge();
      if (session.promptMsgId) await clearKeyboard(chatId, session.promptMsgId).catch(() => undefined);
      await dropSession(chatId);
      await sendMessage(chatId, "Left as it was. /current shows what you have.");
      return;
    }

    case "commit": {
      await acknowledge();
      await finishWizard(outcome.session);
      return;
    }
  }
}

/** Districts typed rather than tapped, which is the only way to reach most of them. */
async function districtsTyped(chatId: string, session: Session, text: string): Promise<void> {
  const context = await wizardContext(session.userId);
  const result = applyDistrictText(session, text, context);
  if (!result.ok) {
    // Named rather than silently dropped: a filter that quietly covers less than
    // was asked for is worse than a refusal, because nobody finds out.
    await sendMessage(chatId, `${result.reason}\n\nTry again, or tap one below.`);
    return;
  }
  await saveSession(await showStep(result.session, context, true));
}


/** The price steps' typed answer. */
async function priceTyped(chatId: string, session: Session, text: string): Promise<void> {
  const result = applyPriceText(session, text);
  if (!result.ok) {
    // Sent as a message rather than edited into the prompt: the person typed, so
    // the correction belongs next to what they typed.
    await sendMessage(chatId, `${result.reason}\n\nSend a number, or tap Continue.`);
    return;
  }
  const context = await wizardContext(session.userId);
  await saveSession(await showStep(result.session, context, true));
}

async function finishWizard(session: Session): Promise<void> {
  let committed;
  try {
    committed = await commitSession(session);
  } catch (error) {
    if (error instanceof InvalidForm) {
      // The plan changed under a draft that was left open. Said plainly, with the
      // draft kept, so nothing has to be answered again.
      await sendMessage(session.chatId, `That didn't work: ${error.message}\n\n/pay — cover more districts`);
      return;
    }
    throw error;
  }

  if (session.promptMsgId) await clearKeyboard(session.chatId, session.promptMsgId).catch(() => undefined);

  const lines = [
    committed.live
      ? "Done. I'll message you when a new listing matches."
      : "Your filter is saved, but your plan has ended, so nothing will be sent yet.",
    "",
    describeCriteria(session.draft),
    "",
    planLine(committed.planName, committed.planUntil, committed.live),
  ];
  if (committed.live) {
    lines.push(
      "",
      "Only listings posted from now on are sent — nothing already on the market.",
      "",
      "/current — what I'm using · /update — change it · /stop — stop",
    );
  } else {
    lines.push("", "/pay — turn the alerts back on");
  }
  await sendMessage(session.chatId, lines.join("\n"));
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
