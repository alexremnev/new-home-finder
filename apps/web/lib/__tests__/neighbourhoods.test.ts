import { describe, expect, it } from "vitest";

import { matchAreas, type Area } from "../neighbourhoods";

const AREAS: Area[] = [
  { name: "Canary Wharf", code: "E14" },
  { name: "Canning Town", code: "E16" },
  { name: "Isle of Dogs", code: "E14" },
  { name: "Poplar", code: "E14" },
  { name: "Whitechapel", code: "E1" },
  { name: "Wharf Road", code: "N1" },
];

describe("matchAreas", () => {
  it("shows everything when nothing has been typed", () => {
    expect(matchAreas(AREAS, "")).toHaveLength(AREAS.length);
  });

  it("puts what starts with the word above what merely contains it", () => {
    // "Wharf Road" contains the word and "Canary Wharf" starts with it. A list
    // that leads with the first reads as broken, which is the whole reason the
    // ranking exists.
    expect(matchAreas(AREAS, "canary")[0]?.name).toBe("Canary Wharf");
    expect(matchAreas(AREAS, "wharf").map((one) => one.name)).toEqual([
      "Wharf Road",
      "Canary Wharf",
    ]);
  });

  it("puts an exact outcode first, and still offers the longer ones", () => {
    // The case the old datalist could not handle: typing E1 committed E1 on
    // the spot and E14 became unreachable. Both are rows now, E1 first.
    const found = matchAreas(AREAS, "E1").map((one) => one.code);
    expect(found[0]).toBe("E1");
    expect(found).toContain("E14");
  });

  it("matches on the outcode as well as the name", () => {
    expect(matchAreas(AREAS, "e16").map((one) => one.name)).toEqual(["Canning Town"]);
  });

  it("is not case or space sensitive", () => {
    expect(matchAreas(AREAS, "  CANARY  ")[0]?.name).toBe("Canary Wharf");
  });

  it("drops what is already chosen, by code", () => {
    // One outcode can hold several neighbourhoods, and the filter is by
    // district — so choosing Canary Wharf takes Poplar and Isle of Dogs with
    // it. Leaving them on the list would offer a choice that changes nothing.
    const left = matchAreas(AREAS, "", ["E14"]).map((one) => one.name);
    expect(left).not.toContain("Poplar");
    expect(left).not.toContain("Canary Wharf");
    expect(left).toContain("Canning Town");
  });

  it("returns nothing rather than everything when there is no match", () => {
    expect(matchAreas(AREAS, "Manchester")).toEqual([]);
  });
});
