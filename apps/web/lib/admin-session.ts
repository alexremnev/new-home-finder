const COOKIE = "admin_session";

const TTL_MS = 8 * 60 * 60 * 1000;

function secret(): string {
  const value = process.env.ADMIN_SESSION_SECRET;

  if (!value || value.length < 32) {
    throw new Error(
      "ADMIN_SESSION_SECRET is not set, or is shorter than 32 characters. " +
        "Generate one with: node -e \"console.log(require('crypto').randomBytes(32).toString('hex'))\"",
    );
  }
  return value;
}

async function sign(message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret()),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message));
  return [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function sameString(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i += 1) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

export async function issueSession(): Promise<{ name: string; value: string; maxAge: number }> {
  const expires = Date.now() + TTL_MS;
  return {
    name: COOKIE,
    value: `${expires}.${await sign(String(expires))}`,
    maxAge: Math.floor(TTL_MS / 1000),
  };
}

export async function sessionIsValid(cookieValue: string | undefined): Promise<boolean> {
  if (!cookieValue) return false;
  const [expires, mac] = cookieValue.split(".");
  if (!expires || !mac) return false;
  const at = Number(expires);
  if (!Number.isFinite(at) || at < Date.now()) return false;

  return sameString(mac, await sign(expires));
}

export const SESSION_COOKIE = COOKIE;

export const COOKIE_OPTIONS = {
  httpOnly: true,      // script cannot read it, so an XSS bug is not a login
  sameSite: "strict",  // never sent from another site, so no CSRF on admin actions
  secure: process.env.NODE_ENV === "production",
  path: "/",
} as const;
