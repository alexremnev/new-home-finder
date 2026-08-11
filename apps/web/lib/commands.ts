// What the bot understands.
//
// Pure parsing, no database and no network, because this is where a
// misunderstanding is most expensive: reading a message as a stop that is not one
// cuts someone off, and failing to read a real stop keeps messaging someone who
// asked to be left alone.
//
// Two ways to change a filter exist on purpose. A quick command is right for the
// change people actually make — the budget, one more district — and the form is
// right for setting one up. Both converge on the same `criteria` object through
// the same validation in criteria.ts, so there is one vocabulary, not two.

import type { Patch } from "./criteria";

export type Command =
  | { kind: "start"; token: string | null }
  | { kind: "stop" }
  | { kind: "show" }
  | { kind: "update" }        // the in-bot wizard
  | { kind: "edit" }          // send a link to the form
  | { kind: "upgrade" }
  | { kind: "patch"; patch: Patch }
  | { kind: "grant"; ref: string; plan: string; days: number }
  | { kind: "menu" }          // admin: push the command list to Telegram
  | { kind: "help"; reason?: string };

/**
 * The menu behind the bot's ⌘ button, pushed with `setMyCommands`.
 *
 * Five entries and no more. This list is what a new person reads before they know
 * anything about the product, so it names the five things there are to do rather
 * than everything that can be typed — the quick edits (/price, /areas) and the
 * aliases below stay working and stay out of the menu.
 *
 * Lives beside the parser on purpose: a menu that offers a command the parser does
 * not understand is worse than no menu, and keeping both in one file is what stops
 * them drifting.
 */
export const BOT_MENU: { command: string; description: string }[] = [
  { command: "start", description: "Start a new search" },
  { command: "update", description: "Update my search" },
  { command: "current", description: "Show my current criteria" },
  { command: "pay", description: "2 weeks of alerts" },
  { command: "stop", description: "Delete my filter and stop" },
];

// Case-insensitive, and every wording a person actually uses. Someone who types
// "СТОП" has withdrawn consent as clearly as someone who types "/stop", and
// answering them with a help message instead of stopping is not defensible.
const STOP_WORDS = new Set([
  "/stop", "stop", "стоп", "unsubscribe", "отписаться", "отписка", "/unsubscribe",
]);

const FLAGS: Record<string, "pets_allowed" | "bills_included" | "landlord_direct_only"> = {
  "/pets": "pets_allowed",
  "/bills": "bills_included",
  "/direct": "landlord_direct_only",
};

export function parseCommand(text: string | undefined): Command {
  const trimmed = (text ?? "").trim();
  const lower = trimmed.toLowerCase();

  // Checked before anything else, as §22 requires: a stop must not be able to
  // fall through into other handling.
  if (STOP_WORDS.has(lower)) return { kind: "stop" };

  if (lower === "/start" || lower.startsWith("/start ") || lower.startsWith("/start@")) {
    const parts = trimmed.split(/\s+/);
    const token = parts.length > 1 ? (parts[1] ?? "").trim() : "";
    return { kind: "start", token: token || null };
  }

  // The command word, with any @botname suffix removed — groups append it.
  const [rawWord = "", ...rest] = trimmed.split(/\s+/);
  const word = rawWord.toLowerCase().split("@")[0] ?? "";
  const argument = rest.join(" ").trim();

  // The menu's names first, then the older ones as aliases. The aliases cost three
  // lines and are worth it: they are sitting in people's chat history, and a
  // command that used to work and now answers with a help screen reads as a broken
  // bot rather than as a rename.
  if (word === "/current" || word === "/show" || word === "/settings") return { kind: "show" };
  if (word === "/filter") {
    // /filter with an argument was always a request to see it; without one it means
    // "let me change it", which is now the wizard rather than a link to the form.
    return argument ? { kind: "show" } : { kind: "update" };
  }
  if (word === "/update") return { kind: "update" };
  // Kept pointing at the web form: someone who asks to edit on a screen should get
  // the screen. The wizard is what /update is for.
  if (word === "/edit") return { kind: "edit" };
  if (word === "/pay" || word === "/upgrade" || word === "/plan") return { kind: "upgrade" };
  if (word === "/menu") return { kind: "menu" };
  if (word === "/help") return { kind: "help" };

  if (word === "/price" || word === "/beds" || word === "/bedrooms") {
    const field = word === "/price" ? "price" : "bedrooms";
    const range = parseRange(argument);
    if (range === null) {
      return {
        kind: "help",
        reason: `Give a range, for example: ${word} ${field === "price" ? "1500-2200" : "1-2"}`
          + `\nOr "${word} any" to stop filtering on it.`,
      };
    }
    return { kind: "patch", patch: { field, ...range } };
  }

  if (word === "/areas" || word === "/area" || word === "/districts") {
    const districts = argument.split(/[,\s]+/).map((d) => d.trim()).filter(Boolean);
    if (!districts.length) {
      return { kind: "help", reason: "Name the districts, for example: /areas SE16, SE8" };
    }
    return { kind: "patch", patch: { field: "areas", districts } };
  }

  const flag = FLAGS[word];
  if (flag) {
    const on = onOff(argument);
    if (on === null) return { kind: "help", reason: `Use ${word} on or ${word} off` };
    return { kind: "patch", patch: { field: flag, on } };
  }


  if (word === "/grant") {
    const [ref = "", plan = "", days = ""] = rest;
    const parsedDays = Number(days);
    // `days` must be present and positive. An omitted one parses as 0, which
    // would grant a plan that has already expired — accepted, recorded in
    // `payments`, and useless to the person who paid.
    if (!ref || !plan || !days || !Number.isFinite(parsedDays) || parsedDays < 1) {
      return { kind: "help", reason: "Usage: /grant <payment ref> <plan> <days>" };
    }
    return { kind: "grant", ref: ref.toUpperCase(), plan: plan.toLowerCase(), days: parsedDays };
  }

  return { kind: "help" };
}

/**
 * "1500-2200", "-2200", "1500-", "2200", "any".
 *
 * `any` clears the criterion, which needs to be sayable: without it the only way
 * to widen a search back out would be to unsubscribe and start again.
 * Returns null when the text is not a range at all.
 *
 * Exported because the wizard's price step reads the same thing a person types
 * after /price. Two parsers for "1500-2200" would eventually disagree, and the
 * disagreement would show up as a filter that means one thing when set one way and
 * something else when set the other.
 */
export function parseRange(text: string): { min?: number; max?: number } | null {
  const value = text.replace(/[£,\s]/g, "");
  if (!value) return null;
  if (["any", "all", "none", "-"].includes(value.toLowerCase())) return {};

  const match = /^(\d+)?(?:[-–—to]+(\d+)?)?$/i.exec(value);
  if (!match) return null;
  const [, low, high] = match;
  if (low === undefined && high === undefined) return null;
  // A bare number is a ceiling: "/price 2000" means "no more than £2000", which
  // is what someone typing one number means by it.
  if (low !== undefined && high === undefined && !/[-–—]|to/i.test(value)) {
    return { max: Number(low) };
  }
  return {
    ...(low !== undefined && { min: Number(low) }),
    ...(high !== undefined && { max: Number(high) }),
  };
}

function onOff(text: string): boolean | null {
  const value = text.trim().toLowerCase();
  if (["on", "yes", "y", "true", "1", "да"].includes(value)) return true;
  if (["off", "no", "n", "false", "0", "нет"].includes(value)) return false;
  return null;
}

export const COMMAND_HELP = [
  "/current — the filter I'm using for you",
  "/update — set it up again, step by step",
  "",
  "Quick changes, if you'd rather not go through the steps:",
  "/price 1500-2200 · /price 2000 · /price any",
  "/beds 1-2",
  "/areas SE16, SE8",
  "/pets on · /bills on · /direct on",
  "/edit — change everything on one screen",
  "",
  "/pay — 2 weeks of alerts",
  "/stop — delete my filter and stop the messages",
].join("\n");
