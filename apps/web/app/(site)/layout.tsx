import Link from "next/link";
import type { ReactNode } from "react";

import { SUPPORT_EMAIL } from "@/lib/support";

// Everything that is the public site rather than the product: the two pieces of
// chrome every page needs, and nothing else. The width belongs to the page —
// the landing page is two columns wide and the rest are one.
export default function SiteLayout({ children }: { children: ReactNode }) {
  const year = new Date().getFullYear();

  return (
    <div className="site">
      <header className="site-head">
        <div className="site-head-row">
          <Link href="/" className="brand">
            <img src="/logo-mark.png" alt="" width={36} height={36} />
            London Home Finder
          </Link>
        </div>
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
