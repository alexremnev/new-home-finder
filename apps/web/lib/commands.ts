export type Command =
  | { kind: "start"; token: string | null }
  | { kind: "stop" }
  | { kind: "show" }
  | { kind: "update" }
  | { kind: "upgrade" }
  | { kind: "resume" }
  | { kind: "grant"; ref: string; plan: string; days: number }
  | { kind: "menu" }
  | { kind: "help"; reason?: string };

export const BOT_MENU: { command: string; description: string }[] = [
  { command: "start", description: "Start a new search" },
  { command: "update", description: "Update my search" },
  { command: "current", description: "Show my current criteria" },
  { command: "pay", description: "Full access" },
  { command: "stop", description: "Delete my filter and stop" },
];

const STOP_WORDS = new Set([
  "/stop", "stop", "стоп", "unsubscribe", "отписаться", "отписка", "/unsubscribe",
]);

export function parseCommand(text: string | undefined): Command {
  const trimmed = (text ?? "").trim();
  const lower = trimmed.toLowerCase();

  if (STOP_WORDS.has(lower)) return { kind: "stop" };

  if (lower === "/start" || lower.startsWith("/start ") || lower.startsWith("/start@")) {
    const parts = trimmed.split(/\s+/);
    const token = parts.length > 1 ? (parts[1] ?? "").trim() : "";
    return { kind: "start", token: token || null };
  }

  const [rawWord = "", ...rest] = trimmed.split(/\s+/);
  const word = rawWord.toLowerCase().split("@")[0] ?? "";
  const argument = rest.join(" ").trim();

  if (word === "/current" || word === "/show" || word === "/settings") return { kind: "show" };
  if (word === "/filter") {

    return argument ? { kind: "show" } : { kind: "update" };
  }
  if (word === "/update") return { kind: "update" };

  if (word === "/pay" || word === "/upgrade" || word === "/plan") return { kind: "upgrade" };
  if (word === "/resume" || word === "/unpause") return { kind: "resume" };
  if (word === "/menu") return { kind: "menu" };
  if (word === "/help") return { kind: "help" };

  if (word === "/grant") {
    const [ref = "", plan = "", days = ""] = rest;
    const parsedDays = Number(days);

    if (!ref || !plan || !days || !Number.isFinite(parsedDays) || parsedDays < 1) {
      return { kind: "help", reason: "Usage: /grant <payment ref> <plan> <days>" };
    }
    return { kind: "grant", ref: ref.toUpperCase(), plan: plan.toLowerCase(), days: parsedDays };
  }

  return { kind: "help" };
}

export const COMMAND_HELP = [
  "/current — the filter I'm using for you",
  "",
  "/pay — full access",
  "/stop — delete my filter and stop the messages",
].join("\n");
