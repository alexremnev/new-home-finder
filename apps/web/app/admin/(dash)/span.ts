// The one time range the whole dashboard reads.
//
// Every page used to carry its own picker with its own options — the System tab
// had six rolling windows, Visitors had three day counts, Districts had ten
// calendar ranges of its own. Comparing a spike in visits against a spike in
// intake therefore meant holding two different meanings of "7d" in your head.
// So there is one list, one parameter (`?w=`), and one resolver.
//
// ── two kinds of range in one list ───────────────────────────────────────
//
// `today` and `yesterday` are calendar days in London. Everything else is a
// rolling window ending now. They are genuinely different questions — "what has
// happened since midnight" is not "the last 24 hours" — and both get asked, so
// both are offered.
//
// ── how a span reaches a query ───────────────────────────────────────────
//
// Two ways, because the tables are keyed two ways:
//
//   * `mins` / `endMins` — minutes before now, for the timestamp columns.
//     `endMins` is 0 for everything that ends now, which is everything except
//     `yesterday`.
//   * `fromDay` / `toDay` — inclusive London dates, for the tables keyed on a
//     DATE: `site_visits.day` and `district_days.day`.

export type SpanKey =
  | "today" | "yesterday"
  | "1h" | "2h" | "3h" | "6h" | "12h"
  | "1d" | "2d" | "3d" | "4d" | "5d" | "6d"
  | "1w" | "2w" | "1m";

type Shape =
  // Whole London days, counting back from today. `back: 0` is today.
  | { kind: "day"; back: number }
  // A window of this many hours, ending now.
  | { kind: "rolling"; hours: number };

const SHAPES: { key: SpanKey; label: string; shape: Shape }[] = [
  { key: "today", label: "Today", shape: { kind: "day", back: 0 } },
  { key: "yesterday", label: "Yesterday", shape: { kind: "day", back: 1 } },
  { key: "1h", label: "1h", shape: { kind: "rolling", hours: 1 } },
  { key: "2h", label: "2h", shape: { kind: "rolling", hours: 2 } },
  { key: "3h", label: "3h", shape: { kind: "rolling", hours: 3 } },
  { key: "6h", label: "6h", shape: { kind: "rolling", hours: 6 } },
  { key: "12h", label: "12h", shape: { kind: "rolling", hours: 12 } },
  { key: "1d", label: "1d", shape: { kind: "rolling", hours: 24 } },
  { key: "2d", label: "2d", shape: { kind: "rolling", hours: 48 } },
  { key: "3d", label: "3d", shape: { kind: "rolling", hours: 72 } },
  { key: "4d", label: "4d", shape: { kind: "rolling", hours: 96 } },
  { key: "5d", label: "5d", shape: { kind: "rolling", hours: 120 } },
  { key: "6d", label: "6d", shape: { kind: "rolling", hours: 144 } },
  { key: "1w", label: "1w", shape: { kind: "rolling", hours: 168 } },
  { key: "2w", label: "2w", shape: { kind: "rolling", hours: 336 } },
  { key: "1m", label: "1m", shape: { kind: "rolling", hours: 720 } },
];

export const SPAN_KEYS: SpanKey[] = SHAPES.map((one) => one.key);
export const SPAN_LABELS: { key: SpanKey; label: string }[] = SHAPES.map(
  ({ key, label }) => ({ key, label }),
);

// Today, not a week. A dashboard answers "what is happening" first and "what
// has been happening" second.
export const DEFAULT_SPAN: SpanKey = "today";

export type Span = {
  key: SpanKey;
  label: string;
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
};

const LONDON = "Europe/London";

const CLOCK = new Intl.DateTimeFormat("en-GB", {
  timeZone: LONDON,
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: LONDON,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

/** Minutes since London midnight. */
function sinceMidnight(at: Date): number {
  const parts = CLOCK.formatToParts(at);
  const value = (type: string) =>
    Number(parts.find((one) => one.type === type)?.value ?? 0);
  // "24" rather than "00" is what en-GB hour12:false gives at midnight.
  return (value("hour") % 24) * 60 + value("minute");
}

/** The London date `back` days ago, as YYYY-MM-DD. */
function dayBack(at: Date, back: number): string {
  return DAY.format(new Date(at.getTime() - back * 86_400_000));
}

export function spanFrom(value: string | undefined, now: Date = new Date()): Span {
  const found = SHAPES.find((one) => one.key === value) ?? SHAPES[0]!;
  const today = sinceMidnight(now);

  if (found.shape.kind === "day") {
    const back = found.shape.back;
    // A whole London day that is not today ends at the following midnight.
    //
    // Measured as 1440 minutes per day rather than by the exact instants: on
    // the two days a year the clocks change, "yesterday" is then an hour out on
    // the timestamp tables. The date-keyed tables are exact either way, because
    // they are asked by date.
    const endMins = back === 0 ? 0 : today + (back - 1) * 1_440;
    const mins = today + back * 1_440;
    const day = dayBack(now, back);
    return {
      key: found.key,
      label: found.label,
      mins,
      endMins,
      fromDay: day,
      toDay: day,
      days: 1,
      hours: Math.max(1, (mins - endMins) / 60),
    };
  }

  const hours = found.shape.hours;
  const days = Math.max(1, Math.ceil(hours / 24));
  return {
    key: found.key,
    label: found.label,
    mins: hours * 60,
    endMins: 0,
    // Whole days, because a date-keyed table cannot answer a part of one. A
    // 1h window therefore reads today's row, which is the nearest true answer.
    fromDay: dayBack(now, days - 1),
    toDay: dayBack(now, 0),
    days,
    hours,
  };
}

// How wide a bucket keeps a chart readable at each width: about 30-60 points
// across, so a line has shape without turning into noise.
export function bucketMinutes(hours: number): number {
  if (hours <= 1) return 2;
  if (hours <= 3) return 5;
  if (hours <= 6) return 10;
  if (hours <= 12) return 15;
  if (hours <= 24) return 30;
  if (hours <= 72) return 60;
  if (hours <= 168) return 180;
  return 720;
}

/**
 * The range as it reads inside a sentence: "today", "yesterday", "in the last
 * 1w". Needed because the chip labels are not sentence fragments — prose built
 * from them said "last Today".
 */
export function spanWords(span: { key: SpanKey; label: string }): string {
  if (span.key === "today") return "today";
  if (span.key === "yesterday") return "yesterday";
  return `in the last ${span.label}`;
}

/** "hourly", "every 4 hours" — what a chart's own heading should say. */
export function bucketWords(minutes: number): string {
  if (minutes < 60) return `every ${minutes} minutes`;
  if (minutes === 60) return "hourly";
  if (minutes === 1_440) return "daily";
  if (minutes % 1_440 === 0) return `every ${minutes / 1_440} days`;
  return `every ${minutes / 60} hours`;
}
