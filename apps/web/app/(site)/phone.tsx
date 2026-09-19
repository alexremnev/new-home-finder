"use client";

import { useState, type ReactNode } from "react";

import sample from "@/lib/sample-alert.json";

type Channel = "whatsapp" | "telegram";

// Each channel marks up emphasis its own way, and the phones show the message
// each one really sends — so both markups are parsed here rather than the text
// being written twice by hand. The strings come from the fixture the renderers
// are tested against.

// WhatsApp: *between asterisks* is bold.
function whatsappLine(line: string): ReactNode[] {
  return line.split(/\*([^*]+)\*/g).map((part, index) =>
    index % 2 === 1 ? <strong key={index}>{part}</strong> : part,
  );
}

const ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
};

const unescape = (text: string) =>
  text.replace(/&(amp|lt|gt|quot|#39);/g, (whole) => ENTITIES[whole] ?? whole);

// Telegram: HTML, which here is only <b> and <a href>. Parsed rather than set as
// innerHTML — the text is ours, but a renderer that grows a new tag should show
// up as a visible oddity on the page and not as an injection.
const TELEGRAM_TAGS = /<b>([^<]*)<\/b>|<a href="[^"]*">([^<]*)<\/a>/g;

function telegramLine(line: string): ReactNode[] {
  const out: ReactNode[] = [];
  let at = 0;
  for (const match of line.matchAll(TELEGRAM_TAGS)) {
    const [whole, bold, linked] = match;
    if (match.index > at) out.push(unescape(line.slice(at, match.index)));
    out.push(
      bold !== undefined ? (
        <strong key={match.index}>{unescape(bold)}</strong>
      ) : (
        <span className="tg-link" key={match.index}>
          {unescape(linked ?? "")}
        </span>
      ),
    );
    at = match.index + whole.length;
  }
  if (at < line.length) out.push(unescape(line.slice(at)));
  return out;
}

function StatusBar({ tone }: { tone: Channel }) {
  return (
    <div className={`ios-status ios-status-${tone}`}>
      <span className="ios-time">9:41</span>
      <span className="ios-icons" aria-hidden="true">
        <svg viewBox="0 0 18 12" className="ios-signal">
          <rect x="0" y="8" width="3" height="4" rx="1" />
          <rect x="5" y="5.5" width="3" height="6.5" rx="1" />
          <rect x="10" y="3" width="3" height="9" rx="1" />
          <rect x="15" y="0.5" width="3" height="11.5" rx="1" />
        </svg>
        <svg viewBox="0 0 16 12" className="ios-wifi">
          <path d="M8 10.9 6.1 8.7a2.6 2.6 0 0 1 3.8 0z" />
          <path d="M2.7 5.1a7.8 7.8 0 0 1 10.6 0" fill="none" strokeWidth="1.6"
                strokeLinecap="round" />
          <path d="M4.6 7.3a5.2 5.2 0 0 1 6.8 0" fill="none" strokeWidth="1.6"
                strokeLinecap="round" />
        </svg>
        <svg viewBox="0 0 26 12" className="ios-battery">
          <rect x="0.5" y="0.5" width="21" height="11" rx="3" fill="none" strokeWidth="1" />
          <rect x="2" y="2" width="16" height="8" rx="1.8" />
          <path d="M23.5 4.2v3.6a2 2 0 0 0 0-3.6z" />
        </svg>
      </span>
    </div>
  );
}

function ChatHead({ channel }: { channel: Channel }) {
  return (
    <div className="chat-head">
      <span className="chat-back" aria-hidden="true">
        ‹
      </span>
      <img className="chat-avatar" src="/logo-mark.png" alt="" />
      <span className="chat-who">
        London Home Finder
        <small>{channel === "whatsapp" ? "online" : "bot"}</small>
      </span>
      <span className="chat-call" aria-hidden="true">
        {channel === "whatsapp" ? (
          <svg viewBox="0 0 20 20">
            <path d="M4 3h4l2 4-2 1a8 8 0 0 0 4 4l1-2 4 2v4a1 1 0 0 1-1 1A15 15 0 0 1 3 4a1 1 0 0 1 1-1z" />
          </svg>
        ) : (
          <svg viewBox="0 0 20 20">
            <circle cx="4" cy="10" r="1.6" />
            <circle cx="10" cy="10" r="1.6" />
            <circle cx="16" cy="10" r="1.6" />
          </svg>
        )}
      </span>
    </div>
  );
}

function WhatsAppChat() {
  const lines = sample.whatsapp.split("\n");
  const link = lines[lines.length - 1] ?? "";

  return (
    <div className="phone-screen screen-whatsapp">
      <div className="chat-top chat-top-whatsapp">
        <StatusBar tone="whatsapp" />
        <ChatHead channel="whatsapp" />
      </div>

      <div className="chat-body">
        <div className="bubble">
          {/* Swap this file for another photograph to change the picture. */}
          <img className="bubble-photo" src="/sample-flat.png" alt="" />
          <div className="bubble-text">
            {lines.slice(0, -1).map((line, index) => (
              <p key={index}>{whatsappLine(line)}</p>
            ))}
            <span className="bubble-link">{link}</span>
          </div>
          <span className="bubble-time">19:42</span>
        </div>
      </div>

      <div className="chat-compose" aria-hidden="true">
        <span className="chat-input">Message</span>
        <span className="chat-mic">
          <svg viewBox="0 0 20 20">
            <path d="M10 3a2.2 2.2 0 0 1 2.2 2.2v4.3a2.2 2.2 0 0 1-4.4 0V5.2A2.2 2.2 0 0 1 10 3z" />
            <path d="M5.5 9.4a4.5 4.5 0 0 0 9 0M10 14v3" fill="none" strokeWidth="1.5"
                  strokeLinecap="round" />
          </svg>
        </span>
      </div>
    </div>
  );
}

function TelegramChat() {
  const lines = sample.telegram.split("\n");
  const link = lines[lines.length - 1] ?? "";

  return (
    <div className="phone-screen screen-telegram">
      <div className="chat-top chat-top-telegram">
        <StatusBar tone="telegram" />
        <ChatHead channel="telegram" />
      </div>

      <div className="chat-body">
        <div className="bubble">
          <div className="bubble-text">
            {lines.slice(0, -1).map((line, index) => (
              <p key={index}>{telegramLine(line)}</p>
            ))}
            <span className="bubble-link">{link}</span>
          </div>

          {/* Telegram makes its own card from the link rather than being sent a
              photograph, which is why the picture sits below the text here and
              above it on WhatsApp. */}
          <div className="tg-preview">
            <span className="tg-preview-site">Rightmove</span>
            <img className="tg-preview-photo" src="/sample-flat.png" alt="" />
          </div>
          <span className="bubble-time">19:42</span>
        </div>

        {/* What the bot actually attaches to a listing on Telegram. */}
        <div className="tg-keys" aria-hidden="true">
          <span>Ignore</span>
        </div>
      </div>

      <div className="chat-compose chat-compose-telegram" aria-hidden="true">
        <span className="chat-input">Message</span>
        <span className="chat-mic chat-mic-telegram">
          <svg viewBox="0 0 20 20">
            <path d="M10 3a2.2 2.2 0 0 1 2.2 2.2v4.3a2.2 2.2 0 0 1-4.4 0V5.2A2.2 2.2 0 0 1 10 3z" />
            <path d="M5.5 9.4a4.5 4.5 0 0 0 9 0M10 14v3" fill="none" strokeWidth="1.5"
                  strokeLinecap="round" />
          </svg>
        </span>
      </div>
    </div>
  );
}

// One phone, switched between the two messengers. Two phones side by side would
// make the page twice as tall to say the same thing, and the point is that the
// same alert arrives either way.
export function PhonePreview() {
  const [channel, setChannel] = useState<Channel>("whatsapp");

  return (
    <div className="preview">
      <div
        className="switch"
        role="radiogroup"
        aria-label="Which messenger to preview"
      >
        <span className={`switch-thumb switch-thumb-${channel}`} aria-hidden="true" />
        {(["whatsapp", "telegram"] as const).map((one) => (
          <label key={one} className={channel === one ? "switch-on" : undefined}>
            <input
              type="radio"
              name="preview-channel"
              value={one}
              checked={channel === one}
              onChange={() => setChannel(one)}
            />
            {one === "whatsapp" ? "WhatsApp" : "Telegram"}
          </label>
        ))}
      </div>

      <div className="phone" aria-label={`An alert as it arrives in ${channel}`}>
        {channel === "whatsapp" ? <WhatsAppChat /> : <TelegramChat />}
      </div>
    </div>
  );
}
