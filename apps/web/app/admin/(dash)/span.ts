// The one time range the whole dashboard reads.
//
// Every page used to carry its own picker with its own options, so comparing a
// spike in visits against a spike in intake meant holding two different
// meanings of "7d" in your head. So there is one list, one parameter (`?w=`),
// and one resolver.
//
// ── what the list offers ─────────────────────────────────────────────────
//
// Rolling windows ending now, the same ladder Datadog's log explorer offers,
// plus any absolute range you care to name. There is no "today" and no
// "yesterday": a calendar day is a different question from a window, and
// mixing the two in one control meant "1d" and "Today" sat side by side
// meaning different things. An absolute range says exactly what it covers.
//
// ── how a span reaches a query ───────────────────────────────────────────
//
// Two ways, because the tables are keyed two ways:
//
//   * `mins` / `endMins` — minutes before now, for the timestamp columns.
//     `endMins` is 0 for a rolling window, and positive for an absolute range
//     that ended in the past.
//   * `fromDay` / `toDay` — inclusive London dates, for the tables keyed on a
//     DATE: `site_visits.day` and `district_days.day`.

export type SpanKey = string;

type Preset = {
  key: string;
  /** As the button and the menu say it: "Past 1 hour". */
  label: string;
  /** As a column heading says it, where there is no room for a sentence. */
  short: string;
  mins: number;
};

const PRESETS: Preset[] = [
  { key: "15m", label: "Past 15 minutes", short: "15m", mins: 15 },
  { key: "30m", label: "Past 30 minutes", short: "30m", mins: 30 },
  { key: "1h", label: "Past 1 hour", short: "1h", mins: 60 },
  { key: "4h", label: "Past 4 hours", short: "4h", mins: 4 * 60 },
  { key: "1d", label: "Past 1 day", short: "1d", mins: 24 * 60 },
  { key: "2d", label: "Past 2 days", short: "2d", mins: 2 * 24 * 60 },
  { key: "1w", label: "Past 1 week", short: "1w", mins: 7 * 24 * 60 },
  { key: "1mo", label: "Past 1 month", short: "30d", mins: 30 * 24 * 60 },
  { key: "3mo", label: "Past 3 months", short: "90d", mins: 90 * 24 * 60 },
];

export const SPAN_PRESETS: { key: string; label: string }[] = PRESETS.map(
  ({ key, label }) => ({ key, label }),
);

// A day. Long enough to show a shape, short enough that the numbers are about
// now rather than about the week.
export const DEFAULT_SPAN: SpanKey = "1d";

export type Span = {
  key: SpanKey;
  label: string;
  short: string;
  /** Minutes before now where the window starts. */
  mins: number;
  /** Minutes before now where it ends. 0 means "now". */
  endMins: number;
  /** Inclusive London dates, for the tables keyed on a DATE. */
  fromDay: string;
  toDay: string;
  /** How many London days the window touches, at least 1. */
  days: number;
  /** The window's length in hours, for chart bucketing. */
  hours: number;
  /** Set for an absolute range, so the picker can reopen the calendar on it. */
  from?: number;
  to?: number;
};

const LONDON = "Europe/London";

const DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: LONDON,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const WALL = new Intl.DateTimeFormat("en-GB", {
  timeZone: LONDON,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

const STAMP = new Intl.DateTimeFormat("en-GB", {
  timeZone: LONDON,
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

// An absolute range is two epoch-millisecond instants. Milliseconds rather
// than a written-out date because an instant has no timezone to argue about:
// the same URL means the same two moments wherever it is opened.
const ABSOLUTE = /^(\d{10,16})~(\d{10,16})$/;

/** How far London is ahead of UTC at a given instant, in milliseconds. */
function londonOffset(at: number): number {
  const parts = WALL.formatToParts(new Date(at));
  const value = (type: string) =>
    Number(parts.find((one) => one.type === type)?.value ?? 0);
  const asIfUtc = Date.UTC(
    value("year"),
    value("month") - 1,
    value("day"),
    // "24" rather than "00" is what en-GB hour12:false gives at midnight.
    value("hour") % 24,
    value("minute"),
    value("second"),
  );
  return asIfUtc - at;
}

/**
 * A wall clock typed into the calendar — "2026-09-28T14:30" — as an instant,
 * reading it as London time.
 *
 * London rather than the browser's own zone because every other number on the
 * dashboard is London: a range typed as 09:00 should start where the charts'
 * 09:00 is, whoever is reading.
 */
export function londonToMs(wall: string): number | null {
  const found = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(wall);
  if (!found) return null;
  const [year, month, day, hour, minute] = found.slice(1, 6).map(Number) as [
    number, number, number, number, number,
  ];
  const asIfUtc = Date.UTC(year, month - 1, day, hour, minute);
  // Twice: the offset at the guessed instant can belong to the other side of a
  // clock change, and the second pass lands on the right side of it.
  const once = asIfUtc - londonOffset(asIfUtc);
  return asIfUtc - londonOffset(once);
}

/** The reverse, for filling the calendar's inputs in. */
export function msToLondon(at: number): string {
  const parts = WALL.formatToParts(new Date(at));
  const value = (type: string) => parts.find((one) => one.type === type)?.value ?? "00";
  const hour = String(Number(value("hour")) % 24).padStart(2, "0");
  return `${value("year")}-${value("month")}-${value("day")}T${hour}:${value("minute")}`;
}

/** The key an absolute range travels under. */
export function absoluteKey(from: number, to: number): string {
  return `${Math.round(from)}~${Math.round(to)}`;
}

/** The London date of an instant, as YYYY-MM-DD. */
function dayOf(at: number): string {
  return DAY.format(new Date(at));
}

/** How many London days two inclusive dates span. */
function daysBetween(fromDay: string, toDay: string): number {
  const from = Date.parse(`${fromDay}T00:00:00Z`);
  const to = Date.parse(`${toDay}T00:00:00Z`);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return 1;
  return Math.max(1, Math.round((to - from) / 86_400_000) + 1);
}

function build(
  key: string,
  label: string,
  short: string,
  from: number,
  to: number,
  now: number,
  absolute: boolean,
): Span {
  return {
    key,
    label,
    short,
    mins: (now - from) / 60_000,
    endMins: Math.max(0, (now - to) / 60_000),
    fromDay: dayOf(from),
    toDay: dayOf(to),
    days: daysBetween(dayOf(from), dayOf(to)),
    hours: Math.max(1 / 60, (to - from) / 3_600_000),
    ...(absolute ? { from, to } : {}),
  };
}

export function spanFrom(value: string | undefined, now: Date = new Date()): Span {
  const at = now.getTime();

  const absolute = value ? ABSOLUTE.exec(value) : null;
  if (absolute) {
    const [first, second] = [Number(absolute[1]), Number(absolute[2])].sort(
      (a, b) => a - b,
    ) as [number, number];
    // A range cannot run past now — the tables have nothing there — and cannot
    // be empty, or every query would return nothing and look like an outage.
    const to = Math.min(second, at);
    const from = Math.min(first, to - 60_000);
    const label = `${STAMP.format(new Date(from))} → ${STAMP.format(new Date(to))}`;
    return build(absoluteKey(from, to), label, "range", from, to, at, true);
  }

  const found =
    PRESETS.find((one) => one.key === value) ??
    PRESETS.find((one) => one.key === DEFAULT_SPAN)!;
  return build(
    found.key,
    found.label,
    found.short,
    at - found.mins * 60_000,
    at,
    at,
    false,
  );
}

// How wide a bucket keeps a chart readable at each width: about 30-60 points
// across, so a line has shape without turning into noise.
export function bucketMinutes(hours: number): number {
  if (hours <= 0.5) return 1;
  if (hours <= 1) return 2;
  if (hours <= 3) return 5;
  if (hours <= 6) return 10;
  if (hours <= 12) return 15;
  if (hours <= 24) return 30;
  if (hours <= 72) return 60;
  if (hours <= 168) return 180;
  if (hours <= 744) return 720;
  // Beyond a month, one point per two days: a quarter at twelve-hour buckets is
  // a hundred and eighty points through a 120-pixel chart.
  return 2_880;
}

/**
 * The range as it reads inside a sentence: "in the past 1 hour", "between
 * 28 Sep 09:00 and 29 Sep 17:30". Needed because the button's label is not a
 * sentence fragment — prose built from it said "last Past 1 hour".
 */
export function spanWords(span: Span): string {
  if (span.from !== undefined && span.to !== undefined) {
    return `between ${STAMP.format(new Date(span.from))} and ${STAMP.format(new Date(span.to))}`;
  }
  return `in the ${span.label.toLowerCase()}`;
}

/** "hourly", "every 4 hours" — what a chart's own heading should say. */
export function bucketWords(minutes: number): string {
  if (minutes < 60) return `every ${minutes} minute${minutes === 1 ? "" : "s"}`;
  if (minutes === 60) return "hourly";
  if (minutes === 1_440) return "daily";
  if (minutes % 1_440 === 0) return `every ${minutes / 1_440} days`;
  return `every ${minutes / 60} hours`;
}
