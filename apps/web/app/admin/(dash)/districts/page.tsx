import { districtDaily, recipientDaily } from "@/lib/admin-queries";
import { districtNames } from "@/lib/plans";
import { londonDay } from "@/lib/when";

import { DistrictsView } from "./view";

export const dynamic = "force-dynamic";

// A month, fetched once. Every range the page offers is a slice of this, worked
// out in the browser — which is what makes changing range or sort instant
// instead of a round trip that looks like a reload.
const HISTORY_DAYS = 31;

export default async function DistrictsPage() {
  const [days, recipients, names] = await Promise.all([
    districtDaily(HISTORY_DAYS),
    recipientDaily(HISTORY_DAYS),
    districtNames().catch(() => ({}) as Record<string, string>),
  ]);

  return (
    <DistrictsView
      days={days}
      recipients={recipients}
      names={names}
      // Passed rather than computed in the browser: "today" has to mean the
      // same London day the rows are keyed on, whatever clock the reader is on.
      today={londonDay()}
    />
  );
}
