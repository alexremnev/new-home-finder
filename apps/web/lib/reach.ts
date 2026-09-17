import { query } from "./db";
import { sendMessage } from "./telegram";
import { sendWhatsApp } from "./whatsapp";

// Say something to whichever app the person actually uses.
//
// The worker has a notifier registry for this; the web app had nothing, so
// anything happening here — a payment clearing, a webhook arriving — was silent
// on the one channel the person is watching.
export async function tell(userId: number, text: string): Promise<boolean> {
  const rows = await query<{ channel: string; address: string }>(
    `SELECT channel, address FROM user_channels
      WHERE user_id = $1 AND is_primary AND verified_at IS NOT NULL
      LIMIT 1`,
    [userId],
  );
  const to = rows[0];
  if (!to) {
    console.error("nothing to tell them on: no verified channel", { userId });
    return false;
  }

  if (to.channel === "telegram") return sendMessage(to.address, text);
  if (to.channel === "whatsapp") return sendWhatsApp(to.address, text);

  console.error("no way to reach that channel", { channel: to.channel });
  return false;
}
