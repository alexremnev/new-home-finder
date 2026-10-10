import { FURNISHED, PROPERTY_TYPES, type Criteria } from "./criteria";

/**
 * The filter form, read backwards: a stored `Criteria` turned into the values
 * its controls start on.
 *
 * Every bound of every control lives here rather than in the form, because the
 * two readings have to agree. A rent floor the form draws at £400 and the
 * prefill treated as £0 would reopen a saved filter wider than it was saved,
 * and the person would never see which end moved.
 *
 * The rule throughout: an absent field means the control was left at its end,
 * so it goes back to that end. That is what `parseForm` means by omitting it —
 * a slider at its ceiling is "and above", not "at most five".
 */

export const RENT_MIN = 400;
export const RENT_MAX = 10_000;
export const RENT_STEP = 100;

// Bedrooms start at nought because a studio is a real thing to search for.
// Bathrooms do not: no flat is let with none.
export const BEDS_MIN = 0;
export const BATHS_MIN = 1;
export const ROOMS_MAX = 5;

// How many days either side of the desired date a listing may be available.
export const DAY_WINDOW = 10;
export const DAY_WINDOW_MAX = 90;

// The size slider is in square metres, and so is everything the person reads.
// The database keeps square feet, because that is the unit British listings
// quote — so the conversion happens twice, both times here: on the way out of
// the form (`asSqft`, used where it is submitted) and on the way back in.
//
// ── why the steps are uneven ─────────────────────────────────────────────
//
// The interesting part of this scale is the bottom. A studio is about 30 m² and
// a two-bed about 70; the difference between 240 and 260 decides nothing. An
// even step fine enough for the bottom would make the handle crawl across the
// top, and one coarse enough for the top cannot tell a studio from a one-bed.
//
// So: 5 m² up to 100, then 10 to 200, then 20 to 300. Each stop gets the same
// travel, which puts the precision where the pointing is hard.
export const AREA_STOPS = [
  ...Array.from({ length: 21 }, (_, i) => i * 5),        // 0 … 100
  ...Array.from({ length: 10 }, (_, i) => 110 + i * 10), // 110 … 200
  ...Array.from({ length: 5 }, (_, i) => 220 + i * 20),  // 220 … 300
];
export const AREA_MIN = AREA_STOPS[0]!;
export const AREA_MAX = AREA_STOPS[AREA_STOPS.length - 1]!;

// 10.7639 square feet to the square metre.
export const SQFT_PER_SQM = 10.7639;
export const asSqft = (sqm: number) => Math.round(sqm * SQFT_PER_SQM);
export const asSqm = (sqft: number) => sqft / SQFT_PER_SQM;

const DAY = 86_400_000;
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export type Fields = {
  /** Districts to show as chips, in the order they were stored. */
  districts: string[];
  /**
   * Stored districts that are no longer covered, left out of `districts`.
   *
   * Kept separate rather than dropped quietly: saving the form replaces the
   * filter, so an area that silently vanished here would vanish from their
   * search as well, and the first they would know of it is the alerts stopping.
   */
  dropped: string[];
  rent: [number, number];
  bedrooms: [number, number];
  bathrooms: [number, number];
  /** Square metres, which is what the slider is in. */
  size: [number, number];
  types: string[];
  furnished: string[];
  pets: boolean;
  /**
   * The duplicates toggle, which reads the stored field backwards: it is on
   * when the same flat on a second portal should NOT be sent. On is the
   * default, so a filter with nothing stored opens with it on.
   */
  oneAlertPerFlat: boolean;
  /** The middle of the available-date window, or "" for any date. */
  availableOn: string;
  dayWindow: number;
};

/** Every control at its end: what a first-time visitor sees. */
export function blank(): Fields {
  return {
    districts: [],
    dropped: [],
    rent: [RENT_MIN, RENT_MAX],
    bedrooms: [BEDS_MIN, ROOMS_MAX],
    bathrooms: [BATHS_MIN, ROOMS_MAX],
    size: [AREA_MIN, AREA_MAX],
    types: [],
    furnished: [],
    pets: false,
    oneAlertPerFlat: true,
    availableOn: "",
    dayWindow: DAY_WINDOW,
  };
}

export function prefill(
  criteria: Criteria | null | undefined,
  covered: string[] = [],
): Fields {
  if (!criteria) return blank();

  const allowed = new Set(covered.map((code) => code.trim().toUpperCase()));
  const stored = [
    ...new Set(
      (criteria.areas?.postcode_districts ?? [])
        .map((code) => String(code).trim().toUpperCase())
        .filter(Boolean),
    ),
  ];

  const { availableOn, dayWindow } = dateWindow(criteria.available_from);

  return {
    districts: stored.filter((code) => allowed.has(code)),
    dropped: stored.filter((code) => !allowed.has(code)),
    rent: band(criteria.price_pcm, RENT_MIN, RENT_MAX, RENT_STEP),
    bedrooms: band(criteria.bedrooms, BEDS_MIN, ROOMS_MAX, 1),
    bathrooms: band(criteria.bathrooms, BATHS_MIN, ROOMS_MAX, 1),
    size: metres(criteria.floor_area_sqft),
    types: allowedOnly(criteria.property_types, PROPERTY_TYPES),
    furnished: allowedOnly(criteria.furnished, FURNISHED),
    pets: criteria.pets_allowed === true,
    oneAlertPerFlat: criteria.send_duplicates !== true,
    availableOn,
    dayWindow,
  };
}

const clamp = (value: number, low: number, high: number) =>
  Math.min(high, Math.max(low, value));

/**
 * One slider's two handles.
 *
 * A stored number off the slider's step can only have been hand-written, and it
 * is rounded outwards — the floor down, the ceiling up — so that reopening a
 * filter never quietly narrows it. Rounding £450 up to £500 would hide the
 * £460 flats somebody was already being sent.
 */
function band(
  value: { min?: number; max?: number } | undefined,
  low: number,
  high: number,
  step: number,
): [number, number] {
  const min =
    value?.min === undefined
      ? low
      : clamp(Math.floor(value.min / step) * step, low, high);
  const max =
    value?.max === undefined
      ? high
      : clamp(Math.ceil(value.max / step) * step, low, high);
  return min <= max ? [min, max] : [max, min];
}

/** Square feet from the database onto a slider marked in metres. */
function metres(value: { min?: number; max?: number } | undefined): [number, number] {
  const min = value?.min === undefined ? AREA_MIN : stop(asSqm(value.min), "down");
  const max = value?.max === undefined ? AREA_MAX : stop(asSqm(value.max), "up");
  return min <= max ? [min, max] : [max, min];
}

// The nearest stop that does not move the handle inwards. The conversion back
// from feet lands a fraction either side of the stop it came from, so "nearest"
// alone would sometimes drop 30 m² to 25.
function stop(value: number, direction: "down" | "up"): number {
  const within = clamp(value, AREA_MIN, AREA_MAX);
  // A hair of tolerance, so 30.0078 — which is 323 ft² read back — counts as
  // the 30 it was rather than as something above it.
  const near = AREA_STOPS.find((one) => Math.abs(one - within) < 0.5);
  if (near !== undefined) return near;

  return direction === "down"
    ? [...AREA_STOPS].reverse().find((one) => one <= within) ?? AREA_MIN
    : AREA_STOPS.find((one) => one >= within) ?? AREA_MAX;
}

/**
 * The date field, from the two dates a date and a window were saved as.
 *
 * The form only ever writes both ends, so the usual case is the midpoint and
 * half the span. One end on its own cannot have come from here — the form has
 * no control for "from March onwards" — so it is read as that date with the
 * default window rather than as an exact day nobody asked for.
 */
function dateWindow(value: Criteria["available_from"]): {
  availableOn: string;
  dayWindow: number;
} {
  const after = ISO_DAY.test(value?.after ?? "") ? (value?.after as string) : undefined;
  const before = ISO_DAY.test(value?.before ?? "") ? (value?.before as string) : undefined;

  if (!after && !before) return { availableOn: "", dayWindow: DAY_WINDOW };
  if (!after || !before) {
    return { availableOn: (after ?? before) as string, dayWindow: DAY_WINDOW };
  }

  const from = Date.parse(`${after}T00:00:00Z`);
  const to = Date.parse(`${before}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to) || to < from) {
    return { availableOn: after, dayWindow: DAY_WINDOW };
  }

  const days = clamp(Math.round((to - from) / DAY / 2), 0, DAY_WINDOW_MAX);
  const middle = new Date(from + Math.round((to - from) / 2));
  return { availableOn: middle.toISOString().slice(0, 10), dayWindow: days };
}

function allowedOnly(value: string[] | undefined, allowed: readonly string[]): string[] {
  const chosen = (value ?? []).map((one) => String(one).toLowerCase());
  return [...new Set(chosen.filter((one) => allowed.includes(one)))];
}
