import { describe, expect, it } from "vitest";

import { chatIdOf } from "../telegram";

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
