import { query } from "./db";

export const SUPPORT_PROMPT = [
  "Sorry to hear it. Tell me what went wrong in one message and I will pass it on.",
  "",
  "Anything helps: what you expected, what arrived instead, roughly when.",
  "",
  "/cancel — never mind",
].join("\n");

export const SUPPORT_ASK_EMAIL = [
  "Thank you. What email should the reply go to?",
  "",
  "Send a dash if you would rather be answered here.",
].join("\n");

export const SUPPORT_BAD_EMAIL = [
  "That does not look like an email address.",
  "",
  "Send it again, or a dash to be answered here instead.",
].join("\n");

export const SUPPORT_DONE = [
  "✅ Your request has been received and will be reviewed within 24 hours.",
  "",
  "Your alerts carry on as normal in the meantime.",
].join("\n");

export const SUPPORT_CANCELLED = "Nothing was sent. Say /support if you change your mind.";

export const SUPPORT_TOO_LONG = [
  "That is longer than I can file — about 2000 characters is the limit.",
  "",
  "Send a shorter version and I will pass it on.",
].join("\n");

export const BODY_LIMIT = 2000;

// A dash — or anything else that plainly is not an address — means answer here.
const EMAIL = /^[^\s@]+@[^\s@.]+\.[^\s@]{2,}$/;

export type Draft = { id: number; status: "awaiting_body" | "awaiting_email" };

export async function openDraft(channel: string, address: string): Promise<Draft | null> {
  const rows = await query<{ id: string; status: string }>(
    `SELECT id, status FROM support_tickets
      WHERE channel = $1 AND address = $2
        AND status IN ('awaiting_body', 'awaiting_email')
      LIMIT 1`,
    [channel, address],
  );
  const row = rows[0];
  if (!row) return null;
  return { id: Number(row.id), status: row.status as Draft["status"] };
}

// One unfinished ticket per address is enforced by a partial unique index, so a
// second /support reuses the draft rather than starting a rival one.
export async function startDraft(
  channel: string,
  address: string,
  userId: number | null,
): Promise<void> {
  await query(
    `INSERT INTO support_tickets (user_id, channel, address, status)
     SELECT $3, $1, $2, 'awaiting_body'
      WHERE NOT EXISTS (
        SELECT 1 FROM support_tickets
         WHERE channel = $1 AND address = $2
           AND status IN ('awaiting_body', 'awaiting_email')
      )`,
    [channel, address, userId],
  );
}

export async function abandonDraft(channel: string, address: string): Promise<boolean> {
  const rows = await query<{ id: string }>(
    `UPDATE support_tickets SET status = 'abandoned'
      WHERE channel = $1 AND address = $2
        AND status IN ('awaiting_body', 'awaiting_email')
      RETURNING id`,
    [channel, address],
  );
  return rows.length > 0;
}

export async function recordBody(id: number, body: string): Promise<void> {
  await query(
    `UPDATE support_tickets SET body = $2, status = 'awaiting_email' WHERE id = $1`,
    [id, body.slice(0, BODY_LIMIT)],
  );
}

export function readEmail(text: string): string | null | "invalid" {
  const trimmed = text.trim();
  if (!trimmed || trimmed === "-" || trimmed === "—") return null;
  return EMAIL.test(trimmed) && trimmed.length <= 320 ? trimmed : "invalid";
}

export async function submit(id: number, email: string | null): Promise<void> {
  await query(
    `UPDATE support_tickets
        SET email = $2, status = 'open', submitted_at = now()
      WHERE id = $1 AND status = 'awaiting_email'`,
    [id, email],
  );
}
