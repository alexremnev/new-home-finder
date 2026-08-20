// Give somebody more time, for nothing.
//
// The one thing the console can change, so it is the one route that has to be
// careful. Three protections, and each covers a different mistake:
//
//   * The session is checked here as well as in middleware. Middleware is the guard
//     that cannot be forgotten; this is the one that cannot be bypassed by a
//     routing change.
//   * The extension is bounded. An unbounded `days` in a form field is a typo away
//     from a subscription that ends in the year 4000, and nothing downstream would
//     find that strange.
//   * It is written to `admin_actions`, not `payments`. A comp is not revenue, and
//     recording it as revenue would overstate takings in the flattering direction.

import { NextResponse } from "next/server";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/admin-session";
import { query } from "@/lib/db";

export const dynamic = "force-dynamic";

const MAX_DAYS = 365;

export async function POST(request: Request) {
  const cookie = request.headers
    .get("cookie")
    ?.split(";")
    .map((one) => one.trim())
    .find((one) => one.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);
  if (!(await sessionIsValid(cookie))) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }

  const form = await request.formData().catch(() => null);
  const userId = Number(form?.get("user_id"));
  const days = Number(form?.get("days"));
  const reason = String(form?.get("reason") ?? "").slice(0, 200);

  if (!Number.isInteger(userId) || userId <= 0) {
    return NextResponse.json({ error: "which user?" }, { status: 400 });
  }
  if (!Number.isInteger(days) || days < 1 || days > MAX_DAYS) {
    return NextResponse.json({ error: `days must be 1–${MAX_DAYS}` }, { status: 400 });
  }

  // `greatest(now(), plan_until)` so extending a live plan adds to what is left
  // rather than restarting from today — otherwise a generous gesture shortens
  // somebody's subscription.
  const updated = await query<{ plan_until: string }>(
    `UPDATE users
        SET plan_until = greatest(now(), coalesce(plan_until, now()))
                       + make_interval(days => $2::int)
      WHERE id = $1
      RETURNING plan_until::text`,
    [userId, days],
  );
  if (updated.length === 0) {
    return NextResponse.json({ error: "no such user" }, { status: 404 });
  }

  await query(
    `INSERT INTO admin_actions (action, user_id, detail)
     VALUES ('extend_plan', $1, $2::jsonb)`,
    [userId, JSON.stringify({ days, reason, plan_until: updated[0]?.plan_until })],
  );

  // Back to the console rather than JSON: this is a form on a page, and the next
  // thing anybody wants is to see the new date in the table.
  return NextResponse.redirect(new URL("/admin?done=extended", request.url), {
    status: 303,
  });
}
