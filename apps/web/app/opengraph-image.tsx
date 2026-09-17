import { ImageResponse } from "next/og";

// The card a link to the site unfurls into, in Telegram, WhatsApp, Slack and
// every search result that shows one. Drawn here rather than shipped as a file
// so it cannot drift from the wording on the page.
export const runtime = "edge";
export const alt = "London Home Finder — new London rentals the moment they list";
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

export default function Image() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          padding: "72px 80px",
          background: "linear-gradient(140deg, #4f9aa4 0%, #2f6f7a 55%, #16304e 100%)",
          color: "#fff",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18, opacity: 0.9 }}>
          <svg width="56" height="56" viewBox="0 0 64 64">
            <circle cx="32" cy="32" r="32" fill="#ffffff" />
            <path d="M9 31 L32 11 L55 31" fill="none" stroke="#e08a3c" strokeWidth="6" />
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
          <span style={{ fontSize: 30, fontWeight: 700, letterSpacing: 1 }}>
            LONDON HOME FINDER
          </span>
        </div>

        <div style={{ fontSize: 68, fontWeight: 800, lineHeight: 1.1, marginTop: 40 }}>
          Every new London rental,
          <br />
          minutes after it lists.
        </div>

        <div style={{ fontSize: 30, marginTop: 28, opacity: 0.9 }}>
          Rightmove · Zoopla · OpenRent — one alert, in Telegram or WhatsApp
        </div>
      </div>
    ),
    size,
  );
}
