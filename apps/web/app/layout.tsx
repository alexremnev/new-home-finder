import type { ReactNode } from "react";

// Imported here because this is the only place every page passes through. It was
// missing, which is why the site rendered as unstyled HTML: the stylesheet existed
// and nothing ever loaded it.
import "./globals.css";

export const metadata = {
  title: "London Rent Alerts",
  description: "New London rental listings, in Telegram, minutes after they appear.",
};

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        {children}
      </body>
    </html>
  );
}
