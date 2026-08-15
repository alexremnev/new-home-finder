// The wizard as a list of button presses.
//
// This is the whole reason `apply` is a pure function: a five-step conversation
// that can only be exercised against a live bot is a conversation nobody exercises.
// Everything below runs with no database, no network and no Telegram.

import { describe, expect, it } from "vitest";

import {
  apply,
  applyDistrictText,
  applyPriceText,
  DISTRICT_BUTTONS,
  readDistricts,
  MAX_BEDROOMS_CHOICE,
  parseCallback,
  PRICE_CEILING,
  PRICE_FLOOR,
  render,
  type Context,
  type Session,
} from "../wizard";

const CONTEXT: Context = { districts: ["SE16", "SE8", "E14", "N1"], maxDistricts: 5 };

function session(overrides: Partial<Session> = {}): Session {
  return {
    chatId: "555",
    userId: null,
    step: "districts",
    draft: {},
    promptMsgId: "10",
    ...overrides,
  };
}

/** Feed a list of `callback_data` strings in and return where it ended up. */
function press(start: Session, ...taps: string[]): Session {
  let current = start;
  for (const tap of taps) {
    const action = parseCallback(tap);
    if (!action) throw new Error(`unparseable callback: ${tap}`);
    const outcome = apply(current, action, CONTEXT);
    if (outcome.kind === "reject") throw new Error(`rejected ${tap}: ${outcome.reason}`);
    current = outcome.session;
  }
  return current;
}

// ── the happy path ────────────────────────────────────────────────────────

describe("a full pass", () => {
  it("collects every answer and arrives at the confirmation", () => {
    let s = press(session(), "w:d:SE16", "w:d:E14", "w:dn", "w:b:2");
    expect(s.step).toBe("priceMin");

    const priced = applyPriceText(s, "1500-2200");
    expect(priced.ok).toBe(true);
    if (!priced.ok) return;
    s = press(priced.session, "w:pe:1", "w:f:f");

    expect(s.step).toBe("confirm");
    expect(s.draft).toEqual({
      areas: { postcode_districts: ["SE16", "E14"] },
      bedrooms: { min: 2 },
      price_pcm: { min: 1500, max: 2200 },
      pets_allowed: true,
      furnished: ["furnished"],
    });
  });

  it("produces a draft the matcher's vocabulary already understands", () => {
    // The point of building a `Criteria` from the first tap rather than converting
    // at the end: there is one definition of a filter, and this is it.
    const s = press(session(), "w:d:SE8", "w:dn", "w:b:0");
    expect(Object.keys(s.draft).sort()).toEqual(["areas", "bedrooms"]);
  });

  it("commits from the confirmation step", () => {
    const s = session({ step: "confirm", draft: { areas: { postcode_districts: ["SE16"] } } });
    expect(apply(s, { kind: "finish" }, CONTEXT).kind).toBe("commit");
  });
});

// ── districts ─────────────────────────────────────────────────────────────

describe("districts", () => {
  it("toggles off on a second tap, so a mis-tap is recoverable", () => {
    const s = press(session(), "w:d:SE16", "w:d:SE8", "w:d:SE16");
    expect(s.draft.areas?.postcode_districts).toEqual(["SE8"]);
  });

  it("drops the criterion entirely when the last one is removed", () => {
    // Not `areas: { postcode_districts: [] }`: an empty list would read as a
    // criterion that matches nothing.
    const s = press(session(), "w:d:SE16", "w:d:SE16");
    expect(s.draft.areas).toBeUndefined();
  });

  it("refuses a district that is not being collected", () => {
    // The whole reason the buttons come from `enabledDistricts()`. A filter naming
    // an uncovered district would wait for alerts that cannot arrive.
    const outcome = apply(session(), { kind: "district", code: "SW1" }, CONTEXT);
    expect(outcome.kind).toBe("reject");
    if (outcome.kind === "reject") expect(outcome.reason).toContain("isn't covered");
  });

  it("stops at the plan's limit and says what the limit is", () => {
    const tight: Context = { ...CONTEXT, maxDistricts: 2 };
    let s = session();
    for (const code of ["SE16", "SE8"]) {
      const out = apply(s, { kind: "district", code }, tight);
      if (out.kind === "render") s = out.session;
    }
    const refused = apply(s, { kind: "district", code: "E14" }, tight);
    expect(refused.kind).toBe("reject");
    if (refused.kind === "reject") expect(refused.reason).toContain("2");
    expect(s.draft.areas?.postcode_districts).toHaveLength(2);
  });

  it("will not move on with nothing chosen", () => {
    // Districts are the one step with no "doesn't matter": no area means every
    // listing in London, which is not a filter.
    const outcome = apply(session(), { kind: "districtsDone" }, CONTEXT);
    expect(outcome.kind).toBe("reject");
  });

  it("has no skip button", () => {
    const { keyboard } = render(session(), CONTEXT);
    const data = keyboard.flat().map((b) => b.callback_data);
    expect(data).not.toContain("w:sk");
  });
});

// ── price ─────────────────────────────────────────────────────────────────

describe("price, asked as two numbers", () => {
  // One field asking for "1500-2200" makes people guess the format. Two fields
  // asking for one number each do not, and either end can be waved past.
  const min = () => session({ step: "priceMin" });
  const max = (floor?: number) =>
    session({ step: "priceMax", draft: floor === undefined ? {} : { price_pcm: { min: floor } } });

  it("reads a plain number at each end", () => {
    const low = applyPriceText(min(), "1500");
    expect(low).toMatchObject({ ok: true, session: { draft: { price_pcm: { min: 1500 } } } });
    if (!low.ok) return;
    expect(low.session.step).toBe("priceMax");
    expect(applyPriceText(low.session, "2200")).toMatchObject({
      ok: true,
      session: { draft: { price_pcm: { min: 1500, max: 2200 } } },
    });
  });

  it("tolerates the way people write money", () => {
    expect(applyPriceText(min(), "£1,500")).toMatchObject({
      ok: true, session: { draft: { price_pcm: { min: 1500 } } },
    });
  });

  it("moves on without a bound when skipped", () => {
    const result = applyPriceText(min(), "any");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.session.draft.price_pcm).toBeUndefined();
      expect(result.session.step).toBe("priceMax");
    }
  });

  it("refuses a maximum below the minimum, while they are looking at it", () => {
    const result = applyPriceText(max(2000), "1500");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("2,000");
  });

  it("keeps the floor and the ceiling", () => {
    expect(applyPriceText(min(), String(PRICE_FLOOR)).ok).toBe(true);
    expect(applyPriceText(max(), String(PRICE_CEILING)).ok).toBe(true);
    expect(applyPriceText(min(), "50").ok).toBe(false);
    expect(applyPriceText(max(), String(PRICE_CEILING + 1)).ok).toBe(false);
  });

  it("refuses text that is not a number, quoting it back", () => {
    const result = applyPriceText(min(), "cheap please");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("cheap please");
  });

  it("is only answerable at a price step", () => {
    expect(applyPriceText(session({ step: "pets" }), "1500").ok).toBe(false);
  });
});

// ── the skippable steps ───────────────────────────────────────────────────

describe("skipping", () => {
  it("leaves no criterion behind", () => {
    const s = press(session({ step: "bedrooms" }), "w:sk", "w:sk", "w:sk", "w:sk");
    expect(s.step).toBe("confirm");
    expect(s.draft).toEqual({});
  });

  it("never records pets as false", () => {
    // `pets_allowed: false` means "listings that state pets are NOT allowed" to the
    // matcher, which is not what a person means by declining the question.
    const s = press(session({ step: "pets" }), "w:sk");
    expect("pets_allowed" in s.draft).toBe(false);
  });

  it("offers bedrooms from 0 to the choice ceiling", () => {
    const { keyboard } = render(session({ step: "bedrooms" }), CONTEXT);
    const numbers = keyboard.flat().map((b) => b.callback_data).filter((d) => d?.startsWith("w:b:"));
    expect(numbers).toHaveLength(MAX_BEDROOMS_CHOICE + 1);
    expect(numbers[0]).toBe("w:b:0");
  });
});

// ── /update over an existing filter ───────────────────────────────────────

describe("overwrite", () => {
  const existing = { areas: { postcode_districts: ["N1"] }, bedrooms: { min: 1 } };

  it("starts from a blank draft when asked to replace, not from the old answers", () => {
    // Otherwise Skip would silently mean "keep what was there", which is not what
    // the button says.
    const s = press(session({ step: "overwrite", draft: existing }), "w:ow:1");
    expect(s.step).toBe("districts");
    expect(s.draft).toEqual({});
  });

  it("abandons without writing when asked to keep it", () => {
    const outcome = apply(
      session({ step: "overwrite", draft: existing }),
      { kind: "overwrite", yes: false },
      CONTEXT,
    );
    expect(outcome.kind).toBe("abandon");
  });

  it("shows the current filter so the choice is informed", () => {
    const { text } = render(session({ step: "overwrite", draft: existing }), CONTEXT);
    expect(text).toContain("N1");
  });
});

// ── stale keyboards ───────────────────────────────────────────────────────

describe("a tap from an earlier step", () => {
  it("is refused rather than applied", () => {
    // Old keyboards stay live in Telegram — clearing them is best-effort — so this
    // is what stops a stale tap rewinding the conversation.
    const outcome = apply(session({ step: "confirm" }), { kind: "bedrooms", min: 3 }, CONTEXT);
    expect(outcome.kind).toBe("reject");
  });

  it("does not move the step", () => {
    const s = session({ step: "furnished" });
    const outcome = apply(s, { kind: "districtsDone" }, CONTEXT);
    if (outcome.kind === "reject") expect(outcome.session.step).toBe("furnished");
  });
});

// ── callback encoding ─────────────────────────────────────────────────────

describe("callback data", () => {
  it("stays inside Telegram's 64-byte limit for every button on every step", () => {
    const steps: Session["step"][] = [
      "overwrite", "districts", "bedrooms", "priceMin", "pets", "furnished", "confirm",
    ];
    const wide: Context = {
      districts: Array.from({ length: 40 }, (_, n) => `SE${n}`),
      maxDistricts: 5,
    };
    for (const step of steps) {
      for (const button of render(session({ step }), wide).keyboard.flat()) {
        if (!button.callback_data) continue;
        expect(Buffer.byteLength(button.callback_data, "utf8")).toBeLessThanOrEqual(64);
      }
    }
  });

  it("is decodable for every button the wizard renders", () => {
    for (const step of ["districts", "bedrooms", "pets", "furnished", "confirm"] as const) {
      for (const button of render(session({ step }), CONTEXT).keyboard.flat()) {
        if (!button.callback_data) continue;
        expect(parseCallback(button.callback_data)).not.toBeNull();
      }
    }
  });

  it("ignores anything it does not recognise instead of guessing", () => {
    // An unknown tap is almost always a keyboard from an older version of the bot,
    // and acting on a guess would act on an intention nobody had.
    expect(parseCallback("w:zz")).toBeNull();
    expect(parseCallback("something-else")).toBeNull();
    expect(parseCallback(undefined)).toBeNull();
    expect(parseCallback("w:d:")).toBeNull();
    expect(parseCallback("w:b:99")).toBeNull();
    expect(parseCallback("w:f:q")).toBeNull();
  });

  it("normalises a district to upper case, as the whitelist is", () => {
    expect(parseCallback("w:d:se16")).toEqual({ kind: "district", code: "SE16" });
  });
});

// ── what the person reads ─────────────────────────────────────────────────

describe("the prompts", () => {
  it("number the steps, so the end is visible", () => {
    for (const [step, label] of [
      ["districts", "Step 1 of 5"],
      ["bedrooms", "Step 2 of 5"],
      ["priceMin", "Step 3 of 5"],
      ["pets", "Step 4 of 5"],
      ["furnished", "Step 5 of 5"],
    ] as const) {
      expect(render(session({ step }), CONTEXT).text).toContain(label);
    }
  });

  it("shows the running choice and the allowance on the district step", () => {
    const s = session({ draft: { areas: { postcode_districts: ["SE16", "E14"] } } });
    const { text } = render(s, CONTEXT);
    expect(text).toContain("SE16, E14");
    expect(text).toContain("2 of 5");
  });

  it("marks the chosen districts on their buttons", () => {
    const s = session({ draft: { areas: { postcode_districts: ["SE8"] } } });
    const chosen = render(s, CONTEXT).keyboard.flat().find((b) => b.callback_data === "w:d:SE8");
    expect(chosen?.text).toBe("✓ SE8");
  });

  it("says that only future listings are sent, before Finish is pressed", () => {
    // The first thing a new subscriber notices is silence, so it is promised
    // rather than discovered.
    const { text } = render(session({ step: "confirm" }), CONTEXT);
    expect(text).toContain("from now on");
  });

  it("warns that a pets filter excludes listings that say nothing", () => {
    expect(render(session({ step: "pets" }), CONTEXT).text).toContain("say nothing");
  });
});

// ── cancelling ────────────────────────────────────────────────────────────

describe("cancel", () => {
  it("abandons from any step without writing", () => {
    for (const step of ["districts", "priceMin", "confirm"] as const) {
      expect(apply(session({ step }), { kind: "cancel" }, CONTEXT).kind).toBe("abandon");
    }
  });

  it("start over empties the draft and returns to the first step", () => {
    const s = session({ step: "confirm", draft: { bedrooms: { min: 3 } } });
    const outcome = apply(s, { kind: "restart" }, CONTEXT);
    expect(outcome.kind).toBe("render");
    if (outcome.kind === "render") {
      expect(outcome.session.step).toBe("districts");
      expect(outcome.session.draft).toEqual({});
    }
  });
});

// ── districts typed rather than tapped ────────────────────────────────────

describe("typed districts", () => {
  // A keyboard cannot hold London — around 300 outward codes, and Telegram will
  // not render a hundred rows. Typing is how most of them are reached, so the
  // parsing has to be forgiving about form and strict about result.
  const ALLOWED = ["SE16", "SE8", "E14", "E11", "N1", "NW1", "SW17", "EC1A"];
  const CTX: Context = { districts: ALLOWED, maxDistricts: 5 };

  const NAMES = { leytonstone: "E11", "camden town": "NW1", "canary wharf": "E14" };

  it("reads commas, spaces and any case", () => {
    expect(readDistricts("SE16, E14 n1", ALLOWED).codes).toEqual(["SE16", "E14", "N1"]);
  });

  it("accepts an area name as readily as a code", () => {
    // The name is what people know. "E11" is what the filter needs, and nobody
    // should have to look it up.
    expect(readDistricts("Leytonstone", ALLOWED, NAMES).codes).toEqual(["E11"]);
    expect(readDistricts("leytonstone", ALLOWED, NAMES).codes).toEqual(["E11"]);
  });

  it("keeps a two-word name whole", () => {
    // Splitting the whole input on whitespace — which the code-only version did —
    // tore every two-word name in half.
    expect(readDistricts("Camden Town", ALLOWED, NAMES).codes).toEqual(["NW1"]);
  });

  it("mixes names and codes in one message", () => {
    expect(readDistricts("Leytonstone, SE16, Canary Wharf", ALLOWED, NAMES).codes)
      .toEqual(["E11", "SE16", "E14"]);
  });

  it("reports a name it does not know as unknown, not as uncovered", () => {
    // "Narnia isn't covered yet" implies it would be, one day.
    expect(readDistricts("Narnia", ALLOWED, NAMES).unknown).toEqual(["Narnia"]);
  });

  it("takes the outward code from a full postcode, without complaining about the rest", () => {
    // Someone pasting their own postcode must not be told they got it wrong.
    const result = readDistricts("E11 4EG", ALLOWED);
    expect(result.codes).toEqual(["E11"]);
    expect(result.unknown).toEqual([]);
  });

  it("separates what is not a district from what is not covered", () => {
    // Two different answers: one is a typo, the other is a coverage gap, and
    // telling someone "banana isn't covered yet" would be nonsense.
    expect(readDistricts("banana", ALLOWED).unknown).toEqual(["BANANA"]);
    expect(readDistricts("ZZ99", ALLOWED).notCovered).toEqual(["ZZ99"]);
  });

  it("collapses duplicates", () => {
    expect(readDistricts("SE16 se16 SE16", ALLOWED).codes).toEqual(["SE16"]);
  });

  it("adds to what was already chosen rather than replacing it", () => {
    const session = { chatId: "1", userId: null, step: "districts" as const,
                      draft: { areas: { postcode_districts: ["N1"] } }, promptMsgId: null };
    const result = applyDistrictText(session, "SE8", CTX);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.session.draft.areas?.postcode_districts).toEqual(["N1", "SE8"]);
  });

  it("stops at the plan's limit and names what it did not add", () => {
    const session = { chatId: "1", userId: null, step: "districts" as const,
                      draft: {}, promptMsgId: null };
    const result = applyDistrictText(session, "SE16, E14, N1, SE8", { ...CTX, maxDistricts: 2 });
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.session.draft.areas?.postcode_districts).toHaveLength(2);
      expect(result.added).toHaveLength(2);
    }
  });

  it("refuses text with no district in it, saying why", () => {
    const session = { chatId: "1", userId: null, step: "districts" as const,
                      draft: {}, promptMsgId: null };
    const result = applyDistrictText(session, "somewhere nice", CTX);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toMatch(/not a district/);
  });

  it("shows a handful of buttons however many districts are covered", () => {
    const many: Context = {
      districts: Array.from({ length: 300 }, (_, n) => `E${n + 1}`),
      maxDistricts: 5,
    };
    const view = render({ chatId: "1", userId: null, step: "districts",
                          draft: {}, promptMsgId: null }, many);
    const buttons = view.keyboard.flat().filter((b) => b.callback_data?.startsWith("w:d:"));
    expect(buttons.length).toBeLessThanOrEqual(DISTRICT_BUTTONS + 1);
    expect(view.text).toMatch(/Type them and send/);
  });

  it("keeps a chosen district visible even when it is outside the sample", () => {
    // Otherwise a district reached by typing could never be removed.
    const many: Context = {
      districts: Array.from({ length: 300 }, (_, n) => `E${n + 1}`),
      maxDistricts: 5,
    };
    const view = render({ chatId: "1", userId: null, step: "districts",
                          draft: { areas: { postcode_districts: ["E250"] } },
                          promptMsgId: null }, many);
    expect(view.keyboard.flat().some((b) => b.text === "✓ E250")).toBe(true);
  });
});
