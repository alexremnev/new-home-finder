// The form, turned into the criteria object the matcher reads.
//
// This file is the boundary between what a person typed and what the worker
// trusts. Two rules follow from that:
//
//   * a field that is absent, blank, or unparseable is left out of the object
//     rather than defaulted. The matcher treats an absent criterion as "no
//     preference" and a present one as a requirement, so inventing a value here
//     silently narrows someone's search;
//   * districts are checked against the ones actually enabled. A criterion naming
//     a district nothing collects for would match nothing for ever, and the
//     person would wait for alerts that cannot arrive.
//
// The shape must stay in step with worker/pipeline/match.py. That coupling is
// deliberate — one vocabulary, checked by the worker's tests.

export type Criteria = {
  price_pcm?: { min?: number; max?: number };
  bedrooms?: { min?: number; max?: number };
  property_types?: string[];
  areas?: { postcode_districts?: string[] };
  furnished?: string[];
  pets_allowed?: boolean;
  bills_included?: boolean;
  min_tenancy_max_months?: number;
  landlord_direct_only?: boolean;
};

export const PROPERTY_TYPES = ["flat", "house", "studio", "room", "maisonette"] as const;
export const FURNISHED = ["furnished", "unfurnished", "part"] as const;

const PRICE_LIMIT = 20_000;
const BEDROOM_LIMIT = 10;
const TENANCY_LIMIT = 60;

export class InvalidForm extends Error {}

/** What the person's plan allows. Read from the database, never assumed here. */
export type Limits = { maxDistricts: number };

/**
 * The one place a plan limit is applied to a filter.
 *
 * Both the form and the bot commands go through this, so there is no path that
 * can produce a subscription covering more districts than was paid for. Refusing
 * with the number named is deliberate: silently keeping the first N would hand
 * back a filter that is not the one they asked for.
 */
export function enforceLimits(criteria: Criteria, limits: Limits): Criteria {
  const districts = criteria.areas?.postcode_districts ?? [];
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

  const types = subset(form.property_types, PROPERTY_TYPES);
  if (types.length) criteria.property_types = types;

  const furnished = subset(form.furnished, FURNISHED);
  if (furnished.length) criteria.furnished = furnished;

  const districts = districtList(form.districts, enabledDistricts);
  if (districts.length) criteria.areas = { postcode_districts: districts };

  // Only a checked box becomes a criterion. An unchecked "pets allowed" means
  // "I don't mind", not "I want listings that forbid pets".
  if (form.pets_allowed === true || form.pets_allowed === "on") criteria.pets_allowed = true;
  if (form.bills_included === true || form.bills_included === "on") criteria.bills_included = true;
  if (form.landlord_direct_only === true || form.landlord_direct_only === "on") {
    criteria.landlord_direct_only = true;
  }

  const tenancy = integer(form.min_tenancy_max_months, 1, TENANCY_LIMIT);
  if (tenancy !== undefined) criteria.min_tenancy_max_months = tenancy;

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
    // Named rather than dropped: silently ignoring a district produces a
    // subscription that quietly covers more than was asked for.
    throw new InvalidForm(`not covered yet: ${unknown.join(", ")}`);
  }
  return wanted;
}


// ── editing an existing filter ────────────────────────────────────────────

export type Patch =
  | { field: "price" | "bedrooms"; min?: number; max?: number }
  | { field: "areas"; districts: string[] }
  | { field: "pets_allowed" | "bills_included" | "landlord_direct_only"; on: boolean };

/**
 * Apply one change to an existing filter.
 *
 * Turning a flag off removes the criterion rather than setting it to false. The
 * matcher reads `pets_allowed: false` as "listings that say pets are NOT allowed",
 * which is a filter almost nobody wants and not what "off" means to the person
 * typing it.
 */
export function applyPatch(
  current: Criteria,
  patch: Patch,
  enabledDistricts: string[],
): Criteria {
  const criteria: Criteria = structuredClone(current);

  switch (patch.field) {
    case "price":
    case "bedrooms": {
      const ceiling = patch.field === "price" ? PRICE_LIMIT : BEDROOM_LIMIT;
      const range = boundedRange(patch.min, patch.max, ceiling);
      if (range === undefined) delete criteria[patch.field === "price" ? "price_pcm" : "bedrooms"];
      else if (patch.field === "price") criteria.price_pcm = range;
      else criteria.bedrooms = range;
      break;
    }
    case "areas": {
      const districts = districtList(patch.districts, enabledDistricts);
      if (!districts.length) throw new InvalidForm("name at least one district");
      criteria.areas = { postcode_districts: districts };
      break;
    }
    default: {
      if (patch.on) criteria[patch.field] = true;
      else delete criteria[patch.field];
    }
  }

  return criteria;
}

function boundedRange(
  min: number | undefined,
  max: number | undefined,
  ceiling: number,
): { min?: number; max?: number } | undefined {
  const low = integer(min, 0, ceiling);
  const high = integer(max, 0, ceiling);
  if (low === undefined && high === undefined) return undefined;
  if (low !== undefined && high !== undefined && low > high) {
    throw new InvalidForm("the minimum is above the maximum");
  }
  return { ...(low !== undefined && { min: low }), ...(high !== undefined && { max: high }) };
}

/** The filter in words, for /show. Says "any" rather than leaving a line out. */
export function describeCriteria(criteria: Criteria): string {
  const lines = [
    `Districts: ${(criteria.areas?.postcode_districts ?? []).join(", ") || "any"}`,
    `Rent: ${rangeText(criteria.price_pcm, "£")}`,
    `Bedrooms: ${rangeText(criteria.bedrooms, "")}`,
    `Type: ${(criteria.property_types ?? []).join(", ") || "any"}`,
    `Furnishing: ${(criteria.furnished ?? []).join(", ") || "any"}`,
  ];
  const musts = [
    criteria.pets_allowed && "pets allowed",
    criteria.bills_included && "bills included",
    criteria.landlord_direct_only && "landlord direct",
  ].filter(Boolean);
  if (musts.length) lines.push(`Must state: ${musts.join(", ")}`);
  if (criteria.min_tenancy_max_months !== undefined) {
    lines.push(`Minimum tenancy at most: ${criteria.min_tenancy_max_months} months`);
  }
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
