import { query } from "./db";

// Records that somebody dismissed one listing, and says whether the row was
// theirs to dismiss.
//
// Scoped by channel and address because the notification id travels through a
// chat app, where anyone could send it back: ownership is checked, not assumed.
export async function dismiss(
  channel: "telegram" | "whatsapp",
  address: string,
  notificationId: number,
): Promise<boolean> {
  if (!Number.isInteger(notificationId) || notificationId < 1) return false;

  const rows = await query<{ id: string }>(
    `UPDATE notifications n SET ignored_at = now()
       WHERE n.id = $1
         AND EXISTS (
           SELECT 1 FROM user_channels uc
            WHERE uc.user_id = n.user_id
              AND uc.channel = $2
              AND uc.address = $3
         )
     RETURNING n.id`,
    [notificationId, channel, address],
  ).catch((error) => {
    // Worth having, not worth failing over: on Telegram the message has
    // already been deleted by the time this runs.
    console.error("could not record a dismissal", { notificationId, error: String(error) });
    return [];
  });

  return rows.length > 0;
}
