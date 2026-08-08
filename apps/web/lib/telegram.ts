// Telegram, from the web side.
//
// The worker has its own sender; this one exists because the replies to START and
// STOP are answers to something the person just did, and queueing them would put
// a scrape interval between the action and the acknowledgement.
//
// Plain text, no parse mode, previews off — the same reasoning as the worker's
// renderer: a listing line is full of MarkdownV2 syntax characters, and one
// missed escape is a rejected message rather than an ugly one.

const API = "https://api.telegram.org";

export type Update = {
  update_id?: number;
  message?: {
    chat?: { id?: number | string };
    from?: { id?: number | string };
    text?: string;
  };
};

export async function sendMessage(chatId: string, text: string): Promise<boolean> {
  const token = process.env.TELEGRAM_TOKEN;
  if (!token) throw new Error("TELEGRAM_TOKEN is not set");
  const response = await fetch(`${API}/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      disable_web_page_preview: true,
    }),
  });
  return response.ok;
}

/** The chat id an update came from, as a string, or null if it carries none. */
export function chatIdOf(update: Update): string | null {
  const id = update.message?.chat?.id ?? update.message?.from?.id;
  return id === undefined || id === null ? null : String(id);
}

export type Command = { kind: "start"; token: string | null } | { kind: "stop" } | { kind: "other" };

// Case-insensitive, and every wording a person actually uses. Someone who types
// "СТОП" has withdrawn consent just as clearly as someone who types "/stop", and
// answering them with a help message instead of stopping is not a defensible
// reading of it.
const STOP_WORDS = new Set([
  "/stop", "stop", "стоп", "unsubscribe", "отписаться", "отписка", "/unsubscribe",
]);

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
  return { kind: "other" };
}
