import type { ReactNode } from "react";

// Nothing under /upgrade is for a crawler: the page needs a short-lived
// token in the query string, and that token is a secret. noindex rather
// than a robots.txt disallow, because a url Google may not crawl is a url
// whose noindex it never reads.
export const metadata = { robots: { index: false, follow: false } };

// The landing page is two columns wide; everything else here is prose and a
// price list, which read better in one narrow column. Kept as a layout so the
// pages underneath do not each have to remember a wrapper.
export default function UpgradeLayout({ children }: { children: ReactNode }) {
  return <div className="column">{children}</div>;
}
