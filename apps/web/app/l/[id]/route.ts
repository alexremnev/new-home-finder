import { NextResponse } from "next/server";

import { query } from "@/lib/db";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

// A WhatsApp template button is a fixed prefix plus one variable, so the three
// portals cannot be linked directly and everything goes through here. The side
// effect is the only click data this project has.
export async function GET(
  _request: Request,
  context: { params: Promise<{ id: string }> },
): Promise<Response> {
  const { id } = await context.params;
  const listingId = Number(id);
  if (!Number.isInteger(listingId) || listingId < 1) {
    return NextResponse.redirect(new URL("/", process.env.SITE_URL ?? "https://londonhomefinder.co.uk"));
  }

  const rows = await query<{ url: string }>(
    `UPDATE listings SET click_count = coalesce(click_count, 0) + 1,
                         last_clicked_at = now()
      WHERE id = $1 RETURNING url`,
    [listingId],
  );
  const url = rows[0]?.url;
  if (!url) {
    return NextResponse.redirect(new URL("/", process.env.SITE_URL ?? "https://londonhomefinder.co.uk"));
  }
  return NextResponse.redirect(url, 302);
}
