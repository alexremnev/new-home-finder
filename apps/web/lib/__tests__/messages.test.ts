import { describe, expect, it } from "vitest";

import type { Criteria } from "../criteria";
import { criteriaCard, criteriaChanged, criteriaSet } from "../messages";

const full: Criteria = {
  areas: { postcode_districts: ["SE16", "E14", "SE8"] },
  price_pcm: { min: 1200, max: 2000 },
  bedrooms: { min: 1, max: 2 },
  bathrooms: { min: 1 },
  property_types: ["flat", "studio"],
  furnished: ["furnished"],
  available_from: { after: "2026-10-01", before: "2026-10-21" },
  pets_allowed: true,
  bills_included: true,
  landlord_direct_only: true,
  min_tenancy_max_months: 12,
};

describe("the criteria card", () => {
  it("states every choice the person made", () => {
    expect(criteriaCard(full).split("\n")).toEqual([
      "📍 Areas: SE16, E14, SE8",
      "💷 Rent: £1,200–£2,000 a month",
      "🛏 Bedrooms: 1–2",
      "🛁 Bathrooms: 1 or more",
      "🏠 Type: flat, studio",
      "🛋 Furnishing: furnished",
      "📅 Available: 1 Oct 2026 – 21 Oct 2026",
      "🐾 Pets must be allowed",
      "💡 Bills must be included",
      "🤝 From the landlord directly",
      "📝 Minimum tenancy: no more than 12 months",
    ]);
  });

  it("says nothing about what was left blank", () => {
    const card = criteriaCard({ areas: { postcode_districts: ["SE16"] } });
    expect(card).toBe("📍 Areas: SE16");
  });

  it("never prints the word undefined or a bare null", () => {
    const card = criteriaCard({});
    expect(card).not.toMatch(/undefined|null|NaN/);
    expect(card).toBe("📍 Areas: everywhere covered");
  });

  it("calls a flat with no bedrooms a studio", () => {
    expect(criteriaCard({ bedrooms: { min: 0, max: 0 } })).toContain("🛏 Bedrooms: studio");
    expect(criteriaCard({ bedrooms: { min: 0, max: 2 } })).toContain("🛏 Bedrooms: studio–2");
  });

  it("reads an open-ended range as open-ended", () => {
    expect(criteriaCard({ price_pcm: { max: 1800 } })).toContain("up to £1,800 a month");
    expect(criteriaCard({ price_pcm: { min: 900 } })).toContain("£900 or more a month");
  });

  it("collapses a range with one value rather than repeating it", () => {
    expect(criteriaCard({ bedrooms: { min: 2, max: 2 } })).toContain("🛏 Bedrooms: 2");
  });

  it("groups thousands in the rent", () => {
    expect(criteriaCard({ price_pcm: { min: 1200, max: 10000 } })).toContain(
      "£1,200–£10,000",
    );
  });

  it("writes dates the way a person reads them, not as ISO", () => {
    expect(criteriaCard({ available_from: { after: "2026-10-01" } })).toContain(
      "📅 Available: from 1 Oct 2026",
    );
    expect(criteriaCard({ available_from: { before: "2026-12-25" } })).toContain(
      "📅 Available: by 25 Dec 2026",
    );
    expect(criteriaCard({ available_from: { after: "2026-10-01" } })).not.toContain("2026-10-01");
  });

  it("leaves a date it cannot read alone rather than inventing one", () => {
    expect(criteriaCard({ available_from: { after: "soon" } })).toContain("from soon");
  });

  it("omits a requirement that was not asked for", () => {
    const card = criteriaCard({ pets_allowed: false, bills_included: false });
    expect(card).not.toContain("Pets");
    expect(card).not.toContain("Bills");
  });
});

describe("the two announcements", () => {
  it("tells a new subscriber the criteria are set and what happens next", () => {
    const text = criteriaSet(full);
    expect(text.startsWith("✅ Your search criteria are set")).toBe(true);
    expect(text).toContain(criteriaCard(full));
    expect(text).toContain("only what appears from now on");
    expect(text).toContain("/stop — delete my filter and stop");
  });

  it("tells a returning one that this replaced what they had", () => {
    const text = criteriaChanged(full);
    expect(text.startsWith("✏️ Your search criteria are updated")).toBe(true);
    expect(text).toContain(criteriaCard(full));
    expect(text).toContain("This replaces what you had before");
  });

  it("does not claim a first-time subscription when the filter changed", () => {
    expect(criteriaChanged(full)).not.toContain("criteria are set");
  });
});
