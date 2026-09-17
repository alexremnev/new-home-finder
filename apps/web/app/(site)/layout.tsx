import Link from "next/link";
import type { ReactNode } from "react";

import { SUPPORT_EMAIL } from "@/lib/support";

import { BrandMark } from "./logos";

// Everything that is the public site rather than the product: the sunset, the
// skyline, the reading column, and the two pieces of chrome every page needs.
export default function SiteLayout({ children }: { children: ReactNode }) {
  const year = new Date().getFullYear();

  return (
    <div className="site">
      <header className="site-head">
        <Link href="/" className="brand">
          <BrandMark />
          London Home Finder <span>· London rentals, the moment they list</span>
        </Link>
      </header>

      <main className="shell">{children}</main>

      <footer className="site-foot">
        <div className="site-foot-row">
          <span>
            Questions or ideas — <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
          </span>
        </div>
        <p className="site-foot-fine">© {year} London Home Finder. All rights reserved.</p>
      </footer>
    </div>
  );
}
