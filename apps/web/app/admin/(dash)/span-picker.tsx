"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";

import { DEFAULT_SPAN, SPAN_LABELS } from "./span";

// One picker for the whole dashboard, in the bar rather than on each page.
//
// A client component so it can build its own links: it keeps whatever page you
// are on and whatever filters you have set, and changes only `w`. That is what
// lets it live in the layout and serve five pages without any of them passing
// it anything.
export function SpanPicker() {
  const here = usePathname();
  const params = useSearchParams();
  const chosen = params.get("w") ?? DEFAULT_SPAN;

  const link = (key: string) => {
    const next = new URLSearchParams(params.toString());
    next.set("w", key);
    // Changing the range starts the list again; staying on page 7 of a range
    // that now has two pages shows nothing.
    next.delete("p");
    return `${here}?${next.toString()}`;
  };

  return (
    <div className="span-picker" role="group" aria-label="Time range">
      {SPAN_LABELS.map((one) => (
        <Link
          key={one.key}
          href={link(one.key)}
          className={one.key === chosen ? "win win-on" : "win"}
          aria-current={one.key === chosen ? "true" : undefined}
        >
          {one.label}
        </Link>
      ))}
    </div>
  );
}
