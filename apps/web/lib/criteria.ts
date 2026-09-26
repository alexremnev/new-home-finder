function day(value: unknown): string | undefined {
  const text = String(value ?? "").trim();
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text : undefined;
}

export type Criteria = {
  price_pcm?: { min?: number; max?: number };
  property_types?: string[];
  bedrooms?: { min?: number; max?: number };

  bathrooms?: { min?: number; max?: number };
  areas?: { postcode_districts?: string[] };
  furnished?: string[];
  pets_allowed?: boolean;
  available_from?: { after?: string; before?: string };
};

export const FURNISHED = ["furnished", "unfurnished", "part"] as const;

// Three words, matching what the parsers store. The matcher compares the stored
// type against this as an exact string, so the two lists have to be the same
// list — anything finer ("terraced house") would answer no filter at all.
//
// No "studio": a studio is a flat with no separate bedroom, so it is stored as
// a flat with nought bedrooms and is asked for with the bedroom slider. As its
// own type it was worse than redundant — a studio hunter ticking Flat was shown
// no studios, because the stored type did not say "flat".
//
// Worth knowing what this can and cannot do: the Telegram feed only states a
// type for studios and rooms, and an unstated type matches everything. So
// choosing flat and house excludes rooms, which is the useful direction;
// choosing flat alone does not exclude a house that never said it was one.
export const PROPERTY_TYPES = ["flat", "house", "room"] as const;

const PRICE_LIMIT = 20_000;
const BEDROOM_LIMIT = 10;

export class InvalidForm extends Error {}

export type Limits = { maxDistricts: number };

export function enforceLimits(criteria: Criteria, limits: Limits): Criteria {
  const districts = criteria.areas?.postcode_districts ?? [];
  if (districts.length === 0) {
    // Without this, no areas means every area: the matcher treats an absent
    // filter as "anything matches", so an empty form would sign somebody up to
    // every listing in London.
    throw new InvalidForm("choose at least one area to watch");
  }
  if (districts.length > limits.maxDistricts) {
    throw new InvalidForm(
      `your plan covers ${limits.maxDistricts} ` +
        `district${limits.maxDistricts === 1 ? "" : "s"}, and you chose ${districts.length}`,
    );
  }
  return criteria;
}

export function parseForm(form: Record<string, unknown>, enabledDistricts: string[]): Criteria {
  const criteria: Criteria = {};

  const price = range(form.price_min, form.price_max, PRICE_LIMIT);
  if (price) criteria.price_pcm = price;

  const bedrooms = range(form.bedrooms_min, form.bedrooms_max, BEDROOM_LIMIT);
  if (bedrooms) criteria.bedrooms = bedrooms;
  const bathrooms = range(form.bathrooms_min, form.bathrooms_max, BEDROOM_LIMIT);
  if (bathrooms) criteria.bathrooms = bathrooms;

  const after = day(form.available_after);
  const before = day(form.available_before);
  if (after || before) {
    criteria.available_from = { ...(after && { after }), ...(before && { before }) };
  }

  const furnished = subset(form.furnished, FURNISHED);
  if (furnished.length) criteria.furnished = furnished;

  const types = subset(form.property_types, PROPERTY_TYPES);
  if (types.length) criteria.property_types = types;

  const districts = districtList(form.districts, enabledDistricts);
  if (districts.length) criteria.areas = { postcode_districts: districts };

  if (form.pets_allowed === true || form.pets_allowed === "on") criteria.pets_allowed = true;

  return criteria;
}

function range(
  low: unknown,
  high: unknown,
  ceiling: number,
): { min?: number; max?: number } | undefined {
  const min = integer(low, 0, ceiling);
  const max = integer(high, 0, ceiling);
  if (min === undefined && max === undefined) return undefined;
  if (min !== undefined && max !== undefined && min > max) {
    throw new InvalidForm("the minimum is above the maximum");
  }
  return { ...(min !== undefined && { min }), ...(max !== undefined && { max }) };
}

function integer(value: unknown, low: number, high: number): number | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return undefined;
  const rounded = Math.round(parsed);
  if (rounded < low || rounded > high) throw new InvalidForm(`${value} is out of range`);
  return rounded;
}

function subset(value: unknown, allowed: readonly string[]): string[] {
  const list = Array.isArray(value) ? value : value === undefined ? [] : [value];
  const chosen = list.map((v) => String(v).toLowerCase()).filter((v) => allowed.includes(v));
  return [...new Set(chosen)];
}

function districtList(value: unknown, enabled: string[]): string[] {
  const list = Array.isArray(value) ? value : value === undefined ? [] : [value];
  const wanted = [...new Set(list.map((v) => String(v).trim().toUpperCase()).filter(Boolean))];
  const unknown = wanted.filter((code) => !enabled.includes(code));
  if (unknown.length) {

    throw new InvalidForm(`not covered yet: ${unknown.join(", ")}`);
  }
  return wanted;
}

export function describeCriteria(criteria: Criteria): string {

  const districts = criteria.areas?.postcode_districts ?? [];
  const lines = [`Districts: ${districts.join(", ") || "none"}`];
  if (criteria.price_pcm) lines.push(`Rent: ${rangeText(criteria.price_pcm, "£")}`);
  if (criteria.bedrooms) lines.push(`Bedrooms: ${rangeText(criteria.bedrooms, "")}`);
  if (criteria.bathrooms) lines.push(`Bathrooms: ${rangeText(criteria.bathrooms, "")}`);
  if (criteria.property_types?.length) {
    lines.push(`Type: ${criteria.property_types.join(", ")}`);
  }
  if (criteria.furnished?.length) {
    lines.push(`Furnishing: ${criteria.furnished.join(", ")}`);
  }
  if (criteria.available_from) {
    const { after, before } = criteria.available_from;
    lines.push(`Available: ${[after && `from ${after}`, before && `to ${before}`].filter(Boolean).join(" ")}`);
  }
  if (criteria.pets_allowed) lines.push("Must state: pets allowed");
  return lines.join("\n");
}

function rangeText(value: { min?: number; max?: number } | undefined, unit: string): string {
  if (!value || (value.min === undefined && value.max === undefined)) return "any";
  if (value.min !== undefined && value.max !== undefined) {
    return `${unit}${value.min}\u2013${unit}${value.max}`;
  }
  if (value.max !== undefined) return `up to ${unit}${value.max}`;
  return `from ${unit}${value.min}`;
}
