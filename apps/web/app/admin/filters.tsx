// The filter bar.
//
// Links, not a form, and not a client component. Every filter is a query parameter,
// so the state of the console is its URL: a view can be bookmarked, shared, and
// reloaded, and the back button does what a back button should. A `useState` filter
// bar gives none of that and costs a hydration boundary.
//
// One row above everything, never inside a chart card. A filter that lives beside one
// chart looks like it applies only to that chart — and when it applies to all of
// them, that is a lie about the numbers.

import type { Range } from "@/lib/admin-queries";

const RANGES: { days: Range; label: string }[] = [
  { days: 1, label: "Today" },
  { days: 7, label: "7 days" },
  { days: 30, label: "30 days" },
  { days: 90, label: "90 days" },
];

export type Chosen = { range: Range; job?: string; level?: string; q?: string };

/** The current filters with one value replaced, as a query string. */
function withOne(current: Chosen, name: string, value: string | undefined): string {
  const params = new URLSearchParams();
  const merged: Record<string, string | undefined> = {
    range: String(current.range),
    job: current.job,
    level: current.level,
    q: current.q,
    [name]: value,
  };
  for (const [key, one] of Object.entries(merged)) {
    if (one) params.set(key, one);
  }
  return `/admin?${params.toString()}`;
}

export function FilterBar({
  chosen, jobs, levels,
}: {
  chosen: Chosen;
  jobs: string[];
  levels: { label: string; value: number }[];
}) {
  return (
    <div className="filters">
      <div className="filter-group">
        {RANGES.map((one) => (
          <a
            key={one.days}
            href={withOne(chosen, "range", String(one.days))}
            className={one.days === chosen.range ? "range-on" : undefined}
          >
            {one.label}
          </a>
        ))}
      </div>

      {jobs.length > 1 && (
        <div className="filter-group">
          {/* "All" is a link that clears the filter rather than a value called
              "all": the absence of a filter and a filter meaning everything are the
              same thing, and having both invites them to disagree. */}
          <a
            href={withOne(chosen, "job", undefined)}
            className={!chosen.job ? "range-on" : undefined}
          >
            All jobs
          </a>
          {jobs.map((job) => (
            <a
              key={job}
              href={withOne(chosen, "job", job)}
              className={job === chosen.job ? "range-on" : undefined}
            >
              {job}
            </a>
          ))}
        </div>
      )}

      {levels.length > 0 && (
        <div className="filter-group">
          <a
            href={withOne(chosen, "level", undefined)}
            className={!chosen.level ? "range-on" : undefined}
          >
            All levels
          </a>
          {levels.map((one) => (
            <a
              key={one.label}
              href={withOne(chosen, "level", one.label)}
              className={one.label === chosen.level ? "range-on" : undefined}
            >
              {one.label} <span className="filter-count">{one.value}</span>
            </a>
          ))}
        </div>
      )}

      {/* A GET form, so searching lands in the URL like every other filter. The
          other filters ride along as hidden fields — otherwise searching would
          quietly reset the date range. */}
      <form method="get" action="/admin" className="filter-search">
        <input type="hidden" name="range" value={String(chosen.range)} />
        {chosen.job && <input type="hidden" name="job" value={chosen.job} />}
        {chosen.level && <input type="hidden" name="level" value={chosen.level} />}
        <input
          type="text"
          name="q"
          defaultValue={chosen.q ?? ""}
          placeholder="Search the log…"
          aria-label="Search the log"
        />
        {chosen.q && (
          <a href={withOne(chosen, "q", undefined)} className="filter-clear">
            clear
          </a>
        )}
      </form>
    </div>
  );
}
