import { cookies } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { SESSION_COOKIE, sessionIsValid } from "@/lib/admin-session";
import { ticketCounts } from "@/lib/admin-queries";

import { Live } from "./live";

export const dynamic = "force-dynamic";

const TABS = [
  { href: "/admin", label: "System" },
  { href: "/admin/subscribers", label: "Subscribers" },
  { href: "/admin/payments", label: "Payments" },
  { href: "/admin/support", label: "Support" },
] as const;

// One check for every page below, instead of the same eight lines in each.
// /admin/login sits outside this group, so there is no redirect loop.
export default async function DashLayout({ children }: { children: ReactNode }) {
  const store = await cookies();
  if (!(await sessionIsValid(store.get(SESSION_COOKIE)?.value))) {
    redirect("/admin/login");
  }

  const waiting = await ticketCounts().catch(() => ({ open: 0, handled: 0 }));

  return (
    <div className="dash">
      <header className="dash-bar">
        <form method="post" action="/api/admin/logout">
          <button type="submit" className="sign-out" title="Sign out">
            Sign out
          </button>
        </form>

        <nav className="tabs">
          {TABS.map((tab) => (
            <Link key={tab.href} href={tab.href} className="tab">
              {tab.label}
              {tab.label === "Support" && waiting.open > 0 && (
                <span className="tab-count">{waiting.open}</span>
              )}
            </Link>
          ))}
        </nav>

        <span className="dash-name">Dashboard</span>
        <Live />
      </header>

      <main className="dash-body">{children}</main>
    </div>
  );
}
