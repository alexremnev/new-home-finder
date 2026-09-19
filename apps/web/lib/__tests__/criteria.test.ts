import { describe, expect, it } from "vitest";

import { InvalidForm, parseForm } from "../criteria";

const ENABLED = ["E14", "SE16", "SE8"];

describe("an absent field stays absent", () => {

  it("produces an empty object from an empty form", () => {
    expect(parseForm({}, ENABLED)).toEqual({});
  });

  it("keeps a one-sided range one-sided", () => {
    expect(parseForm({ price_max: "2000" }, ENABLED).price_pcm).toEqual({ max: 2000 });
  });

  it("treats a blank string as nothing, not as zero", () => {
    expect(parseForm({ price_min: "", bedrooms_max: "" }, ENABLED)).toEqual({});
  });
});

describe("checkboxes", () => {
  it("only a ticked box becomes a criterion", () => {

    expect(parseForm({}, ENABLED).pets_allowed).toBeUndefined();
    expect(parseForm({ pets_allowed: "on" }, ENABLED).pets_allowed).toBe(true);
  });

  it("accepts a single value and a group alike", () => {
    expect(parseForm({ furnished: "furnished" }, ENABLED).furnished).toEqual(["furnished"]);
    expect(
      parseForm({ furnished: ["furnished", "part"] }, ENABLED).furnished,
    ).toEqual(["furnished", "part"]);
  });

  it("drops a value that is not in the vocabulary", () => {

    expect(parseForm({ furnished: ["furnished", "velvet"] }, ENABLED).furnished)
      .toEqual(["furnished"]);
  });

  it("ignores a criterion the form does not collect", () => {

    // The form asks for areas, rent, bedrooms, bathrooms, a date, furnishing and
    // pets. Anything else reaching this route was hand-crafted, and accepting it
    // would put a filter in the database that nobody can see or change.
    const criteria = parseForm(
      {
        districts: ["SE16"],
        property_types: ["flat"],
        bills_included: "on",
        landlord_direct_only: "on",
        min_tenancy_max_months: "12",
      },
      ENABLED,
    );
    expect(Object.keys(criteria)).toEqual(["areas"]);
  });

  it("de-duplicates", () => {
    expect(parseForm({ districts: ["SE16", "se16"] }, ENABLED).areas)
      .toEqual({ postcode_districts: ["SE16"] });
  });
});

describe("districts", () => {
  it("normalises case", () => {
    expect(parseForm({ districts: "se16" }, ENABLED).areas)
      .toEqual({ postcode_districts: ["SE16"] });
  });

  it("refuses a district nothing is collected for", () => {

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

  it("takes a range for rooms, not only a floor", () => {
    // Both sliders have two handles now, so both ends arrive.
    const criteria = parseForm(
      { bedrooms_min: "2", bedrooms_max: "3", bathrooms_min: "1", bathrooms_max: "2" },
      ENABLED,
    );
    expect(criteria.bedrooms).toEqual({ min: 2, max: 3 });
    expect(criteria.bathrooms).toEqual({ min: 1, max: 2 });
  });

  it("leaves the top open when only a floor is given", () => {
    // What a slider parked at its ceiling sends: no maximum at all, so a
    // six-bedroom house is not filtered out.
    expect(parseForm({ bedrooms_min: "2" }, ENABLED).bedrooms).toEqual({ min: 2 });
    expect(parseForm({ bathrooms_max: "2" }, ENABLED).bathrooms).toEqual({ max: 2 });
  });

  it("refuses a room range that is inside out", () => {
    expect(() => parseForm({ bedrooms_min: "4", bedrooms_max: "2" }, ENABLED)).toThrow(
      InvalidForm,
    );
  });


  it("allows a studio, which is zero and not missing", () => {
    expect(parseForm({ bedrooms_min: "0", bedrooms_max: "0" }, ENABLED).bedrooms)
      .toEqual({ min: 0, max: 0 });
  });
});

describe("there is no daily cap", () => {
  it("ignores a cap someone tries to send", () => {

    expect(parseForm({ max_alerts_per_day: "3" }, ENABLED)).toEqual({});
  });
});
