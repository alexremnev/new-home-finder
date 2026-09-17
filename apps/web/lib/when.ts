// Every time the admin shows is London time.
//
// Not by setting the session timezone: a transaction pooler hands out a
// different backend per transaction, so `SET TIME ZONE` does not survive. The
// conversion is therefore explicit — chart labels in SQL, everything else here.
//
// Timestamps arrive as `2026-09-17 10:39:58.73+00`, offset included, so the
// arithmetic below is right whatever the server's own clock is set to.
const LONDON = "Europe/London";

function parse(stamp: string | null | undefined): Date | null {
  if (!stamp) return null;

  // Postgres writes the offset as `+00` or `+01`; ISO 8601 — and therefore
  // Date.parse — wants `+00:00`. Without this every timestamp from the database
  // parsed as Invalid Date and the whole admin showed em dashes.
  const iso = stamp
    .replace(" ", "T")
    .replace(/([+-]\d{2})$/, "$1:00")
    .replace(/([+-]\d{2})(\d{2})$/, "$1:$2");

  const when = new Date(iso);
  return Number.isNaN(when.getTime()) ? null : when;
}

const shape = (options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en-GB", { timeZone: LONDON, ...options });

const HHMM = shape({ hour: "2-digit", minute: "2-digit", hour12: false });
const DAY_MONTH = shape({ day: "numeric", month: "short" });
const FULL_DAY = shape({ day: "numeric", month: "short", year: "numeric" });
const DAY_TIME = shape({
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
});
const WITH_SECONDS = shape({
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  second: "2-digit", hour12: false,
});

/** "11:39" */
export const clockOf = (stamp: string | null): string => {
  const when = parse(stamp);
  return when ? HHMM.format(when) : "—";
};

/** "17 Sep 11:39" */
export const at = (stamp: string | null): string => {
  const when = parse(stamp);
  return when ? DAY_TIME.format(when).replace(",", "") : "—";
};

/** "17 Sep 11:39:58" */
export const atSecond = (stamp: string | null): string => {
  const when = parse(stamp);
  return when ? WITH_SECONDS.format(when).replace(",", "") : "—";
};

/** "17 Sep 2026" */
export const dayOf = (stamp: string | null): string => {
  const when = parse(stamp);
  return when ? FULL_DAY.format(when) : "—";
};

/** "17 Sep" */
export const shortDay = (stamp: string | null): string => {
  const when = parse(stamp);
  return when ? DAY_MONTH.format(when) : "—";
};

/** "just now", "12 min ago", "3h ago", "2d ago" — never in the future. */
export function ago(stamp: string | null): string {
  const when = parse(stamp);
  if (!when) return "never";
  const mins = (Date.now() - when.getTime()) / 60_000;
  if (mins < 1) return "just now";
  if (mins < 90) return `${Math.round(mins)} min ago`;
  if (mins < 60 * 36) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}

/** The same, compressed for a table cell: "12m", "3h", "2d". */
export function since(stamp: string | null): string {
  const when = parse(stamp);
  if (!when) return "never";
  const mins = (Date.now() - when.getTime()) / 60_000;
  if (mins < 90) return `${Math.max(1, Math.round(mins))}m`;
  if (mins < 60 * 36) return `${Math.round(mins / 60)}h`;
  return `${Math.round(mins / 1440)}d`;
}

/** Whether a promise made in hours has been broken. */
export const olderThan = (stamp: string | null, hours: number): boolean => {
  const when = parse(stamp);
  return when !== null && Date.now() - when.getTime() > hours * 3_600_000;
};

/**
 * How much of WhatsApp's 24-hour service window is left, or null when there is
 * none. Only an inbound message opens it — no API call can — so this is read
 * from the last one we received.
 */
export function windowLeft(lastInbound: string | null): string | null {
  const when = parse(lastInbound);
  if (!when) return null;
  const mins = 24 * 60 - (Date.now() - when.getTime()) / 60_000;
  if (mins <= 0) return null;
  if (mins < 60) return `${Math.round(mins)}m left`;
  return `${Math.floor(mins / 60)}h left`;
}
