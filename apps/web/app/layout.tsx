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

export const metadata = {
  title: "London Home Finder",
  description: "New London rental listings, in Telegram, minutes after they appear.",
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
