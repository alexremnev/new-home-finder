"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { useNav } from "./range";

const TABS = [
  { href: "/admin", label: "System" },
  { href: "/admin/subscribers", label: "Subscribers" },
  { href: "/admin/payments", label: "Payments" },
  { href: "/admin/visitors", label: "Visitors" },
  { href: "/admin/districts", label: "Districts" },
] as const;

// A client component so the current tab can be marked, the chosen range can
// travel with it, and the move can be marked as busy.
//
// ── why the range travels ────────────────────────────────────────────────
//
// There is one range for the whole dashboard, so leaving `?w=` behind on a tab
// change silently reset it: you picked "past 1 hour" on System, clicked
// Visitors, and read a day's numbers under a picker that had gone back to
// saying "Past 1 day". The other parameters stay behind on purpose — `job` and
// `level` belong to the System tab's run log and mean nothing anywhere else.

// Reading `?w=` needs a Suspense boundary; the fallback is the same row with
// no range on its links, so there is nothing to see happening.
export function Tabs() {
  return (
    <Suspense fallback={<Row span={null} />}>
      <Live />
    </Suspense>
  );
}

function Live() {
  const params = useSearchParams();
  const { go } = useNav();
  return <Row span={params.get("w")} go={go} />;
}

// /admin is a prefix of every other route, so it matches exactly; the rest
// match their subtree, so a subscriber's own page keeps Subscribers lit.
function Row({
  span,
  go,
}: {
  span: string | null;
  go?: (href: string, scroll?: boolean) => void;
}) {
  const here = usePathname();

  return (
    <nav className="tabs" aria-label="Sections">
      {TABS.map((tab) => {
        const on = tab.href === "/admin" ? here === "/admin" : here.startsWith(tab.href);
        const href = span ? `${tab.href}?w=${encodeURIComponent(span)}` : tab.href;
        return (
          <Link
            key={tab.href}
            href={href}
            className={on ? "tab tab-on" : "tab"}
            aria-current={on ? "page" : undefined}
            // Through the frame's transition rather than the link's own, so the
            // body dims and the bar draws its progress line while the next
            // tab's queries run, and the tab you are leaving stays readable
            // instead of blanking to a row of skeletons.
            onClick={(event) => {
              if (!go) return;
              if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              if (!on) go(href, true);
            }}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
