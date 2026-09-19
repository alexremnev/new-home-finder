import { NextResponse } from "next/server";

import {
  LOCKOUT_WINDOW_MINUTES,
  callerHash,
  recordAttempt,
  signInIsCorrect,
  tooManyFailures,
} from "@/lib/admin";
import { COOKIE_OPTIONS, issueSession } from "@/lib/admin-session";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const form = await request.formData().catch(() => null);
  const username = String(form?.get("username") ?? "");
  const password = String(form?.get("password") ?? "");
  const ipHash = callerHash(request);

  if (await tooManyFailures(ipHash)) {

    return NextResponse.redirect(
      new URL(`/admin/login?e=locked&mins=${LOCKOUT_WINDOW_MINUTES}`, request.url),
      { status: 303 },
    );
  }

  if (!signInIsCorrect(username, password)) {
    await recordAttempt(ipHash, false);
    return NextResponse.redirect(new URL("/admin/login?e=wrong", request.url), {
      status: 303,
    });
  }

  await recordAttempt(ipHash, true);
  const cookie = await issueSession();

  const response = NextResponse.redirect(new URL("/admin", request.url), { status: 303 });
  response.cookies.set({ ...COOKIE_OPTIONS, name: cookie.name, value: cookie.value, maxAge: cookie.maxAge });
  return response;
}
