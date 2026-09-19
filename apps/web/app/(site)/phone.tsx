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

function Avatar() {
  return (
    <svg className="chat-avatar" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
      <circle cx="20" cy="20" r="20" fill="#128c7e" />
      <path
        d="M11 20.5 20 12.5l9 8"
        fill="none"
        stroke="#fff"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path d="M13.5 20v8h13v-8" fill="none" stroke="#fff" strokeWidth="2.4"
            strokeLinejoin="round" />
      <rect x="18" y="22.5" width="4" height="5.5" fill="#fff" />
    </svg>
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
        <div className="chat-head">
          <Avatar />
          <span className="chat-who">
            London Home Finder Bot
            <small>Online</small>
          </span>
        </div>

        <div className="chat-body">
          <div className="bubble">
            {/* Swap this file for a photograph to change the picture. */}
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
      </div>
    </div>
  );
}
