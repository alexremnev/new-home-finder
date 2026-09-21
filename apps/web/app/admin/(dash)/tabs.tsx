"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/admin", label: "System" },
  { href: "/admin/subscribers", label: "Subscribers" },
  { href: "/admin/payments", label: "Payments" },
  { href: "/admin/visitors", label: "Visitors" },
  { href: "/admin/districts", label: "Districts" },
] as const;

// A client component only so the current tab can be marked. /admin is a prefix
// of every other route, so it matches exactly; the rest match their subtree, so
// a subscriber's own page keeps Subscribers lit.
export function Tabs() {
  const here = usePathname();

  return (
    <nav className="tabs" aria-label="Sections">
      {TABS.map((tab) => {
        const on = tab.href === "/admin" ? here === "/admin" : here.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={on ? "tab tab-on" : "tab"}
            aria-current={on ? "page" : undefined}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
