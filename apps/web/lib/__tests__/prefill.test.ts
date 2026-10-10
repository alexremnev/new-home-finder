import { describe, expect, it } from "vitest";

import { parseForm, type Criteria } from "../criteria";
import {
  AREA_MAX,
  AREA_MIN,
  asSqft,
  BATHS_MIN,
  BEDS_MIN,
  blank,
  DAY_WINDOW,
  prefill,
  RENT_MAX,
  RENT_MIN,
  ROOMS_MAX,
} from "../prefill";

const COVERED = ["E14", "SE16", "SE8"];

describe("nothing saved", () => {
  it("opens every control at its end", () => {
    expect(prefill(null, COVERED)).toEqual(blank());
    expect(prefill(undefined, COVERED)).toEqual(blank());
  });

  // The account of somebody who used /stop and came back: there is a token and
  // a channel, so the page is the editing one, but there is no filter to draw.
  it("treats an empty filter as nothing saved", () => {
    expect(prefill({}, COVERED)).toEqual(blank());
  });
});

describe("an absent end means the handle was left there", () => {
  it("puts a one-sided rent back on one handle only", () => {
    expect(prefill({ price_pcm: { max: 2000 } }, COVERED).rent).toEqual([RENT_MIN, 2000]);
    expect(prefill({ price_pcm: { min: 1500 } }, COVERED).rent).toEqual([1500, RENT_MAX]);
  });

  it("keeps the room sliders' own floors", () => {
    const fields = prefill({ bedrooms: { min: 2 }, bathrooms: { max: 2 } }, COVERED);
    expect(fields.bedrooms).toEqual([2, ROOMS_MAX]);
    expect(fields.bathrooms).toEqual([BATHS_MIN, 2]);
  });

  it("reads a studio as the floor it is, not as nothing", () => {
    // Nought is a real answer here, and `|| BEDS_MIN` would have been the same
    // answer by accident.
    expect(prefill({ bedrooms: { min: 0, max: 0 } }, COVERED).bedrooms).toEqual([
      BEDS_MIN, 0,
    ]);
  });
});

describe("numbers nobody could have chosen with the sliders", () => {
  it("rounds outwards, so reopening never narrows the filter", () => {
    // £450 belongs to somebody who edited the json. Rounding it up to £500
    // would hide the £460 flats they were already being sent.
    expect(prefill({ price_pcm: { min: 450, max: 2050 } }, COVERED).rent).toEqual([
      400, 2100,
    ]);
  });

  it("clamps what is beyond the slider's reach", () => {
    expect(prefill({ price_pcm: { min: 50, max: 19_000 } }, COVERED).rent).toEqual([
      RENT_MIN, RENT_MAX,
    ]);
    expect(prefill({ bedrooms: { max: 9 } }, COVERED).bedrooms).toEqual([
      BEDS_MIN, ROOMS_MAX,
    ]);
  });

  it("puts a reversed range the right way round", () => {
    expect(prefill({ price_pcm: { min: 3000, max: 1000 } }, COVERED).rent).toEqual([
      1000, 3000,
    ]);
  });
});

describe("square feet back onto a slider marked in metres", () => {
  it("returns the stop the handle was on", () => {
    for (const sqm of [0, 30, 35, 70, 100, 110, 200, 220, 300]) {
      const fields = prefill({ floor_area_sqft: { min: asSqft(sqm) } }, COVERED);
      expect(fields.size[0]).toBe(sqm);
    }
  });

  it("does not move a handle inwards when the feet land between stops", () => {
    // 400 ft² is 37.2 m², which is not a stop. The floor goes down to 35 and
    // the ceiling up to 40: both ends widen rather than narrow.
    expect(prefill({ floor_area_sqft: { min: 400 } }, COVERED).size[0]).toBe(35);
    expect(prefill({ floor_area_sqft: { max: 400 } }, COVERED).size[1]).toBe(40);
  });

  it("leaves an untouched end at the end", () => {
    const fields = prefill({ floor_area_sqft: { min: asSqft(45) } }, COVERED);
    expect(fields.size).toEqual([45, AREA_MAX]);
    expect(prefill({}, COVERED).size).toEqual([AREA_MIN, AREA_MAX]);
  });
});

describe("the available date and its window", () => {
  it("reads the two stored dates as the day in the middle", () => {
    const fields = prefill(
      { available_from: { after: "2026-03-01", before: "2026-03-21" } },
      COVERED,
    );
    expect(fields.availableOn).toBe("2026-03-11");
    expect(fields.dayWindow).toBe(10);
  });

  it("reads an exact day as a window of nought", () => {
    const fields = prefill(
      { available_from: { after: "2026-03-11", before: "2026-03-11" } },
      COVERED,
    );
    expect(fields.availableOn).toBe("2026-03-11");
    expect(fields.dayWindow).toBe(0);
  });

  // The form cannot express "from March onwards", so one end on its own was
  // hand-written. Read as that date with the usual window rather than as an
  // exact day nobody asked for.
  it("gives a one-sided date the default window", () => {
    expect(prefill({ available_from: { after: "2026-03-01" } }, COVERED)).toMatchObject({
      availableOn: "2026-03-01",
      dayWindow: DAY_WINDOW,
    });
  });

  it("ignores anything that is not a date", () => {
    const fields = prefill(
      { available_from: { after: "soon", before: "" } } as unknown as Criteria,
      COVERED,
    );
    expect(fields.availableOn).toBe("");
  });
});

describe("the areas", () => {
  it("keeps the covered ones and names the rest", () => {
    const fields = prefill(
      { areas: { postcode_districts: ["e14", "SE16", "N1"] } },
      COVERED,
    );
    expect(fields.districts).toEqual(["E14", "SE16"]);
    // Said rather than swallowed: saving would drop it from their search.
    expect(fields.dropped).toEqual(["N1"]);
  });

  it("does not repeat a district stored twice", () => {
    const fields = prefill({ areas: { postcode_districts: ["E14", "e14"] } }, COVERED);
    expect(fields.districts).toEqual(["E14"]);
  });

  it("drops everything when nothing is covered any more", () => {
    const fields = prefill({ areas: { postcode_districts: ["E14"] } }, []);
    expect(fields.districts).toEqual([]);
    expect(fields.dropped).toEqual(["E14"]);
  });
});

describe("the tick boxes", () => {
  it("ticks what was saved and ignores what the form cannot offer", () => {
    const fields = prefill(
      {
        property_types: ["flat", "mansion"],
        furnished: ["Unfurnished"],
        pets_allowed: true,
      } as unknown as Criteria,
      COVERED,
    );
    expect(fields.types).toEqual(["flat"]);
    expect(fields.furnished).toEqual(["unfurnished"]);
    expect(fields.pets).toBe(true);
  });

  it("leaves pets alone unless it was actually set", () => {
    expect(prefill({}, COVERED).pets).toBe(false);
  });

  // The one control that reads its field backwards: the toggle is on when
  // duplicates are *not* to be sent, so a filter with nothing saved opens with
  // it on and only `send_duplicates` turns it off.
  it("opens the duplicates toggle on unless copies were asked for", () => {
    expect(prefill({}, COVERED).oneAlertPerFlat).toBe(true);
    expect(prefill({ send_duplicates: true }, COVERED).oneAlertPerFlat).toBe(false);
  });
});

// The two halves of the same journey: what the form submits, parsed into
// criteria exactly as /api/subscribe does it, then read back onto the controls.
// Every value has to come home to the position it was sent from — this is the
// test that fails if either side's bounds are changed on their own.
describe("a filter saved from the form and reopened", () => {
  it("comes back on the same controls", () => {
    const sent = {
      districts: ["E14", "SE8"],
      price_min: "1200",
      price_max: "2500",
      bedrooms_min: "1",
      bedrooms_max: "3",
      bathrooms_max: "2",
      area_min: String(asSqft(45)),
      area_max: String(asSqft(90)),
      property_types: ["flat", "house"],
      furnished: ["furnished", "part"],
      pets_allowed: "on",
      available_after: "2026-03-01",
      available_before: "2026-03-11",
    };

    expect(prefill(parseForm(sent, COVERED), COVERED)).toEqual({
      districts: ["E14", "SE8"],
      dropped: [],
      rent: [1200, 2500],
      bedrooms: [1, 3],
      bathrooms: [BATHS_MIN, 2],
      size: [45, 90],
      types: ["flat", "house"],
      furnished: ["furnished", "part"],
      pets: true,
      oneAlertPerFlat: true,
      availableOn: "2026-03-06",
      dayWindow: 5,
    });
  });

  it("brings an untouched form back untouched", () => {
    const criteria = parseForm({ districts: ["E14"] }, COVERED);
    expect(prefill(criteria, COVERED)).toEqual({
      ...blank(),
      districts: ["E14"],
    });
  });
});
