import type { ReactNode } from "react";

import { SUPPORT_EMAIL } from "@/lib/support";

// Everything that is the public site rather than the product: the sunset, the
// skyline, the reading column and the way to reach a person. It was on <body>
// and in the root layout, so the admin inherited a 40rem column and a gradient
// behind its charts.
export default function SiteLayout({ children }: { children: ReactNode }) {
  return (
    <div className="site">
      <main className="shell">{children}</main>

      <footer className="site-foot">
        Questions or ideas —{" "}
        <a href={`mailto:${SUPPORT_EMAIL}`}>{SUPPORT_EMAIL}</a>
      </footer>
    </div>
  );
}
