import { describe, expect, it } from "vitest";

import { BOT_MENU, parseCommand } from "../commands";

describe("stop is recognised however it is written", () => {
  // This is not a matter of taste. Someone who types "СТОП" has withdrawn
  // consent as clearly as someone who types "/stop", and answering with a help
  // message instead of stopping is not a defensible reading of it.
  it.each(["/stop", "STOP", "stop", " Stop ", "стоп", "СТОП", "unsubscribe", "Отписаться"])(
    "%s stops",
    (text) => {
      expect(parseCommand(text)).toEqual({ kind: "stop" });
    },
  );

  it("does not stop on a message that merely mentions stopping", () => {
    // Otherwise a question about how to unsubscribe would unsubscribe them.
    expect(parseCommand("how do I stop these?").kind).toBe("help");
    expect(parseCommand("stop sending flats above £2000").kind).toBe("help");
  });
});

describe("start", () => {
  it("carries the token through", () => {
    expect(parseCommand("/start abc123")).toEqual({ kind: "start", token: "abc123" });
  });

  it("reports a bare start with no token rather than inventing one", () => {
    expect(parseCommand("/start")).toEqual({ kind: "start", token: null });
    expect(parseCommand("/start   ")).toEqual({ kind: "start", token: null });
  });

  it("survives the group-chat form of the command", () => {
    expect(parseCommand("/start@london_rent_alerts_bot tok").kind).toBe("start");
  });

  it("is not confused by a token that looks like a command", () => {
    expect(parseCommand("/start /stop")).toEqual({ kind: "start", token: "/stop" });
  });
});


// ── editing the filter from the bot ───────────────────────────────────────

describe("quick edits", () => {
  it("reads a range", () => {
    expect(parseCommand("/price 1500-2200")).toEqual({
      kind: "patch",
      patch: { field: "price", min: 1500, max: 2200 },
    });
  });

  it("treats a single number as a ceiling", () => {
    // What someone typing one number means by it: "no more than this".
    expect(parseCommand("/price 2000")).toEqual({
      kind: "patch",
      patch: { field: "price", max: 2000 },
    });
  });

  it("ignores the currency symbol and separators people type", () => {
    expect(parseCommand("/price £1,500 - £2,200")).toEqual({
      kind: "patch",
      patch: { field: "price", min: 1500, max: 2200 },
    });
  });

  it("reads an open-ended range in both directions", () => {
    expect(parseCommand("/price 1500-")).toEqual({
      kind: "patch",
      patch: { field: "price", min: 1500 },
    });
    expect(parseCommand("/price -2200")).toEqual({
      kind: "patch",
      patch: { field: "price", max: 2200 },
    });
  });

  it("can clear a criterion", () => {
    // Without this the only way to widen a search back out would be to
    // unsubscribe and start again.
    expect(parseCommand("/price any")).toEqual({ kind: "patch", patch: { field: "price" } });
  });

  it("explains itself rather than guessing at nonsense", () => {
    const result = parseCommand("/price cheap");
    expect(result.kind).toBe("help");
    expect(result.kind === "help" && result.reason).toContain("/price");
  });

  it("reads districts however they are separated", () => {
    expect(parseCommand("/areas SE16, SE8")).toEqual({
      kind: "patch",
      patch: { field: "areas", districts: ["SE16", "SE8"] },
    });
    expect(parseCommand("/areas se16 e14")).toEqual({
      kind: "patch",
      patch: { field: "areas", districts: ["se16", "e14"] },
    });
  });

  it("reads flags", () => {
    expect(parseCommand("/pets on")).toEqual({
      kind: "patch",
      patch: { field: "pets_allowed", on: true },
    });
    expect(parseCommand("/bills off")).toEqual({
      kind: "patch",
      patch: { field: "bills_included", on: false },
    });
    expect(parseCommand("/direct yes")).toEqual({
      kind: "patch",
      patch: { field: "landlord_direct_only", on: true },
    });
  });

  it("does not accept a flag without a value", () => {
    // "/pets" alone is ambiguous, and guessing "on" would silently narrow the
    // filter to listings that state a pet policy.
    expect(parseCommand("/pets").kind).toBe("help");
  });

});

describe("the retired alert command", () => {
  it("is no longer understood, so it cannot look like it worked", () => {
    // There is no cap to set. Accepting the command and doing nothing would be
    // worse than not knowing it.
    expect(parseCommand("/alerts 5").kind).toBe("help");
  });
});

describe("the other commands", () => {
  it("distinguishes showing the filter from changing it", () => {
    expect(parseCommand("/show").kind).toBe("show");
    expect(parseCommand("/settings").kind).toBe("show");
    // "Let me change this" now means the wizard, not a link to the form. /edit is
    // what still sends the form, for someone who would rather use a screen.
    expect(parseCommand("/filter").kind).toBe("update");
    expect(parseCommand("/edit").kind).toBe("edit");
  });

  it("recognises the upgrade ask", () => {
    expect(parseCommand("/upgrade").kind).toBe("upgrade");
    expect(parseCommand("/plan").kind).toBe("upgrade");
  });

  it("parses a grant", () => {
    expect(parseCommand("/grant lra-abc123 paid 14")).toEqual({
      kind: "grant",
      ref: "LRA-ABC123",
      plan: "paid",
      days: 14,
    });
  });

  it("refuses an incomplete grant rather than granting something", () => {
    expect(parseCommand("/grant LRA-ABC123 paid").kind).toBe("help");
    expect(parseCommand("/grant LRA-ABC123 paid forever").kind).toBe("help");
    // Zero days would be a plan that has already expired: accepted, recorded as
    // a payment, and worth nothing to whoever paid for it.
    expect(parseCommand("/grant LRA-ABC123 paid 0").kind).toBe("help");
    expect(parseCommand("/grant LRA-ABC123 paid -5").kind).toBe("help");
  });

  it("tolerates the group-chat suffix on any command", () => {
    expect(parseCommand("/show@london_rent_alerts_bot").kind).toBe("show");
  });

  it("falls back to help on anything it does not know", () => {
    expect(parseCommand("hello").kind).toBe("help");
    expect(parseCommand("/wat").kind).toBe("help");
  });
});

// ── the menu's names, and the ones they replaced ───────────────────────────

describe("the five menu commands", () => {
  it("map to the right intent", () => {
    expect(parseCommand("/start")).toEqual({ kind: "start", token: null });
    expect(parseCommand("/update")).toEqual({ kind: "update" });
    expect(parseCommand("/current")).toEqual({ kind: "show" });
    expect(parseCommand("/pay")).toEqual({ kind: "upgrade" });
    expect(parseCommand("/stop")).toEqual({ kind: "stop" });
  });

  it("are all understood by the parser", () => {
    // A menu entry the parser does not know is worse than no menu: the person taps
    // it from a list the bot itself offered and is answered with a help screen.
    for (const entry of BOT_MENU) {
      expect(parseCommand(`/${entry.command}`).kind).not.toBe("help");
    }
  });

  it("is five entries, because a menu is read before anything is understood", () => {
    expect(BOT_MENU).toHaveLength(5);
  });
});

describe("the older names still work", () => {
  // They are sitting in people's chat history. A command that used to work and now
  // answers with a help screen reads as a broken bot, not as a rename.
  it.each([
    ["/show", "show"],
    ["/settings", "show"],
    ["/upgrade", "upgrade"],
    ["/plan", "upgrade"],
    ["/edit", "edit"],
  ])("%s is still %s", (text, kind) => {
    expect(parseCommand(text).kind).toBe(kind);
  });

  it("/filter with no argument opens the wizard, with one shows the filter", () => {
    expect(parseCommand("/filter").kind).toBe("update");
    expect(parseCommand("/filter please").kind).toBe("show");
  });

  it("tolerates the @botname suffix a group adds", () => {
    expect(parseCommand("/update@london_rent_alerts_bot").kind).toBe("update");
    expect(parseCommand("/current@some_bot").kind).toBe("show");
  });
});

describe("/menu", () => {
  it("parses, so the route can check who sent it", () => {
    // Authorisation is the route's job — the parser must not decide it, or the
    // admin check would be spread over two files.
    expect(parseCommand("/menu")).toEqual({ kind: "menu" });
  });
});
