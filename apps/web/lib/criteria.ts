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

export const MAX_ALERTS_LIMIT = 50;
const PRICE_LIMIT = 20_000;
const BEDROOM_LIMIT = 10;
const TENANCY_LIMIT = 60;

export type Parsed = { criteria: Criteria; maxAlertsPerDay: number };

export class InvalidForm extends Error {}

export function parseForm(form: Record<string, unknown>, enabledDistricts: string[]): Parsed {
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

  return {
    criteria,
    maxAlertsPerDay: integer(form.max_alerts_per_day, 1, MAX_ALERTS_LIMIT) ?? 10,
  };
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
