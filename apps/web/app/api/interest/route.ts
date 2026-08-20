// "I'd want it on WhatsApp."
//
// A vote, not a subscription. It records that somebody asked for a channel that
// does not exist yet, so which one gets built first is decided by how many people
// asked rather than by which is more interesting to build.
//
// Toggling on purpose: pressing again withdraws the vote. A vote that cannot be
// taken back is a trap, and somebody who taps the wrong one should not be counted
// for ever. The response says which way it went so the button can show it.

import { NextResponse } from "next/server";

import { query } from "@/lib/db";
import { accountForToken } from "@/lib/plans";

export const dynamic = "force-dynamic";

const CHANNELS = new Set(["whatsapp", "email", "sms"]);

export async function POST(request: Request) {
  let body: { token?: string; channel?: string };
  try {
    body = (await request.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: "bad request" }, { status: 400 });
  }

  const token = String(body.token ?? "").trim();
  const channel = String(body.channel ?? "").trim().toLowerCase();
  if (!token || !CHANNELS.has(channel)) {
    return NextResponse.json({ error: "bad request" }, { status: 400 });
  }

  // Read the account without spending the token. The token's job is to connect a
  // channel; a vote must not use it up, or voting would break the thing the page
  // is actually for.
  const account = await accountForToken(token, "start").catch(() => null);
  if (!account) {
    return NextResponse.json({ error: "that link has expired" }, { status: 404 });
  }

  // The insert is the vote and its absence is the withdrawal, in one statement
  // each, so a double tap cannot leave two rows or none.
  const inserted = await query<{ user_id: number }>(
    `INSERT INTO channel_interest (user_id, channel) VALUES ($1, $2)
     ON CONFLICT (user_id, channel) DO NOTHING
     RETURNING user_id`,
    [account.user_id, channel],
  );

  if (inserted.length > 0) return NextResponse.json({ wanted: true });

  await query(`DELETE FROM channel_interest WHERE user_id = $1 AND channel = $2`, [
    account.user_id,
    channel,
  ]);
  return NextResponse.json({ wanted: false });
}
