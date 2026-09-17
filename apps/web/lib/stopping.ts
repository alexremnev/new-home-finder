import { transaction } from "./db";

export type Reason = "found_a_place" | "paused" | "stopped";

// Stopping a filter and saying why, from either channel.
//
// `active = false` alone records that the alerts stopped; it does not record
// whether the service worked. Somebody who found a flat is a success and
// somebody who gave up is not, and without the reason both look identical.
export async function stopFilter(
  channel: "telegram" | "whatsapp",
  address: string,
  reason: Reason,
): Promise<boolean> {
  return transaction(async (run) => {
    const users = await run(
      `SELECT u.id FROM users u
         JOIN user_channels uc ON uc.user_id = u.id
        WHERE uc.channel = $1 AND uc.address = $2
        FOR UPDATE OF u`,
      [channel, address],
    );
    const userId = users[0]?.id;
    if (userId === undefined) return false;

    const stilled = await run(
      `UPDATE subscriptions
          SET active = false, stopped_reason = $2, stopped_at = now()
        WHERE user_id = $1 AND active
        RETURNING id`,
      [userId, reason],
    );
    if (stilled.length === 0) return false;

    // Anything already queued would otherwise still be drained: claim_queued
    // reads the outbox, not the subscription.
    await run(`DELETE FROM notifications WHERE user_id = $1 AND status = 'queued'`, [userId]);
    return true;
  });
}
