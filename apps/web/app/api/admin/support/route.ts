import { NextResponse } from "next/server";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/admin-session";
import { query } from "@/lib/db";

export const dynamic = "force-dynamic";

function signedIn(request: Request): Promise<boolean> {
  const cookie = request.headers
    .get("cookie")
    ?.split(";")
    .map((one) => one.trim())
    .find((one) => one.startsWith(`${SESSION_COOKIE}=`))
    ?.slice(SESSION_COOKIE.length + 1);
  return sessionIsValid(cookie);
}

export async function POST(request: Request): Promise<Response> {
  if (!(await signedIn(request))) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }

  const form = await request.formData().catch(() => null);
  const action = String(form?.get("action") ?? "");
  const ticketId = Number(form?.get("ticket_id"));
  const note = String(form?.get("note") ?? "").slice(0, 200).trim() || null;

  if (!Number.isInteger(ticketId) || ticketId <= 0) {
    return NextResponse.json({ error: "which ticket?" }, { status: 400 });
  }

  if (action === "handle") {
    await query(
      `UPDATE support_tickets
          SET status = 'handled', handled_at = now(), handled_note = $2
        WHERE id = $1 AND status = 'open'`,
      [ticketId, note],
    );
  } else if (action === "reopen") {
    await query(
      `UPDATE support_tickets
          SET status = 'open', handled_at = NULL
        WHERE id = $1 AND status = 'handled'`,
      [ticketId],
    );
  } else {
    return NextResponse.json({ error: "no such action" }, { status: 400 });
  }

  // A plain form post, so the answer is the page it came from.
  return NextResponse.redirect(new URL("/admin/support", request.url), 303);
}
