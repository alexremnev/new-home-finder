import { NextResponse } from "next/server";

import { COOKIE_OPTIONS, SESSION_COOKIE } from "@/lib/admin-session";

export const dynamic = "force-dynamic";

export async function POST(request: Request) {
  const response = NextResponse.redirect(new URL("/admin/login", request.url), {
    status: 303,
  });

  response.cookies.set({ ...COOKIE_OPTIONS, name: SESSION_COOKIE, value: "", maxAge: 0 });
  return response;
}
