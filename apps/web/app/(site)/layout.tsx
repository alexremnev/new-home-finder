import Link from "next/link";
import { Suspense, type ReactNode } from "react";

import { SUPPORT_EMAIL } from "@/lib/support";

import { SiteNav } from "./site-nav";

// Everything that is the public site rather than the product: the two pieces of
// chrome every page needs, and nothing else. The width belongs to the page —
// the landing page is full-bleed bands and the rest are one narrow column.
export default function SiteLayout({ children }: { children: ReactNode }) {
  const year = new Date().getFullYear();
  // The same test the form makes, for the same reason: a WhatsApp button on a
  // deployment with no WhatsApp number configured is a button that cannot work.
  const whatsappReady = Boolean(
    process.env.WHATSAPP_NUMBER && process.env.WA_PHONE_NUMBER_ID,
  );

  // The real mark, in both places it appears. Written once because the footer
  // repeats the header: two copies would be two chances for one of them to be
  // the old logo.
  const brand = (
    <>
      <img className="brand-badge" src="/logo-mark.png" alt="" width={36} height={36} />
      London Home Finder
    </>
  );

  return (
    <div className="site">
      {/* Sticky, so the way back to the form is never more than a glance away
          on a page this tall. Translucent over the hero's own dark, which is
          why the bar has no fill of its own below the blur. */}
      <header className="site-head">
        <div className="site-head-row">
          <Link href="/" className="brand">
            {brand}
          </Link>

          <Suspense fallback={null}>
            <SiteNav whatsappReady={whatsappReady} />
          </Suspense>
        </div>
      </header>

      <main className="shell">{children}</main>

      <footer className="site-foot">
        <div className="site-foot-row">
          <Link href="/" className="brand brand-foot">
            {brand}
          </Link>
          <p>
            Questions or ideas —{" "}
            <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
          </p>
          <p className="site-foot-fine">
            © {year} London Home Finder. All rights reserved.
          </p>
        </div>
      </footer>
    </div>
  );
}
