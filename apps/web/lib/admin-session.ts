// The admin session cookie.
//
// Separate from `admin.ts` because password checking needs Node's `scrypt` and this
// needs only Web Crypto — which means this half runs in middleware (edge) as well as
// in a route handler, and middleware is where the guard belongs: a page that forgets
// to call `requireAdmin` is a page that is public, and middleware makes forgetting
// impossible rather than unlikely.
//
// ── what the cookie is ───────────────────────────────────────────────────────
//
// `<expiry>.<hmac>` and nothing else. No user id, no role, no JSON. There is one
// admin, so the only fact the cookie has to carry is "this browser proved it knew
// the password, and that proof expires at this moment".
//
// The HMAC is over the expiry, so the expiry cannot be edited: moving it forward
// invalidates the signature. That is the whole mechanism, and it is why there is no
// server-side session table — nothing to expire, nothing to clean up, and no
// database round trip on every request.
//
// Not encrypted, because nothing in it is secret. Signed, because it is a claim.

const COOKIE = "admin_session";
// Eight hours: long enough to look at a dashboard across a working day, short
// enough that a browser left open on a shared machine stops being a way in.
const TTL_MS = 8 * 60 * 60 * 1000;

function secret(): string {
  const value = process.env.ADMIN_SESSION_SECRET;
  // Refused rather than defaulted. A default would be in the repository, and a
  // signing key in the repository is a signature anybody can forge.
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

/** Constant time, so a wrong signature does not reveal how much of it was right. */
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
  // Signature checked even when the expiry has passed above — order matters only
  // for cost here, not for safety, and expiry is the cheaper test.
  return sameString(mac, await sign(expires));
}

export const SESSION_COOKIE = COOKIE;

/** The attributes every set of this cookie must carry. One place, so none is missed. */
export const COOKIE_OPTIONS = {
  httpOnly: true,      // script cannot read it, so an XSS bug is not a login
  sameSite: "strict",  // never sent from another site, so no CSRF on admin actions
  secure: process.env.NODE_ENV === "production",
  path: "/",
} as const;
