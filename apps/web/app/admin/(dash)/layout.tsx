import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/admin-session";

import { Live } from "./live";
import { Tabs } from "./tabs";

export const dynamic = "force-dynamic";

// One check for every page below, instead of the same eight lines in each.
// /admin/login sits outside this group, so there is no redirect loop.
export default async function DashLayout({ children }: { children: ReactNode }) {
  const store = await cookies();
  if (!(await sessionIsValid(store.get(SESSION_COOKIE)?.value))) {
    redirect("/admin/login");
  }


  return (
    <div className="dash">
      <header className="dash-bar">
        <span className="dash-name">Dashboard</span>

        <Tabs />

        <div className="dash-bar-right">
          <Live />
          {/* The one place a full navigation is the right answer: the session
              cookie is gone and the page must become the login screen. */}
          <form method="post" action="/api/admin/logout">
            <button type="submit" className="sign-out" title="Sign out">
              Sign out
            </button>
          </form>
        </div>
      </header>

      <main className="dash-body">{children}</main>
    </div>
  );
}
