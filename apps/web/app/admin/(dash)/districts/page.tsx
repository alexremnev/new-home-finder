import { districtDaily, recipientDaily } from "@/lib/admin-queries";
import { districtNames } from "@/lib/plans";

import { DEFAULT_SPAN, spanFrom } from "../span";
import { DistrictsView } from "./view";

export const dynamic = "force-dynamic";

// The range comes from the bar, like every other page. This page used to fetch
// a month and slice it in the browser so its own picker felt instant; with one
// picker for the whole dashboard, changing range is a navigation either way,
// and fetching a month to show a day was work nobody asked for.
//
// Sorting and paging are still done in the browser, and those are the two that
// are worth being instant.
export default async function DistrictsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const win = spanFrom(params.w ?? DEFAULT_SPAN);

  const [days, recipients, names] = await Promise.all([
    districtDaily(win),
    recipientDaily(win),
    districtNames().catch(() => ({}) as Record<string, string>),
  ]);

  return (
    <DistrictsView
      days={days}
      recipients={recipients}
      names={names}
      label={win.label}
      span={win.key}
      // How many London days the range covers, for the per-day averages. Taken
      // from the range rather than counted from the rows: a district silent for
      // five days of seven produces less per day, and dividing by the days that
      // happen to have rows would flatter it.
      days_in_range={win.days}
    />
  );
}
