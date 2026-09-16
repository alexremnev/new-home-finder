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

describe("the shorthand commands are gone", () => {
  it("answers with help rather than pretending to have changed something", () => {
    for (const typed of [
      "/price 1500-2200",
      "/price any",
      "/beds 1-2",
      "/areas SE16, SE8",
      "/pets on",
      "/bills off",
      "/direct on",
      "/edit",
    ]) {
      expect(parseCommand(typed).kind).toBe("help");
    }
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

  it("stays short, because a menu is read before anything is understood", () => {

    // Six is the ceiling, not a target. /support earned a place because a
    // command nobody can find is a support channel nobody uses.
    expect(BOT_MENU.length).toBeLessThanOrEqual(6);
    expect(BOT_MENU.map((entry) => entry.command)).toContain("support");
  });

  it("offers a way to report a problem", () => {
    expect(parseCommand("/support")).toEqual({ kind: "support" });
    expect(parseCommand("/cancel")).toEqual({ kind: "cancel" });
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
