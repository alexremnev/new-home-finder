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

  const account = await accountForToken(token, "start").catch(() => null);
  if (!account) {
    return NextResponse.json({ error: "that link has expired" }, { status: 404 });
  }

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
