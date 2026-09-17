import type { Metadata } from "next";
import type { ReactNode } from "react";
import { Inter } from "next/font/google";

import "./globals.css";

// One face, loaded by next/font so it is self-hosted and never a flash of
// fallback. Tabular figures matter more than the face does: a dashboard whose
// numbers change width jitters on every refresh.
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-sans",
});

const SITE = (process.env.SITE_URL ?? "https://londonhomefinder.co.uk").replace(
  /\/+$/,
  "",
);

const PITCH =
  "A bot that sends real-time alerts for new London rentals. Set a filter by " +
  "area, rent and rooms; every match from Rightmove, Zoopla and OpenRent " +
  "arrives in Telegram or WhatsApp within minutes of going live. Free to try.";

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
    "London letting alerts",
    "rent alerts by postcode",
    "be first to new listings",
    "London rental notifications",
  ],
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: "London Home Finder",
    url: SITE,
    title: "Real-time alerts for new London rentals — Telegram & WhatsApp bot",
    description: PITCH,
    locale: "en_GB",
    images: [{ url: "/opengraph-image", width: 1200, height: 630,
               alt: "London Home Finder" }],
  },
  twitter: {
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

// The badge's teal, so the address bar on Android and the task switcher match
// the mark instead of defaulting to white.
export const viewport = {
  themeColor: "#4f9aa4",
};

// No wrapper here. The marketing pages wrap themselves in <Site>, which carries
// the 40rem column and the sunset; the admin needs neither and was being forced
// into both.
export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={inter.variable}>
      <body>{children}</body>
    </html>
  );
}
