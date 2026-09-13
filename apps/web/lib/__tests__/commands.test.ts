import { describe, expect, it } from "vitest";

import { BOT_MENU, parseCommand } from "../commands";

describe("stop is recognised however it is written", () => {

  it("does not stop on a message that merely mentions stopping", () => {

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

describe("quick edits", () => {
  it("reads a range", () => {
    expect(parseCommand("/price 1500-2200")).toEqual({
      kind: "patch",
      patch: { field: "price", min: 1500, max: 2200 },
    });
  });

  it("treats a single number as a ceiling", () => {

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

    expect(parseCommand("/price any")).toEqual({ kind: "patch", patch: { field: "price" } });
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

});

describe("the retired alert command", () => {
  it("is no longer understood, so it cannot look like it worked", () => {

    expect(parseCommand("/alerts 5").kind).toBe("help");
  });
});

describe("the other commands", () => {
  it("distinguishes showing the filter from changing it", () => {
    expect(parseCommand("/show").kind).toBe("show");
    expect(parseCommand("/settings").kind).toBe("show");

    expect(parseCommand("/filter").kind).toBe("update");
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

describe("the five menu commands", () => {
  it("map to the right intent", () => {
    expect(parseCommand("/start")).toEqual({ kind: "start", token: null });
    expect(parseCommand("/update")).toEqual({ kind: "update" });
    expect(parseCommand("/current")).toEqual({ kind: "show" });
    expect(parseCommand("/pay")).toEqual({ kind: "upgrade" });
    expect(parseCommand("/stop")).toEqual({ kind: "stop" });
  });

  it("are all understood by the parser", () => {

    for (const entry of BOT_MENU) {
      expect(parseCommand(`/${entry.command}`).kind).not.toBe("help");
    }
  });

  it("is five entries, because a menu is read before anything is understood", () => {
    expect(BOT_MENU).toHaveLength(5);
  });
});

describe("the older names still work", () => {

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

    expect(parseCommand("/menu")).toEqual({ kind: "menu" });
  });
});
