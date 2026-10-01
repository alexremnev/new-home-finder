import Link from "next/link";
import type { ReactNode } from "react";

import { SUPPORT_EMAIL } from "@/lib/support";

import { TelegramMark, WhatsAppMark } from "./logos";

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

          {/* The header's own call to action, which is a link and not a second
              form.
              ── why it scrolls instead of starting ──────────────────────────
              A trial cannot begin until an area has been chosen, so a header
              button that submitted would have to either fail or invent a
              filter. What it does instead is what every landing page with a
              form below the fold does: take you to the form, with the
              messenger already decided. `#start` is on the landing page, and
              the href carries the path so it works from the other pages too.
              One button per messenger rather than one generic one, because
              which messenger you use is the actual decision — and the logo
              says which without a word. */}
          <nav className="site-head-cta" aria-label="Start a free trial">
            <Link
              href="/#start"
              className="head-cta head-cta-telegram"
              aria-label="Start a free trial on Telegram"
            >
              <TelegramMark />
              <span>Start free trial</span>
            </Link>
            {whatsappReady && (
              <Link
                href="/#start"
                className="head-cta head-cta-whatsapp"
                aria-label="Start a free trial on WhatsApp"
              >
                <WhatsAppMark />
                <span>Start free trial</span>
              </Link>
            )}
          </nav>
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
