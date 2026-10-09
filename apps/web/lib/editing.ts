import type { Criteria } from "./criteria";
import { query, transaction } from "./db";
import { accountForToken, type Channel } from "./plans";

export type Edited = {
  /** Whether a filter that was already running has just been replaced. */
  replaced: boolean;
  /** Where to answer, and where to send them back to. */
  address: string;
};

/**
 * Saving a changed filter straight onto the account that asked to change it.
 *
 * The sign-up path cannot do this job. It opens a new pending account, hands
 * out a link, and the filter only goes live once the person messages the bot
 * and the token is claimed. On Telegram that message is invisible — the
 * `?start=<token>` deeplink is sent by the client itself — so a change there
 * already looks like coming back to the chat. On WhatsApp it is a prefilled
 * "Link my alerts: …" waiting to be sent, which is the sign-up flow wearing
 * the wrong hat: somebody who has had alerts for a fortnight is asked to
 * connect WhatsApp and start a trial again, and if they close the app without
 * pressing send their search silently did not change.
 *
 * So an edit token is saved here instead, and the person is simply sent back
 * to the conversation. Nothing about the plan is touched — no trial begins or
 * restarts — because the subscription being edited is already running.
 *
 * Null when the token is not a live edit token, or when the account has
 * nothing verified on this messenger: then there is nothing to send them back
 * to, and the caller is right to treat the submission as the sign-up it looks
 * like.
 */
export async function saveEditedFilter(
  token: string,
  criteria: Criteria,
  channel: Channel,
): Promise<Edited | null> {
  const account = await accountForToken(token, "edit").catch(() => null);
  if (!account) return null;

  const rows = await query<{ address: string }>(
    `SELECT address FROM user_channels
      WHERE user_id = $1 AND channel = $2 AND verified_at IS NOT NULL
      ORDER BY is_primary DESC
      LIMIT 1`,
    [account.user_id, channel],
  ).catch(() => []);
  const address = rows[0]?.address;
  if (!address) return null;

  const label = (criteria.areas?.postcode_districts ?? []).join(", ") || "London";
  const body = JSON.stringify(criteria);

  const replaced = await transaction(async (run) => {
    // The filter being edited — or, for somebody who paused and came back, the
    // one that was last running. Written in place rather than inserted beside:
    // "the filter you had before is replaced" is what the person was told, and
    // a second row would leave the worker to decide which of the two wins.
    const found = await run(
      `SELECT id, active FROM subscriptions
        WHERE user_id = $1
        ORDER BY active DESC, created_at DESC, id DESC
        LIMIT 1
        FOR UPDATE`,
      [account.user_id],
    );
    const current = found[0];
    const was = Boolean(current?.active);

    let id = current?.id as number | undefined;
    if (id === undefined) {
      const made = await run(
        `INSERT INTO subscriptions (user_id, label, criteria, backfill_from, active)
         VALUES ($1, $2, $3::jsonb, now(), true)
         RETURNING id`,
        [account.user_id, label, body],
      );
      id = made[0]?.id as number;
    } else {
      await run(
        `UPDATE subscriptions
            SET criteria = $2::jsonb, label = $3,
                -- Only listings from now on, which is what the form promises
                -- and what a changed search means: the ones that appeared
                -- while the old filter was running were already offered.
                backfill_from = now(),
                active = true, stopped_reason = NULL, stopped_at = NULL
          WHERE id = $1`,
        [id, body, label],
      );
    }

    // One active filter per account is what the worker matches on, so anything
    // else this account has stands down — the same rule `beginSubscription`
    // applies on the sign-up path.
    await run(
      `UPDATE subscriptions SET active = false
        WHERE user_id = $1 AND active AND id <> $2`,
      [account.user_id, id],
    );

    // Saving a filter is asking for the alerts, so somebody who had paused is
    // running again. Never a blocked account: that status is ours, not theirs.
    await run(
      `UPDATE users SET status = 'active', stopped_at = NULL
        WHERE id = $1 AND status IN ('pending', 'stopped')`,
      [account.user_id],
    );

    return was;
  });

  return { replaced, address };
}
