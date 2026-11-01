"use client";

import Link from "next/link";
import { useState } from "react";
import { useSearchParams } from "next/navigation";

import { TelegramMark, WhatsAppMark } from "./logos";

// The sections the landing page has, in the order it has them. `/#…` rather
// than `#…` so the links still work from /upgrade and the thanks page, which
// have no sections of their own.
const LINKS = [
  { href: "/#search", text: "Search" },
  { href: "/#how", text: "How it works" },
  { href: "/#pricing", text: "Pricing" },
  { href: "/#faq", text: "FAQ" },
];

/**
 * The header's navigation and its call to action.
 *
 * ── why the buttons scroll instead of starting ────────────────────────────
 * A trial cannot begin until an area has been chosen, so a header button that
 * submitted would have to either fail or invent a filter. What it does instead
 * is what every landing page with a form below the fold does: take you to the
 * form. One button per messenger, because which messenger you use is the
 * actual decision — and the logo says which without a word.
 *
 * ── why it all disappears on an `?e=` link ───────────────────────────────
 * That link is a subscriber changing a search they already have. The page
 * drops everything below the form for them — the plans, the questions, the
 * pitch — so a bar of links to sections that are not on the page would be a
 * bar of dead ends, and a free trial is answering a question they did not ask.
 * The brand stays, because it says where you are.
 *
 * A client component for two reasons a layout cannot do either of: it reads
 * the query string, and the narrow-screen menu opens. Hence also the Suspense
 * boundary it is rendered in — `/upgrade/thanks` is prerendered, and
 * `useSearchParams` would otherwise refuse to build.
 */
export function SiteNav({ whatsappReady }: { whatsappReady: boolean }) {
  const editing = useSearchParams().get("e");
  const [open, setOpen] = useState(false);
  if (editing) return null;

  const trials = (
    <>
      <Link
        href="/#start"
        className="head-cta head-cta-telegram"
        aria-label="Start a free trial on Telegram"
        onClick={() => setOpen(false)}
      >
        <TelegramMark />
        <span>Start free trial</span>
      </Link>
      {whatsappReady && (
        <Link
          href="/#start"
          className="head-cta head-cta-whatsapp"
          aria-label="Start a free trial on WhatsApp"
          onClick={() => setOpen(false)}
        >
          <WhatsAppMark />
          <span>Start free trial</span>
        </Link>
      )}
    </>
  );

  return (
    <>
      <nav className="site-nav" aria-label="Sections">
        {LINKS.map((link) => (
          <Link key={link.href} href={link.href}>
            {link.text}
          </Link>
        ))}
      </nav>

      <div className="site-head-cta">{trials}</div>

      {/* Four links and two buttons do not fit beside the brand on a phone.
          The toggle is the only part of this that needs state, and the panel
          it opens is the same links again rather than a second set — one list
          in the markup would have to be either a row or a sheet, and it is
          both. */}
      <button
        type="button"
        className="nav-toggle"
        aria-expanded={open}
        aria-controls="site-menu"
        aria-label={open ? "Close menu" : "Open menu"}
        onClick={() => setOpen((was) => !was)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          {open ? (
            <path
              d="M6 6l12 12M18 6 6 18"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          ) : (
            <path
              d="M4 8h16M4 16h16"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          )}
        </svg>
      </button>

      {open && (
        <div className="nav-panel" id="site-menu">
          {LINKS.map((link) => (
            <Link key={link.href} href={link.href} onClick={() => setOpen(false)}>
              {link.text}
            </Link>
          ))}
          <div className="nav-panel-cta">{trials}</div>
        </div>
      )}
    </>
  );
}
