import type { ReactNode } from "react";

import sample from "@/lib/sample-alert.json";

// WhatsApp's own emphasis: *between asterisks* is bold. Rendered here rather
// than written as markup so the bubble shows the message exactly as the phone
// would, from the same string the bot sends.
function emphasise(line: string): ReactNode[] {
  return line.split(/\*([^*]+)\*/g).map((part, index) =>
    index % 2 === 1 ? <strong key={index}>{part}</strong> : part,
  );
}

function StatusBar() {
  return (
    <div className="ios-status">
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

// The right-hand column: what an alert looks like where it is read. The text is
// not written here — it comes from the fixture the renderer is tested against,
// so this cannot quietly become a picture of a message we no longer send.
export function PhonePreview() {
  const lines = sample.text.split("\n");
  const link = lines[lines.length - 1] ?? "";
  const body = lines.slice(0, -1);

  return (
    <div className="phone" aria-label="An alert as it arrives in WhatsApp">
      <div className="phone-screen">
        <div className="chat-top">
          <StatusBar />
          <div className="chat-head">
            <span className="chat-back" aria-hidden="true">
              ‹
            </span>
            <img className="chat-avatar" src="/logo-mark.png" alt="" />
            <span className="chat-who">
              London Home Finder
              <small>online</small>
            </span>
            <span className="chat-call" aria-hidden="true">
              <svg viewBox="0 0 20 20">
                <path d="M4 3h4l2 4-2 1a8 8 0 0 0 4 4l1-2 4 2v4a1 1 0 0 1-1 1A15 15 0 0 1 3 4a1 1 0 0 1 1-1z" />
              </svg>
            </span>
          </div>
        </div>

        <div className="chat-body">
          <div className="bubble">
            {/* Swap this file for another photograph to change the picture. */}
            <img className="bubble-photo" src="/sample-flat.png" alt="" />
            <div className="bubble-text">
              {body.map((line, index) => (
                <p key={index}>{emphasise(line)}</p>
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

        <span className="ios-bar" aria-hidden="true" />
      </div>
    </div>
  );
}
