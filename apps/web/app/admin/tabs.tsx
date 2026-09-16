import Link from "next/link";

// The admin had one page and no navigation. Two pages need a way between them,
// and the unhandled count belongs on the tab: a support queue you have to open
// to discover is a support queue that grows.
export function Tabs({ here, waiting }: { here: "overview" | "support"; waiting: number }) {
  return (
    <nav className="tabs">
      <Link href="/admin" className={here === "overview" ? "tab tab-here" : "tab"}>
        Overview
      </Link>
      <Link
        href="/admin/support"
        className={here === "support" ? "tab tab-here" : "tab"}
      >
        Support
        {waiting > 0 && <span className="tab-count">{waiting}</span>}
      </Link>
    </nav>
  );
}
