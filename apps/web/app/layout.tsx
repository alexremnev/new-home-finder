import type { ReactNode } from "react";

export const metadata = {
  title: "London Rent Alerts",
  description: "New London rental listings, in Telegram, minutes after they appear.",
};

export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body
        style={{
          fontFamily: "system-ui, -apple-system, sans-serif",
          maxWidth: "34rem",
          margin: "0 auto",
          padding: "2rem 1.25rem 4rem",
          lineHeight: 1.5,
          color: "#111",
        }}
      >
        {children}
      </body>
    </html>
  );
}
