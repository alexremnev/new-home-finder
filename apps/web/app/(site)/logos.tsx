// The two marks, drawn rather than fetched: the CSP allows no external images,
// and an emoji paper plane is not the Telegram logo — people scan for the shape
// they already know.
//
// These are simplified renderings of the official marks, at the size they are
// used (20px beside a line of text). Replace the paths with the official SVGs
// from telegram.org/brand and whatsappbrand.com if you want the exact curves.

const SIZE = 20;

export function TelegramMark() {
  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <circle cx="12" cy="12" r="12" fill="#fff" />
      <path
        fill="#229ED9"
        d="M5.3 11.7 17.1 7.1c.55-.2 1.03.13.85.96l-2.01 9.48c-.14.67-.54.83-1.1.52l-3.05-2.25-1.47 1.42c-.16.16-.3.3-.6.3l.21-3.03 5.52-4.99c.24-.21-.05-.33-.36-.12l-6.82 4.3-2.94-.92c-.64-.2-.65-.64.02-.9Z"
      />
    </svg>
  );
}

export function WhatsAppMark() {
  return (
    <svg
      width={SIZE}
      height={SIZE}
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <path
        fill="#fff"
        d="M12 2.2c-5.4 0-9.8 4.4-9.8 9.8 0 1.73.45 3.35 1.25 4.76L2.2 21.8l5.2-1.2a9.75 9.75 0 0 0 4.6 1.16c5.4 0 9.8-4.4 9.8-9.8S17.4 2.2 12 2.2Z"
      />
      <path
        fill="#25D366"
        d="M9.1 7.3c-.2-.45-.4-.46-.6-.47h-.5c-.17 0-.45.07-.69.33-.24.26-.9.88-.9 2.15 0 1.27.92 2.5 1.05 2.66.13.17 1.79 2.86 4.43 3.9 2.2.86 2.51.69 2.96.65.45-.04 1.46-.6 1.66-1.18.21-.58.21-1.07.15-1.18-.07-.1-.24-.17-.5-.3-.26-.13-1.46-.72-1.7-.8-.22-.09-.39-.13-.55.13-.17.26-.65.84-.8 1.01-.14.17-.29.19-.55.06-.26-.13-1.08-.4-2.06-1.27-.76-.68-1.28-1.5-1.43-1.76-.15-.26-.02-.4.11-.53.11-.11.26-.3.39-.45.13-.15.17-.26.26-.43.09-.17.04-.32-.02-.45-.07-.13-.54-1.3-.74-1.77Z"
      />
    </svg>
  );
}

// The badge from the favicon, at a size a header can use. Same three shapes:
// the teal disc, the orange roof, the monogram as rectangles.
export function BrandMark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <circle cx="32" cy="32" r="32" fill="#4f9aa4" />
      <path d="M9 31 L32 11 L55 31" fill="none" stroke="#e08a3c" strokeWidth="6"
            strokeLinejoin="miter" />
      <g fill="#16304e">
        <rect x="12" y="34" width="4" height="18" />
        <rect x="12" y="48" width="10" height="4" />
        <rect x="26" y="34" width="4" height="18" />
        <rect x="34" y="34" width="4" height="18" />
        <rect x="26" y="42" width="12" height="4" />
        <rect x="42" y="34" width="4" height="18" />
        <rect x="42" y="34" width="10" height="4" />
        <rect x="42" y="42" width="8" height="4" />
      </g>
    </svg>
  );
}
