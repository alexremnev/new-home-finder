// The guard on /admin.
//
// Here rather than in each page, because a page that forgets to check is a page
// that is public, and there is no way to notice that from reading the page. A
// middleware matcher cannot be forgotten by the next file added under /admin.
//
// It only checks the signature on the session cookie — no database, no scrypt — so
// it runs on the edge and costs nothing per request. The routes still do their own
// checks where they change data: defence in depth is the point, and a bug in one
// layer should not be an open door.

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/admin-session";

export async function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // The login page and the login endpoint have to be reachable without a session,
  // or there is no way to get one.
  if (pathname === "/admin/login" || pathname.startsWith("/api/admin/login")) {
    return NextResponse.next();
  }

  if (await sessionIsValid(request.cookies.get(SESSION_COOKIE)?.value)) {
    return NextResponse.next();
  }

  // An API call gets a status; a page gets sent to the login form. Redirecting an
  // API call would answer a fetch with an HTML page, which reads as a bug.
  if (pathname.startsWith("/api/admin")) {
    return NextResponse.json({ error: "not signed in" }, { status: 401 });
  }
  return NextResponse.redirect(new URL("/admin/login", request.url));
}

export const config = {
  // "/admin" listed as well as "/admin/:path*": the pattern with the wildcard is
  // documented to match zero segments, and relying on that for the one route that
  // matters most is a bet with no upside.
  matcher: ["/admin", "/admin/:path*", "/api/admin/:path*"],
};
