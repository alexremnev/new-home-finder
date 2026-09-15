const API = "https://api.telegram.org";

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

export async function deleteMessage(chatId: string, messageId: number): Promise<boolean> {
  return call("deleteMessage", { chat_id: chatId, message_id: messageId });
}

export async function answerCallback(callbackId: string, text?: string): Promise<boolean> {
  return call("answerCallbackQuery", {
    callback_query_id: callbackId,
    ...(text && { text }),
  });
}

export async function setMyCommands(
  commands: { command: string; description: string }[],
): Promise<boolean> {
  return call("setMyCommands", { commands });
}

export function chatIdOf(update: Update): string | null {
  const id =
    update.message?.chat?.id ??
    update.message?.from?.id ??
    update.callback_query?.message?.chat?.id ??
    update.callback_query?.from?.id;
  return id === undefined || id === null ? null : String(id);
}
