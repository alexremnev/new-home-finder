const GRAPH = "https://graph.facebook.com";
const VERSION = "v21.0";

// Free-form text, which WhatsApp permits only within 24 hours of the person's
// last message. Every use here is a reply to something they just sent, so the
// window is open by construction.
export async function sendWhatsApp(to: string, body: string): Promise<boolean> {
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

  const response = await fetch(`${GRAPH}/${VERSION}/${phoneId}/messages`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({
      messaging_product: "whatsapp",
      to: to.replace(/\D/g, ""),
      type: "text",
      text: { preview_url: false, body },
    }),
  });

  if (!response.ok) {
    console.error("whatsapp reply failed", {
      status: response.status,
      body: await response.text().catch(() => ""),
    });
  }
  return response.ok;
}
