import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { sendWhatsApp } from "../whatsapp";

// The meter writes a row per delivered message and needs a database for it.
// Stubbed, not pointed at one: these tests are about the shape of what goes
// to Meta, and the ledger has its own tests against a real Postgres
// (`test_outbox_db`). Without this every case here would log a connection
// failure it is not about.
vi.mock("../wa-meter", () => ({ meterWhatsApp: vi.fn(async () => {}) }));

type Call = { url: string; payload: Record<string, any> };

function fetching(...answers: boolean[]): Call[] {
  const calls: Call[] = [];
  let turn = 0;
  vi.stubGlobal("fetch", async (url: string, init: { body: string }) => {
    calls.push({ url, payload: JSON.parse(init.body) });
    const ok = answers[Math.min(turn++, answers.length - 1)] ?? true;
    // `json` as well as `text`: a successful send reads the message id out of
    // the body for the ledger, so a fake without it would be testing a path
    // the real sender does not take.
    return {
      ok,
      status: ok ? 200 : 400,
      text: async () => "no",
      json: async () => ({ messages: [{ id: "wamid.test" }] }),
    } as unknown as Response;
  });
  return calls;
}

beforeEach(() => {
  process.env.WA_PHONE_NUMBER_ID = "123";
  process.env.WA_ACCESS_TOKEN = "t";
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("a reply with a link on it", () => {
  it("draws the link as a button rather than writing it out", async () => {
    const calls = fetching(true);
    expect(
      await sendWhatsApp("+44 7700 900123", "💎 Full access", {
        label: "Choose a plan",
        url: "https://site.test/upgrade?t=abc",
      }),
    ).toBe(true);

    expect(calls).toHaveLength(1);
    expect(calls[0]?.payload).toEqual({
      messaging_product: "whatsapp",
      to: "447700900123",
      type: "interactive",
      interactive: {
        type: "cta_url",
        body: { text: "💎 Full access" },
        action: {
          name: "cta_url",
          parameters: {
            display_text: "Choose a plan",
            url: "https://site.test/upgrade?t=abc",
          },
        },
      },
    });
  });

  it("cuts the label to the twenty characters WhatsApp allows", async () => {
    const calls = fetching(true);
    await sendWhatsApp("447700900123", "x", {
      label: "Choose a plan and pay for it today",
      url: "https://site.test/u",
    });
    expect(calls[0]?.payload.interactive.action.parameters.display_text).toBe(
      "Choose a plan and pa",
    );
  });

  it("falls back to the plain message when the button is refused", async () => {
    // A reply nobody receives is worse than a reply nobody can tap, and the
    // cta_url shape is the one Meta may decline outright.
    const calls = fetching(false, true);
    expect(
      await sendWhatsApp("447700900123", "💎 Full access", {
        label: "Choose a plan",
        url: "https://site.test/u",
      }),
    ).toBe(true);

    expect(calls).toHaveLength(2);
    expect(calls[1]?.payload.type).toBe("text");
    expect(calls[1]?.payload.text.body).toBe("💎 Full access\nhttps://site.test/u");
    // A link in the body is a link worth previewing.
    expect(calls[1]?.payload.text.preview_url).toBe(true);
  });

  it("is sent as text, link and all, when it is too long for a button", async () => {
    const calls = fetching(true);
    const body = "x".repeat(1100);
    await sendWhatsApp("447700900123", body, {
      label: "Open the site",
      url: "https://site.test/u",
    });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.payload.type).toBe("text");
    expect(calls[0]?.payload.text.body).toBe(`${body}\nhttps://site.test/u`);
  });
});

describe("a reply with nothing to tap", () => {
  it("is plain text with no preview to guess at", async () => {
    const calls = fetching(true);
    await sendWhatsApp("447700900123", "You have no active filter.");
    expect(calls[0]?.payload).toEqual({
      messaging_product: "whatsapp",
      to: "447700900123",
      type: "text",
      text: { preview_url: false, body: "You have no active filter." },
    });
  });
});

describe("without credentials", () => {
  it("says which one is missing rather than failing silently", async () => {
    delete process.env.WA_ACCESS_TOKEN;
    const calls = fetching(true);
    const complained = vi.spyOn(console, "error").mockImplementation(() => undefined);

    expect(await sendWhatsApp("447700900123", "x")).toBe(false);
    expect(calls).toHaveLength(0);
    expect(complained.mock.calls[0]?.[0]).toContain("WA_ACCESS_TOKEN");
    complained.mockRestore();
  });
});
