"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { TelegramMark, WhatsAppMark } from "./logos";

/**
 * The header's call to action, which is a link and not a second form.
 *
 * ── why it scrolls instead of starting ────────────────────────────────────
 * A trial cannot begin until an area has been chosen, so a header button that
 * submitted would have to either fail or invent a filter. What it does instead
 * is what every landing page with a form below the fold does: take you to the
 * form, with the messenger already decided. `#start` is on the landing page,
 * and the href carries the path so it works from the other pages too. One
 * button per messenger rather than one generic one, because which messenger
 * you use is the actual decision — and the logo says which without a word.
 *
 * ── why it disappears on an `?e=` link ────────────────────────────────────
 * That link is a subscriber changing a search they already have. Offering them
 * a free trial is answering a question they did not ask, and offering it on
 * *both* messengers offers somebody writing from WhatsApp a second account on
 * Telegram. The page below already has their one button — "save and go back" —
 * so the right number of calls to action here is none.
 *
 * A client component for the one reason a layout cannot do this itself: it
 * cannot read the query string. Hence also the Suspense boundary it is
 * rendered in — `/upgrade/thanks` is prerendered, and this hook would
 * otherwise refuse to build.
 */
export function HeadCta({ whatsappReady }: { whatsappReady: boolean }) {
  const editing = useSearchParams().get("e");
  if (editing) return null;

  return (
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
  );
}
