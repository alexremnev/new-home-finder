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

/** One inline keyboard button. `callback_data` is capped at 64 bytes by Telegram. */
export type Button = { text: string; callback_data?: string; url?: string };
export type Keyboard = Button[][];

export type Update = {
  update_id?: number;
  message?: {
    message_id?: number;
    chat?: { id?: number | string };
    from?: { id?: number | string };
    text?: string;
  };
  // Arrives only if the webhook was registered with `callback_query` in
  // `allowed_updates`. Registering with just `["message"]` — as the cheatsheet
  // did before the wizard existed — means button taps never reach us at all, and
  // the failure is silent on both ends.
  callback_query?: {
    id?: string;
    data?: string;
    from?: { id?: number | string };
    message?: {
      message_id?: number;
      chat?: { id?: number | string };
    };
  };
};

type Payload = Record<string, unknown>;

async function call(method: string, payload: Payload): Promise<boolean> {
  const token = process.env.TELEGRAM_TOKEN;
  if (!token) throw new Error("TELEGRAM_TOKEN is not set");
  const response = await fetch(`${API}/bot${token}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    // Logged rather than thrown: every caller is answering a person, and a failed
    // courtesy reply must not turn into a 500 that makes Telegram retry the whole
    // update. The body is where Telegram puts the reason.
    console.error("telegram call failed", {
      method,
      status: response.status,
      body: await response.text().catch(() => ""),
    });
  }
  return response.ok;
}

export async function sendMessage(
  chatId: string,
  text: string,
  keyboard?: Keyboard,
): Promise<boolean> {
  return call("sendMessage", {
    chat_id: chatId,
    text,
    disable_web_page_preview: true,
    ...(keyboard && { reply_markup: { inline_keyboard: keyboard } }),
  });
}

/**
 * The message id, so a keyboard can be edited in place afterwards.
 *
 * Separate from `sendMessage` because only the wizard needs the id, and making
 * every caller handle a nullable number to get a boolean's worth of information
 * would be worse than one extra function.
 */
export async function sendMessageReturningId(
  chatId: string,
  text: string,
  keyboard?: Keyboard,
): Promise<string | null> {
  const token = process.env.TELEGRAM_TOKEN;
  if (!token) throw new Error("TELEGRAM_TOKEN is not set");
  const response = await fetch(`${API}/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      chat_id: chatId,
      text,
      disable_web_page_preview: true,
      ...(keyboard && { reply_markup: { inline_keyboard: keyboard } }),
    }),
  });
  if (!response.ok) {
    console.error("telegram sendMessage failed", {
      status: response.status,
      body: await response.text().catch(() => ""),
    });
    return null;
  }
  const body = (await response.json().catch(() => null)) as
    | { result?: { message_id?: number } }
    | null;
  const id = body?.result?.message_id;
  return id === undefined ? null : String(id);
}

/**
 * Rewrite the message a keyboard is attached to.
 *
 * This is how the wizard advances: one message, edited five times, rather than
 * five messages each leaving a live keyboard behind. An old keyboard is not
 * merely untidy — it stays tappable, and a tap on step 2's buttons after step 4
 * has been answered is a state machine going backwards.
 *
 * Returns false when the edit was rejected, which the caller treats as "send a
 * fresh message instead": Telegram refuses an edit whose text and keyboard are
 * both unchanged, and refuses one on a message too old to touch.
 */
export async function editMessageText(
  chatId: string,
  messageId: string,
  text: string,
  keyboard?: Keyboard,
): Promise<boolean> {
  return call("editMessageText", {
    chat_id: chatId,
    message_id: Number(messageId),
    text,
    disable_web_page_preview: true,
    ...(keyboard && { reply_markup: { inline_keyboard: keyboard } }),
  });
}

/**
 * Remove one of our own messages.
 *
 * Used to keep exactly one live wizard prompt in the chat. Telegram allows a bot to
 * delete its own messages for 48 hours, which is far longer than a wizard lives, and
 * a failure is ignored: a leftover prompt is untidy, not broken.
 */
export async function deleteMessage(chatId: string, messageId: string): Promise<boolean> {
  return call("deleteMessage", { chat_id: chatId, message_id: Number(messageId) });
}


/** Take the keyboard away, leaving the text. Used when a wizard is finished or abandoned. */
export async function clearKeyboard(chatId: string, messageId: string): Promise<boolean> {
  return call("editMessageReplyMarkup", {
    chat_id: chatId,
    message_id: Number(messageId),
    reply_markup: { inline_keyboard: [] },
  });
}

/**
 * Acknowledge a button tap.
 *
 * Not optional and not cosmetic: until this is called Telegram shows a loading
 * indicator on the button, and after a few seconds the client decides the tap
 * failed. It is answered even on the paths that reject the tap, because "nothing
 * happened" and "the app is broken" look identical to the person holding the phone.
 */
export async function answerCallback(callbackId: string, text?: string): Promise<boolean> {
  return call("answerCallbackQuery", {
    callback_query_id: callbackId,
    ...(text && { text }),
  });
}

/** The command list shown in the bot's menu button. Idempotent; safe to re-send. */
export async function setMyCommands(
  commands: { command: string; description: string }[],
): Promise<boolean> {
  return call("setMyCommands", { commands });
}

/** The chat id an update came from, as a string, or null if it carries none. */
export function chatIdOf(update: Update): string | null {
  const id =
    update.message?.chat?.id ??
    update.message?.from?.id ??
    update.callback_query?.message?.chat?.id ??
    update.callback_query?.from?.id;
  return id === undefined || id === null ? null : String(id);
}
