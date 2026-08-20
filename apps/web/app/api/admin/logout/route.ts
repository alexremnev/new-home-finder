// Out.
//
// A POST rather than a link, so nothing else on a page can log the admin out by
// being prefetched or embedded.

import { NextResponse } from "next/server";

import { COOKIE_OPTIONS, SESSION_COOKIE } from "@/lib/admin-session";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const response = NextResponse.redirect(new URL("/admin/login", request.url), {
    status: 303,
  });
  // maxAge 0 rather than deleting: the same attributes have to be sent back for
  // the browser to match and drop the right cookie.
  response.cookies.set({ ...COOKIE_OPTIONS, name: SESSION_COOKIE, value: "", maxAge: 0 });
  return response;
}
