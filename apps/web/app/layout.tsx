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
        {/* One container rather than letting each top-level child centre itself.
            That was the layout drifting on wide screens: `body > *` gave the
            heading, the paragraph and the form three independent centred boxes,
            and anything that was not a direct child fell outside all of them. */}
        <main className="shell">{children}</main>
      </body>
    </html>
  );
}
