const GRAPH = "https://graph.facebook.com";
const VERSION = "v21.0";

// WhatsApp's one url button. A message carries at most one, its label is cut at
// twenty characters, and the body it sits under is shorter than a plain text
// message's — so a message that will not fit is sent as text rather than
// trimmed to suit the button.
const DISPLAY_CHARS = 20;
const INTERACTIVE_BODY = 1024;

/**
 * A link drawn as a button rather than written out in the message.
 *
 * `label` is what the button says — plain words, no emoji: twenty characters is
 * the whole budget and WhatsApp spends some of it rendering the glyph.
 */
export type WaLink = { label: string; url: string };

type Payload = Record<string, unknown>;

function textPayload(to: string, body: string, preview = false): Payload {
  return {
    messaging_product: "whatsapp",
    to,
    type: "text",
    text: { preview_url: preview, body },
  };
}

function buttonPayload(to: string, body: string, link: WaLink): Payload {
  return {
    messaging_product: "whatsapp",
    to,
    type: "interactive",
    interactive: {
      type: "cta_url",
      body: { text: body },
      action: {
        name: "cta_url",
        parameters: { display_text: link.label.slice(0, DISPLAY_CHARS), url: link.url },
      },
    },
  };
}

async function post(payload: Payload, phoneId: string, token: string): Promise<boolean> {
  const response = await fetch(`${GRAPH}/${VERSION}/${phoneId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    console.error("whatsapp reply failed", {
      status: response.status,
      type: payload.type,
      body: await response.text().catch(() => ""),
    });
  }
  return response.ok;
}

/**
 * Free-form text, which WhatsApp permits only within 24 hours of the person's
 * last message. Every use here is a reply to something they just sent, so the
 * window is open by construction.
 *
 * `link` turns the reply into a cta_url message: the words, and the link as a
 * button under them. A url written out in a chat message is a url somebody has
 * to select and copy, and on the replies whose whole purpose is the tap — the
 * payment link, the form — that was the tap missing.
 *
 * The button is the only shape here that Meta may decline outright, so a
 * refusal falls back to the plain message with the link on the end of it. A
 * reply nobody receives is worse than a reply nobody can tap.
 */
export async function sendWhatsApp(
  to: string,
  body: string,
  link?: WaLink,
): Promise<boolean> {
  const phoneId = process.env.WA_PHONE_NUMBER_ID;
  const token = process.env.WA_ACCESS_TOKEN;
  if (!phoneId || !token) {
    // Named individually. "one of these two is unset" costs whoever reads it a
    // round of guessing, and these are set in two different places — the worker
    // on the server and this deployment — so it is easy to have one without the
    // other.
    const missing = [
      !phoneId ? "WA_PHONE_NUMBER_ID" : null,
      !token ? "WA_ACCESS_TOKEN" : null,
    ].filter(Boolean);
    console.error(
      `whatsapp reply not sent: ${missing.join(" and ")} ` +
        `${missing.length > 1 ? "are" : "is"} not set in this deployment`,
    );
    return false;
  }

  const number = to.replace(/\D/g, "");

  if (link && body.length <= INTERACTIVE_BODY) {
    if (await post(buttonPayload(number, body, link), phoneId, token)) return true;
    console.error("whatsapp button refused; sending the plain message instead");
  }

  // Either there is no button, or the button was refused, or the message is
  // longer than one can hold. The link still has to reach the person, so it
  // goes back into the body — and `preview_url` with it, because a link in the
  // text is a link worth previewing.
  const text = link && !body.includes(link.url) ? `${body}\n${link.url}` : body;
  return post(textPayload(number, text, Boolean(link)), phoneId, token);
}
