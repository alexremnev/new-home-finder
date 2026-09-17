import { NextResponse } from "next/server";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/admin-session";
import { query, transaction } from "@/lib/db";

export const dynamic = "force-dynamic";

const MAX_DAYS = 365;

const ACTIONS = new Set([
  "extend_plan",
  "set_plan",
  "block",
  "unblock",
  "pause",
  "resume",
  "erase",
]);

function signedIn(request: Request): Promise<boolean> {
  const cookie = request.headers
    .get("cookie")
    ?.split(";")
    .map((one) => one.trim())
    .find((one) => one.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);
  return sessionIsValid(cookie);
}

export async function POST(request: Request) {
  if (!(await signedIn(request))) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }

  const form = await request.formData().catch(() => null);
  const action = String(form?.get("action") ?? "");
  const userId = Number(form?.get("user_id"));
  const reason = String(form?.get("reason") ?? "").slice(0, 200);

  if (!ACTIONS.has(action)) {
    return NextResponse.json({ error: "no such action" }, { status: 400 });
  }
  if (!Number.isInteger(userId) || userId <= 0) {
    return NextResponse.json({ error: "which user?" }, { status: 400 });
  }

  const detail: Record<string, unknown> = { reason };
  let done = "";

  try {
    await transaction(async (run) => {
      const exists = await run(`SELECT id FROM users WHERE id = $1`, [userId]);
      if (exists.length === 0) throw new Error("no such user");

      if (action === "extend_plan") {
        const days = Number(form?.get("days"));
        if (!Number.isInteger(days) || days < 1 || days > MAX_DAYS) {
          throw new Error(`days must be 1-${MAX_DAYS}`);
        }
        const rows = await run(
          `UPDATE users
              SET plan_until = greatest(now(), coalesce(plan_until, now()))
                             + make_interval(days => $2::int)
            WHERE id = $1
            RETURNING plan_until::text`,
          [userId, days],
        );
        detail.days = days;
        detail.plan_until = rows[0]?.plan_until;
        done = `extended by ${days} days`;
      }

      if (action === "set_plan") {
        const plan = String(form?.get("plan") ?? "");
        const known = await run(
          `SELECT key, duration_days FROM plans WHERE key = $1`, [plan],
        );
        if (known.length === 0) throw new Error("no such plan");
        const rows = await run(
          `UPDATE users
              SET plan = $2,
                  plan_until = CASE
                      WHEN $3::int IS NULL THEN NULL
                      ELSE now() + make_interval(days => $3::int)
                  END
            WHERE id = $1
            RETURNING plan, plan_until::text`,
          [userId, plan, known[0]?.duration_days ?? null],
        );
        detail.plan = plan;
        detail.plan_until = rows[0]?.plan_until;
        done = `moved to ${plan}`;
      }

      if (action === "block" || action === "unblock") {
        const status = action === "block" ? "blocked" : "active";
        await run(`UPDATE users SET status = $2 WHERE id = $1`, [userId, status]);
        if (action === "block") {
          await run(
            `DELETE FROM notifications WHERE user_id = $1 AND status = 'queued'`,
            [userId],
          );
        }
        detail.status = status;
        done = action === "block" ? "blocked" : "unblocked";
      }

      if (action === "pause" || action === "resume") {
        const active = action === "resume";
        await run(
          `UPDATE users
              SET status = $2, stopped_at = CASE WHEN $3 THEN NULL ELSE now() END
            WHERE id = $1`,
          [userId, active ? "active" : "stopped", active],
        );
        await run(`UPDATE subscriptions SET active = $2 WHERE user_id = $1`, [
          userId,
          active,
        ]);
        if (!active) {
          await run(
            `DELETE FROM notifications WHERE user_id = $1 AND status = 'queued'`,
            [userId],
          );
        }
        done = active ? "resumed" : "paused";
      }

      if (action === "erase") {
        const paid = await run(
          `SELECT count(*)::int AS n FROM payments WHERE user_id = $1`, [userId],
        );
        const hasPayments = Number(paid[0]?.n ?? 0) > 0;

        await run(`DELETE FROM user_channels WHERE user_id = $1`, [userId]);
        await run(`DELETE FROM user_tokens WHERE user_id = $1`, [userId]);
        await run(`DELETE FROM notifications WHERE user_id = $1 AND status = 'queued'`, [
          userId,
        ]);
        await run(`UPDATE subscriptions SET active = false WHERE user_id = $1`, [userId]);

        if (hasPayments) {
          await run(
            `UPDATE users SET status = 'erased', stopped_at = now() WHERE id = $1`,
            [userId],
          );
          detail.kept = "payment history";
          done = "personal data erased, payment history kept";
        } else {
          await run(`DELETE FROM users WHERE id = $1`, [userId]);
          detail.kept = "nothing";
          done = "deleted";
        }
      }

      const gone = detail.kept === "nothing";
      await run(
        `INSERT INTO admin_actions (action, user_id, detail)
         VALUES ($1, $2, $3::jsonb)`,
        [action, gone ? null : userId, JSON.stringify({ ...detail, user_id: userId })],
      );
    });
  } catch (error) {
    const message = String(error).replace(/^Error:\s*/, "");
    return NextResponse.json({ error: message }, { status: 400 });
  }

  if (request.headers.get("x-async")) {
    // The erase that removed the row has nowhere to go back to, so the caller
    // is told where to send the person instead.
    const gone = action === "erase" && done === "deleted";
    return NextResponse.json({ ok: true, done, goto: gone ? "/admin/subscribers" : null });
  }

  const back =
    action === "erase" && done === "deleted"
      ? `/admin?done=${encodeURIComponent(done)}`
      : `/admin/subscribers/${userId}?done=${encodeURIComponent(done)}`;
  return NextResponse.redirect(new URL(back, request.url), { status: 303 });
}
