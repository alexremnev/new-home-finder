import { TelegramMark, WhatsAppMark } from "../../logos";
import { botChat, whatsappSay } from "@/lib/plans";

export const metadata = { title: "Payment received" };

// What the WhatsApp button types into the box. A real command and not a
// pleasantry, for the reason `whatsappSay` gives: the person presses send, the
// 24-hour window reopens, and what comes back is their plan and their filter
// rather than a list of commands they could have sent instead.
const SHOW = "/current";

/**
 * Where Stripe lands somebody whose payment cleared.
 *
 * ── why there is a button here at all ─────────────────────────────────────
 *
 * They are in a browser, and everything they bought happens in a chat app. A
 * page that only says "paid" leaves the way back as the person's own problem,
 * which on a phone means finding the app and then finding the conversation.
 *
 * On WhatsApp it is more than a courtesy. The notice that brought them here
 * reaches a shut window by definition — a plan ends after a day of silence,
 * which is exactly when free-form text is refused — and tapping a url button
 * on a template sends nothing back to us, so the window is still shut when the
 * payment clears. Meta refuses the receipt with 131047, and the alerts they
 * have just paid for sit in the queue until something inbound arrives. This
 * button is one of the two things that makes something inbound arrive; the
 * other is the quick reply on the notice itself, which is the one that does
 * not depend on anybody visiting this page.
 *
 * ── why the channel comes off the query string ────────────────────────────
 *
 * Because there is nothing else to read it from: the upgrade token is deleted
 * by the webhook that grants the plan, so a `t=` here would be expired by
 * construction. Checkout knows the account and says which messenger it
 * belongs to — see `success_url`. Anything else, including nothing, falls
 * back to the text that was here before: it names `/show`, which works in
 * both apps and needs no link.
 */
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ c?: string }>;
}) {
  const { c } = await searchParams;
  const whatsapp = c === "whatsapp";
  const url = whatsapp ? whatsappSay(SHOW) : c === "telegram" ? botChat() : null;

  return (
    <div className="panel">
      <div className="done-tick">✓</div>
      <h1>Payment received</h1>
      <p className="lede">
        Your plan is extended.{" "}
        {url && whatsapp ? (
          <>
            Open WhatsApp and press send on the message waiting there — the bot
            answers with your new limits, and the alerts resume with it.
          </>
        ) : url ? (
          <>
            Open Telegram and send <strong>/show</strong> to see the new limits.
          </>
        ) : (
          <>
            Send <strong>/show</strong> to the bot to see the new limits — if it
            still shows the old plan, give it a minute and ask again.
          </>
        )}
      </p>

      {url && (
        <a href={url} className={whatsapp ? "cta cta-whatsapp" : "cta cta-telegram"}>
          {whatsapp ? <WhatsAppMark /> : <TelegramMark />} Back to{" "}
          {whatsapp ? "WhatsApp" : "Telegram"}
        </a>
      )}

      {url && whatsapp && (
        /* Said plainly, because the step is doing work the person cannot see.
           WhatsApp only hands a business a message the person actually sent,
           so a chat opened and left unsent is the same to us as this page not
           being visited at all — and the thing waiting on it is their own
           alerts. */
        <p className="hint">
          The message has to be sent, not just opened: it is what lets WhatsApp
          deliver to you again.
        </p>
      )}
    </div>
  );
}
