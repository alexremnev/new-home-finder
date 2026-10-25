import { query } from "@/lib/db";

/**
 * One more day of the WhatsApp trial, free, and once per account for ever.
 *
 * Offered five minutes before a trial that delivered ten messages or fewer
 * runs out — see `owed_a_free_day` in the worker — and granted here, by the
 * tap on its button. The tap is also what reopens the 24-hour window, so the
 * extended day and the window it is spent in start together, exactly as they
 * did when the trial began.
 *
 * ── one statement, because the button outlives its message ────────────────
 *
 * A reply button sits in the chat for ever and WhatsApp will deliver a tap on
 * last week's. So "have they had it already" cannot be a read followed by a
 * write: two taps a second apart would both read NULL and both grant a day.
 * `trial_gift_at IS NULL` is in the WHERE, so the database decides, one tap
 * wins, and the loser is told the gift is spent.
 *
 * ── what it resets ────────────────────────────────────────────────────────
 *
 * `plan_from` as well as `plan_until`, which is what makes this the trial run
 * again rather than a longer trial: the allowance is counted from `plan_from`
 * — 0057, 0068 — so a day with the old one would be a second day sharing the
 * first day's thirty messages. The same move `/api/stripe/webhook` makes when
 * a period is bought, and for the same reason.
 *
 * A day from now, not from `plan_until`. The offer goes out while the trial is
 * still running and the tap may come minutes or hours later; "another 24
 * hours" is what the message promises and what the button says, and counting
 * from the old end would quietly make it fewer.
 *
 * ── what false means ──────────────────────────────────────────────────────
 *
 * Either the day has been had, or the account is no longer on the trial —
 * they paid, most likely, in the five minutes between the offer and the tap.
 * Both are answered by the same sentence, which is wrong for the second in a
 * harmless direction: somebody who has just bought a month is not waiting to
 * hear about a free day, and the alternative is a second query to tell two
 * dead ends apart.
 */
export async function giftExtraDay(number: string): Promise<boolean> {
  const rows = await query<{ id: number }>(
    `UPDATE users u
        SET plan_until = now() + interval '1 day',
            plan_from = now(),
            trial_gift_at = now()
       FROM user_channels uc
      WHERE uc.user_id = u.id
        AND uc.channel = 'whatsapp'
        AND uc.address = $1
        AND u.plan = 'trial'
        AND u.trial_gift_at IS NULL
      RETURNING u.id`,
    [number],
  );
  return rows.length > 0;
}
