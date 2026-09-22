type Run = (sql: string, params?: unknown[]) => Promise<Record<string, unknown>[]>;

export type Channel = "telegram" | "whatsapp";

// Everything that should begin when the alerts do, rather than when the form was
// submitted. `/api/subscribe` has to write the criteria and the account before
// the person leaves the page — a token must point at something — but none of it
// should be counted or clocked until a messenger is actually connected.
//
// Idempotent: connecting twice re-activates the same subscription and leaves a
// trial that has already started alone.
export async function beginSubscription(
  run: Run,
  userId: number,
  channel: Channel = "telegram",
): Promise<void> {
  // The newest filter wins and the rest stand down, which is also what makes a
  // returning subscriber's older filters inactive after the merge.
  await run(
    `UPDATE subscriptions s
        SET active = (s.id = newest.id),
            backfill_from = CASE WHEN s.id = newest.id THEN now()
                                 ELSE s.backfill_from END
       FROM (SELECT id FROM subscriptions
              WHERE user_id = $1 ORDER BY created_at DESC, id DESC LIMIT 1) AS newest
      WHERE s.user_id = $1`,
    [userId],
  );

  // The trial runs from the first alert, not from the form. A plan with no
  // duration — the free tier, or a comp — has nothing to start.
  //
  // The length depends on which messenger was connected: a WhatsApp alert is
  // billed per message, so the trial there is shorter. `duration_days_whatsapp`
  // is NULL on every plan where the channel makes no difference, and coalesce
  // then falls back to the one length.
  await run(
    `UPDATE users u
        SET plan_until = now() + make_interval(days => days.count)
       FROM plans p
       CROSS JOIN LATERAL (
         SELECT CASE WHEN $2 = 'whatsapp'
                     THEN coalesce(p.duration_days_whatsapp, p.duration_days)
                     ELSE p.duration_days
                END AS count
       ) AS days
      WHERE u.id = $1
        AND p.key = u.plan
        AND u.plan_until IS NULL
        AND days.count IS NOT NULL`,
    [userId, channel],
  );
}
