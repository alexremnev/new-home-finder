import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Suspense, type ReactNode } from "react";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/admin-session";

import { RangeFrame, RangePicker } from "./range";
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
    // The frame owns the pending state of a range change: the picker sits in
    // the bar and the panels that reload sit in the body, so whatever marks
    // both as busy has to be above the two of them. See ./range.tsx.
    <RangeFrame>
      <header className="dash-bar">
        <span className="dash-name">Dashboard</span>

        <Tabs />

        <div className="dash-bar-right">
          {/* One range for the whole dashboard, in the top right corner where
              a time range is looked for. */}
          <Suspense fallback={<div className="range-slot" />}>
            <RangePicker />
          </Suspense>

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
    </RangeFrame>
  );
}
