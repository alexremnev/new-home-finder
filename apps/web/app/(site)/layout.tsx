import type { ReactNode } from "react";

// Everything that is the public site rather than the product: the sunset, the
// skyline and the reading column. It was on <body> and in the root layout, so
// the admin inherited a 40rem column and a gradient behind its charts.
export default function SiteLayout({ children }: { children: ReactNode }) {
  return (
    <div className="site">
      <main className="shell">{children}</main>
    </div>
  );
}
