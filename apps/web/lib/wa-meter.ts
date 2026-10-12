import { query } from "./db";

/**
 * What the web app sends on WhatsApp, and therefore what it has to record.
 *
 * `reply` is an answer to something the person just sent — a command, a
 * button, a message to the bot. `receipt` is us telling them something they
 * did not ask for in that moment: a payment that cleared, a filter that was
 * saved from the form.
 *
 * The distinction is not cosmetic. Both are billed, but only one of them is
 * our own doing, and that is the first question asked of a WhatsApp bill.
 */
export type WaKind = "reply" | "receipt";

/**
 * Record one WhatsApp message we sent and will be charged for.
 *
 * Meta bills every message now — there is no free window and no free service
 * tier — so a message that is not in `wa_messages` is money that nothing in
 * the dashboard can see and an allowance that overstates what is left. See
 * 0063.
 *
 * The worker has `store.record_wa_message` for the same job. Two
 * implementations because they share no code and no connection, not because
 * they are two rules.
 *
 * ── why the number and not a user id ──────────────────────────────────────
 *
 * The one caller is `sendWhatsApp`, and what it has is a phone number: the
 * webhook answers whoever wrote in, including somebody with no account yet.
 * So the account is resolved here, and a message to a stranger is recorded
 * with no user attached rather than not recorded — we paid for it either way.
 *
 * ── why a failure here is only a log line ─────────────────────────────────
 *
 * The message has already been delivered by the time this runs. Throwing
 * would turn a delivered reply into a failed one at the call site, which is
 * the worse of the two wrongs: the person has their answer, and what is lost
 * is one row of accounting. Said out loud, though — a meter quietly missing
 * rows is how an allowance drifts away from the invoice.
 */
export async function meterWhatsApp(
  address: string,
  kind: WaKind,
  providerMsgId?: string | null,
): Promise<void> {
  // Digits only, the same normalisation the sender applies to the number it
  // posts and the webhook applies to the number it looks up. `user_channels`
  // holds them in that form, so anything else here would silently resolve to
  // no account and file every reply as a message to a stranger.
  const number = address.replace(/\D/g, "");

  try {
    await query(
      // `billed_as` takes its default of `service`, and that is a fact about
      // this file rather than an assumption: `sendWhatsApp` has no template
      // path at all. Everything here is free-form inside the 24-hour window,
      // which the webhook has just reopened by receiving something. The
      // worker's notifier is the one that can send the other kind, and it
      // says so for itself.
      `INSERT INTO wa_messages (user_id, kind, provider_msg_id, cost_micros)
       SELECT (SELECT uc.user_id FROM user_channels uc
                WHERE uc.channel = 'whatsapp' AND uc.address = $1),
              $2, $3,
              coalesce((SELECT ps.wa_service_micros FROM plan_settings ps), 0)`,
      [number, kind, providerMsgId ?? null],
    );
  } catch (error) {
    console.error("whatsapp message not metered", {
      kind,
      to: number.slice(-4),
      error: error instanceof Error ? error.message : String(error),
    });
  }
}
