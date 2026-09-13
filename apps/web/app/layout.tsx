import type { ReactNode } from "react";

import "./globals.css";

export const metadata = {
  title: "London Rent Alerts",
  description: "New London rental listings, in Telegram, minutes after they appear.",
};

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>

        <main className="shell">{children}</main>
      </body>
    </html>
  );
}
