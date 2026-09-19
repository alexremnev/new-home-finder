import type { ReactNode } from "react";

// The landing page is two columns wide; everything else here is prose and a
// price list, which read better in one narrow column. Kept as a layout so the
// pages underneath do not each have to remember a wrapper.
export default function UpgradeLayout({ children }: { children: ReactNode }) {
  return <div className="column">{children}</div>;
}
