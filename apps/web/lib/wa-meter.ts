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
 * Which of Meta's rates a message was charged at — its vocabulary, not ours,
 * because the ledger exists to be matched against the invoice line for line.
 *
 * `service` is free-form inside the 24-hour window. `utility` is a template,
 * which is the only thing that reaches a shut one. `marketing` is the dearest
 * and nothing is sent at it any more — see 0070 — but a value this column may
 * hold is a value this type has to admit.
 */
export type WaRate = "service" | "utility" | "marketing";

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
  billedAs: WaRate = "service",
): Promise<void> {
  // Digits only, the same normalisation the sender applies to the number it
  // posts and the webhook applies to the number it looks up. `user_channels`
  // holds them in that form, so anything else here would silently resolve to
  // no account and file every reply as a message to a stranger.
  const number = address.replace(/\D/g, "");

  try {
    await query(
      // The price is chosen here, off `billed_as`, exactly as the worker's
      // `record_wa_message` does it — and read inside the statement so that it
      // is the price now, with nothing cached.
      //
      // `service` is still the default and still almost everything: the
      // webhook's replies are free-form inside a window it has just reopened
      // by receiving something. What changed is that this is no longer the
      // only thing the site can send. The payment receipt goes out as a
      // template when the window is shut — see `tell` — and a template is
      // billed at the template rate whoever posted it.
      `INSERT INTO wa_messages
              (user_id, kind, provider_msg_id, billed_as, cost_micros)
       SELECT (SELECT uc.user_id FROM user_channels uc
                WHERE uc.channel = 'whatsapp' AND uc.address = $1),
              $2, $3, $4,
              (SELECT CASE
                          WHEN $4 = 'utility'   THEN ps.wa_template_micros
                          WHEN $4 = 'marketing' THEN ps.wa_marketing_micros
                          ELSE ps.wa_service_micros
                      END
                 FROM plan_settings ps)`,
      [number, kind, providerMsgId ?? null, billedAs],
    );
  } catch (error) {
    console.error("whatsapp message not metered", {
      kind,
      billed_as: billedAs,
      to: number.slice(-4),
      error: error instanceof Error ? error.message : String(error),
    });
  }
}
