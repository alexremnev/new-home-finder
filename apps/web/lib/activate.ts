type Run = (sql: string, params?: unknown[]) => Promise<Record<string, unknown>[]>;

// Everything that should begin when the alerts do, rather than when the form was
// submitted. `/api/subscribe` has to write the criteria and the account before
// the person leaves the page — a token must point at something — but none of it
// should be counted or clocked until a messenger is actually connected.
//
// Idempotent: connecting twice re-activates the same subscription and leaves a
// trial that has already started alone.
export async function beginSubscription(run: Run, userId: number): Promise<void> {
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
  await run(
    `UPDATE users u
        SET plan_until = now() + make_interval(days => p.duration_days)
       FROM plans p
      WHERE u.id = $1
        AND p.key = u.plan
        AND u.plan_until IS NULL
        AND p.duration_days IS NOT NULL`,
    [userId],
  );
}
