import { query } from "./db";
import { siteUrl } from "./plans";
import { sendMessage } from "./telegram";
import { sendWhatsApp, sendWhatsAppTemplate } from "./whatsapp";

// WhatsApp permits free-form text only this long after the person's own last
// message. The worker has `window_open` for the same constant; two copies
// because they share no code, not because they are two rules.
const WINDOW_HOURS = 24;

/**
 * A template to fall back to when WhatsApp's window is shut, and the payload
 * its quick reply sends back.
 *
 * Only some of what `tell` says is worth a template. A payment receipt is —
 * the person has handed over money and hears nothing, and their alerts are
 * stuck behind the same shut window. A refund notice is not: it is rarer, it
 * is not blocking anything, and no template has been approved for it, so what
 * it gets instead is an honest log line naming the window rather than a 131047
 * that reads like a broken integration.
 *
 * `setting` is the *name* of the environment variable holding the approved
 * template, not its value. Deliberately: a template that has been written but
 * not yet approved is the ordinary state of affairs for a week or two, and the
 * log line for that has to name the variable somebody still has to set.
 */
export type WaFallback = { setting: string; payload: string };

/**
 * The one fallback there is, and the one message that earns it.
 *
 * Here rather than at the call site so that the payload cannot drift from the
 * webhook that answers it: `pressed` in the WhatsApp route matches this exact
 * string, and a payload nobody handles is a button that visibly does nothing
 * on the message confirming somebody's money was taken.
 */
export const PAYMENT_RECEIPT: WaFallback = {
  setting: "WA_RECEIPT_TEMPLATE",
  payload: "paid",
};

/**
 * Say something to whichever app the person actually uses.
 *
 * The worker has a notifier registry for this; the web app had nothing, so
 * anything happening here — a payment clearing, a webhook arriving — was silent
 * on the one channel the person is watching.
 *
 * `fallback` is what to send on WhatsApp when the 24-hour window is shut. The
 * window is checked here rather than discovered from Meta's refusal, because
 * the two outcomes need different payloads and a refusal arrives too late to
 * choose: see `sendWhatsAppTemplate`.
 */
export async function tell(
  userId: number,
  text: string,
  fallback?: WaFallback,
): Promise<boolean> {
  const rows = await query<{
    channel: string;
    address: string;
    last_inbound_at: Date | null;
  }>(
    `SELECT channel, address, last_inbound_at FROM user_channels
      WHERE user_id = $1 AND is_primary AND verified_at IS NOT NULL
      LIMIT 1`,
    [userId],
  );
  const to = rows[0];
  if (!to) {
    console.error("nothing to tell them on: no verified channel", { userId });
    return false;
  }

  // The same rule as the two webhooks: a way back to the site on everything
  // except a listing alert, and as a button on both channels — a url written
  // out in a chat message is a url somebody has to select and copy.
  const site = siteUrl();
  const carries = text.includes(site);
  if (to.channel === "telegram") {
    return sendMessage(
      to.address,
      text,
      carries ? undefined : [[{ text: "🌐 Open the site", url: `${site}/` }]],
    );
  }
  if (to.channel === "whatsapp") {
    const open =
      to.last_inbound_at !== null &&
      Date.now() - new Date(to.last_inbound_at).getTime() <
        WINDOW_HOURS * 60 * 60 * 1000;

    if (!open) {
      // Everything this function says is unprompted, so a shut window is the
      // normal case here rather than the exception — a plan ends after a day
      // of silence, which is exactly the day that shuts it.
      if (!fallback) {
        // Named for what it is. Before this the send was attempted anyway,
        // Meta refused it with 131047, and the log said "payment granted but
        // not announced" — which reads as a fault in the integration and
        // sends whoever finds it looking at tokens and signatures.
        console.error("whatsapp window is shut and nothing may be sent into it", {
          user: userId,
          last_inbound_at: to.last_inbound_at,
        });
        return false;
      }
      const name = process.env[fallback.setting];
      if (!name) {
        console.error(
          `whatsapp window is shut and ${fallback.setting} is not set in ` +
            `this deployment, so there is no template to send instead`,
          { user: userId },
        );
        return false;
      }
      return sendWhatsAppTemplate(to.address, name, fallback.payload);
    }

    return sendWhatsApp(
      to.address,
      text,
      carries ? undefined : { label: "Open the site", url: `${site}/` },
      // Everything through here is unprompted — a payment clearing, a webhook
      // arriving — so it is a receipt rather than a reply. Both are billed;
      // only this one is our own doing.
      "receipt",
    );
  }

  console.error("no way to reach that channel", { channel: to.channel });
  return false;
}
