// The only way in.
//
// A POST, so the password never lands in a URL, a log line, or a browser history
// entry. Node runtime because scrypt lives there.

import { NextResponse } from "next/server";

import {
  LOCKOUT_WINDOW_MINUTES,
  callerHash,
  passwordIsCorrect,
  recordAttempt,
  tooManyFailures,
} from "@/lib/admin";
import { COOKIE_OPTIONS, issueSession } from "@/lib/admin-session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const form = await request.formData().catch(() => null);
  const password = String(form?.get("password") ?? "");
  const ipHash = callerHash(request);

  if (await tooManyFailures(ipHash)) {
    // The same answer as a wrong password would give, plus the reason. There is
    // nothing to hide here: telling somebody they are locked out does not help
    // them in, and not telling them makes a locked-out admin think the password
    // changed.
    return NextResponse.redirect(
      new URL(`/admin/login?e=locked&mins=${LOCKOUT_WINDOW_MINUTES}`, request.url),
      { status: 303 },
    );
  }

  if (!passwordIsCorrect(password)) {
    await recordAttempt(ipHash, false);
    return NextResponse.redirect(new URL("/admin/login?e=wrong", request.url), {
      status: 303,
    });
  }

  await recordAttempt(ipHash, true);
  const cookie = await issueSession();
  // 303, so the browser turns the POST into a GET and the password is not
  // re-submitted by a refresh.
  const response = NextResponse.redirect(new URL("/admin", request.url), { status: 303 });
  response.cookies.set({ ...COOKIE_OPTIONS, name: cookie.name, value: cookie.value, maxAge: cookie.maxAge });
  return response;
}
