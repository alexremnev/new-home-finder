import Link from "next/link";
import { Suspense, type ReactNode } from "react";

import { SUPPORT_EMAIL } from "@/lib/support";

import { HeadCta } from "./head-cta";

// Everything that is the public site rather than the product: the two pieces of
// chrome every page needs, and nothing else. The width belongs to the page —
// the landing page is two columns wide and the rest are one.
export default function SiteLayout({ children }: { children: ReactNode }) {
  const year = new Date().getFullYear();
  // The same test the form makes, for the same reason: a WhatsApp button on a
  // deployment with no WhatsApp number configured is a button that cannot work.
  const whatsappReady = Boolean(
    process.env.WHATSAPP_NUMBER && process.env.WA_PHONE_NUMBER_ID,
  );

  return (
    <div className="site">
      <header className="site-head">
        <div className="site-head-row">
          <Link href="/" className="brand">
            <img src="/logo-mark.png" alt="" width={36} height={36} />
            London Home Finder
          </Link>

          <Suspense fallback={null}>
            <HeadCta whatsappReady={whatsappReady} />
          </Suspense>
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
