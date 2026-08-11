// The wizard as a list of button presses.
//
// This is the whole reason `apply` is a pure function: a five-step conversation
// that can only be exercised against a live bot is a conversation nobody exercises.
// Everything below runs with no database, no network and no Telegram.

import { describe, expect, it } from "vitest";

import {
  apply,
  applyPriceText,
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
    expect(s.step).toBe("price");

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

describe("price", () => {
  const at = () => session({ step: "price" });

  it("reads the same formats the /price command accepts", () => {
    expect(applyPriceText(at(), "1500-2200")).toMatchObject({
      ok: true,
      session: { draft: { price_pcm: { min: 1500, max: 2200 } } },
    });
    // A bare number is a ceiling, which is what one number means to the person
    // typing it.
    expect(applyPriceText(at(), "2000")).toMatchObject({
      ok: true,
      session: { draft: { price_pcm: { max: 2000 } } },
    });
    expect(applyPriceText(at(), "£1,800-2,400")).toMatchObject({
      ok: true,
      session: { draft: { price_pcm: { min: 1800, max: 2400 } } },
    });
  });

  it("treats 'any' as the skip it is, rather than refusing it", () => {
    const result = applyPriceText(at(), "any");
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.session.draft.price_pcm).toBeUndefined();
      expect(result.session.step).toBe("pets");
    }
  });

  it("refuses a rent below the floor, because it is a weekly figure or a typo", () => {
    const result = applyPriceText(at(), "50-200");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain(String(PRICE_FLOOR));
  });

  it("refuses a rent above the ceiling", () => {
    const result = applyPriceText(at(), `1500-${PRICE_CEILING + 1}`);
    expect(result.ok).toBe(false);
  });

  it("accepts exactly the floor and the ceiling", () => {
    expect(applyPriceText(at(), `${PRICE_FLOOR}-${PRICE_CEILING}`).ok).toBe(true);
  });

  it("refuses a range that is the wrong way round", () => {
    const result = applyPriceText(at(), "2200-1500");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("minimum is above");
  });

  it("refuses text that is not a price at all, quoting it back", () => {
    const result = applyPriceText(at(), "cheap please");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("cheap please");
  });

  it("is only answerable while the wizard is at that step", () => {
    expect(applyPriceText(session({ step: "pets" }), "1500-2200").ok).toBe(false);
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
      "overwrite", "districts", "bedrooms", "price", "pets", "furnished", "confirm",
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
      ["price", "Step 3 of 5"],
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
    for (const step of ["districts", "price", "confirm"] as const) {
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
