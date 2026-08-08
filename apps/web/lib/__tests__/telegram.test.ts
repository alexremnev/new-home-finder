import { describe, expect, it } from "vitest";

import { chatIdOf, parseCommand } from "../telegram";

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
    expect(parseCommand("how do I stop these?").kind).toBe("other");
    expect(parseCommand("stop sending flats above £2000").kind).toBe("other");
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

describe("chat id", () => {
  it("prefers the chat over the sender", () => {
    expect(chatIdOf({ message: { chat: { id: 7 }, from: { id: 9 } } })).toBe("7");
  });

  it("is a string, because ids exceed what a float represents exactly", () => {
    expect(chatIdOf({ message: { chat: { id: 1234567890123 } } })).toBe("1234567890123");
  });

  it("is null when the update carries no chat at all", () => {
    expect(chatIdOf({})).toBeNull();
    expect(chatIdOf({ message: {} })).toBeNull();
  });
});
