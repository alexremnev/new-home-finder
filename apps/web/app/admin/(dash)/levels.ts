// The geometry behind `Gauge`, kept out of ./charts.tsx because both sides of
// the boundary need it.
//
// ./charts.tsx is "use client", and every export of a "use client" module
// becomes a client reference the moment a server component imports it — so a
// plain function living there can be called by the chart that sits in the same
// file and not by the panel that decides whether to render the chart at all.
// `peakOf` is exactly that: the server component asks it whether anything was
// measured, and swaps in a hint if nothing was.
//
// Nothing here touches React, which also makes it the part worth testing: an
// off-by-one in `stretches` does not look like anything — it draws a chart
// that is simply missing a reading.

/** One bucket of a level over time. `null` is "nobody measured", not zero. */
export type Reading = { label: string; value: number | null };

/**
 * The indices of each unbroken stretch of readings.
 *
 * A line with a gap in it is several sub-paths, and this is where they are
 * decided. The case that matters is a stretch of exactly one: an SVG path
 * built from a single point has no segment and renders as nothing at all, so a
 * lone sample between two quiet hours would vanish — and a lone sample is the
 * whole reading on a night when the timers are an hour apart.
 */
export function stretches(data: Reading[]): number[][] {
  const runs: number[][] = [];
  let open: number[] | null = null;
  data.forEach((one, at) => {
    if (one.value === null) {
      open = null;
      return;
    }
    if (!open) {
      open = [];
      runs.push(open);
    }
    open.push(at);
  });
  return runs;
}

/**
 * The highest reading, ignoring the buckets that have none.
 *
 * Null rather than zero when nothing was measured, because that is what tells
 * a panel to print a hint instead of an empty chart — and because zero is a
 * true reading a quiet box can give.
 */
export function peakOf(data: Reading[]): number | null {
  const seen = data.filter((one) => one.value !== null).map((one) => one.value as number);
  return seen.length ? Math.max(...seen) : null;
}
