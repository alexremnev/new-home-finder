import { query } from "./db";
import { siteUrl } from "./plans";
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
