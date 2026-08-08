import { describe, expect, it } from "vitest";

import { InvalidForm, parseForm } from "../criteria";

const ENABLED = ["E14", "SE16", "SE8"];

describe("an absent field stays absent", () => {
  // The matcher reads a present criterion as a requirement, so a default invented
  // here silently narrows someone's search.
  it("produces an empty object from an empty form", () => {
    expect(parseForm({}, ENABLED).criteria).toEqual({});
  });

  it("keeps a one-sided range one-sided", () => {
    expect(parseForm({ price_max: "2000" }, ENABLED).criteria.price_pcm).toEqual({ max: 2000 });
  });

  it("treats a blank string as nothing, not as zero", () => {
    expect(parseForm({ price_min: "", bedrooms_max: "" }, ENABLED).criteria).toEqual({});
  });
});

describe("checkboxes", () => {
  it("only a ticked box becomes a criterion", () => {
    // An unticked "pets allowed" means "I don't mind", not "listings that forbid pets".
    expect(parseForm({}, ENABLED).criteria.pets_allowed).toBeUndefined();
    expect(parseForm({ pets_allowed: "on" }, ENABLED).criteria.pets_allowed).toBe(true);
  });

  it("accepts a single value and a group alike", () => {
    expect(parseForm({ property_types: "flat" }, ENABLED).criteria.property_types).toEqual(["flat"]);
    expect(
      parseForm({ property_types: ["flat", "house"] }, ENABLED).criteria.property_types,
    ).toEqual(["flat", "house"]);
  });

  it("drops a value that is not in the vocabulary", () => {
    // The worker's matcher compares against its own list; a value it has never
    // heard of would match nothing and look like a bug in matching.
    expect(parseForm({ property_types: ["flat", "castle"] }, ENABLED).criteria.property_types)
      .toEqual(["flat"]);
  });

  it("de-duplicates", () => {
    expect(parseForm({ districts: ["SE16", "se16"] }, ENABLED).criteria.areas)
      .toEqual({ postcode_districts: ["SE16"] });
  });
});

describe("districts", () => {
  it("normalises case", () => {
    expect(parseForm({ districts: "se16" }, ENABLED).criteria.areas)
      .toEqual({ postcode_districts: ["SE16"] });
  });

  it("refuses a district nothing is collected for", () => {
    // Accepting it would create a subscription that waits for ever, and the
    // person could not tell that apart from a quiet market.
    expect(() => parseForm({ districts: ["SE16", "W1"] }, ENABLED)).toThrow(InvalidForm);
  });
});

describe("ranges", () => {
  it("rejects a minimum above the maximum", () => {
    expect(() => parseForm({ price_min: "3000", price_max: "1000" }, ENABLED)).toThrow(InvalidForm);
  });

  it("rejects a value outside the plausible range", () => {
    expect(() => parseForm({ price_max: "9999999" }, ENABLED)).toThrow(InvalidForm);
    expect(() => parseForm({ bedrooms_max: "99" }, ENABLED)).toThrow(InvalidForm);
  });

  it("allows a studio, which is zero and not missing", () => {
    expect(parseForm({ bedrooms_min: "0", bedrooms_max: "0" }, ENABLED).criteria.bedrooms)
      .toEqual({ min: 0, max: 0 });
  });
});

describe("the daily cap", () => {
  it("defaults to ten", () => {
    expect(parseForm({}, ENABLED).maxAlertsPerDay).toBe(10);
  });

  it("is bounded, so a subscription cannot ask for a thousand messages", () => {
    expect(() => parseForm({ max_alerts_per_day: "1000" }, ENABLED)).toThrow(InvalidForm);
    expect(() => parseForm({ max_alerts_per_day: "0" }, ENABLED)).toThrow(InvalidForm);
  });
});
