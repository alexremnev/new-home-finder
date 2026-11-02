import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Bricolage_Grotesque, Manrope } from "next/font/google";

import "./globals.css";

// Two faces, both loaded by next/font so they are self-hosted and never a
// flash of fallback.
//
// Manrope is the interface: everything that is read rather than looked at,
// including the whole dashboard. Tabular figures matter more there than the
// face does — a column of numbers that changes width jitters on every refresh
// — and Manrope carries them.
const body = Manrope({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

// And one for display sizes only: the headlines, the prices, the step numbers.
// A second face earns its download by being used where type is looked at
// rather than read, which is the one place a neutral grotesque has nothing to
// say. Never set on body — see `--font-heading` in globals.css.
const heading = Bricolage_Grotesque({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-heading",
});

const SITE = (process.env.SITE_URL ?? "https://londonhomefinder.co.uk").replace(
  /\/+$/,
  "",
);

const PITCH =
  "A bot that sends real-time alerts for new London rentals. Set a filter by " +
  "area, rent and rooms; every match from Rightmove, Zoopla, OpenRent and " +
  "SpareRoom arrives in Telegram or WhatsApp within minutes of going live. " +
  "Free to try.";

// metadataBase makes every relative url below absolute, which is what a crawler
// and a chat app's preview both need.
export const metadata: Metadata = {
  metadataBase: new URL(SITE),
  title: {
    default: "London Home Finder — real-time alerts for new London rentals",
    template: "%s · London Home Finder",
  },
  description: PITCH,
  applicationName: "London Home Finder",
  keywords: [
    "London rental alerts",
    "real-time property alerts",
    "real time rental alerts London",
    "instant listing alerts",
    "property alert bot",
    "Telegram property bot",
    "WhatsApp property bot",
    "London flat hunting bot",
    "new listings London",
    "flats to rent London",
    "rooms to rent London",
    "Rightmove alerts",
    "Zoopla alerts",
    "OpenRent alerts",
    "SpareRoom alerts",
    "London letting alerts",
    "rent alerts by postcode",
    "be first to new listings",
    "London rental notifications",
  ],
  openGraph: {
    type: "website",
    siteName: "London Home Finder",
    url: SITE,
    title: "Real-time alerts for new London rentals — Telegram & WhatsApp bot",
    description: PITCH,
    locale: "en_GB",
    // No `images` here on purpose. app/opengraph-image.tsx is picked up by
    // file convention, which takes the width, height and mime type from that
    // file's own `size` and `contentType` exports, so the declared size cannot
    // drift from the image the way a hand-written entry does — and a declared
    // size an unfurler cannot match is a preview it may decline to draw. The
    // image is drawn by that file at build time; change it there.
  },
  twitter: {
    // Safe because the image is itself 1.91:1: the large card crops whatever
    // it is given to that ratio, which on the square original the logo used to
    // be took the "HOME FINDER" line off the bottom. There is nothing left to
    // crop.
    card: "summary_large_image",
    title: "Real-time alerts for new London rentals",
    description: PITCH,
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true, "max-image-preview": "large" },
  },
  category: "real estate",
};

// The hero's own near-black, so the address bar on Android and the task
// switcher continue the page rather than drawing a white line above it.
export const viewport = {
  themeColor: "#101215",
};

// No wrapper here. The marketing pages wrap themselves in <Site>, which carries
// the 40rem column and the sunset; the admin needs neither and was being forced
// into both.
export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${body.variable} ${heading.variable}`}>
      <body>{children}</body>
    </html>
  );
}
