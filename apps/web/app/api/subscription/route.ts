// Reading and replacing a filter from the edit link.
//
// The token is the only credential, so it is treated like one: single purpose,
// short-lived, and spent on use. A GET does not spend it — the page has to load
// the current filter before it can show it — but a PUT does, so a link cannot be
// replayed later by anyone who comes across it.

import { NextResponse } from "next/server";

import { enforceLimits, InvalidForm, parseForm } from "@/lib/criteria";
import { query, transaction } from "@/lib/db";
import { enabledDistricts } from "@/lib/plans";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

type Row = {
  user_id: number;
  subscription_id: number | null;
  criteria: Record<string, unknown> | null;
  plan_name: string;
  max_districts: number;
};

async function accountForToken(token: string): Promise<Row | null> {
  const rows = await query<Row>(
    `SELECT u.id AS user_id, s.id AS subscription_id, s.criteria,
            p.display_name AS plan_name, p.max_districts
       FROM user_tokens t
       JOIN users u ON u.id = t.user_id
       JOIN plans p ON p.key = u.plan
       LEFT JOIN subscriptions s ON s.user_id = u.id AND s.active
      WHERE t.token = $1 AND t.purpose = 'edit'
        AND t.used_at IS NULL AND t.expires_at > now()
      ORDER BY s.id DESC
      LIMIT 1`,
    [token],
  );
  return rows[0] ?? null;
}

export async function GET(request: Request): Promise<NextResponse> {
  const token = new URL(request.url).searchParams.get("t") ?? "";
  const account = token ? await accountForToken(token) : null;
  if (!account) return NextResponse.json({ error: "link expired" }, { status: 404 });

  return NextResponse.json({
    criteria: account.criteria ?? {},
    plan: account.plan_name,
    max_districts: account.max_districts,
    districts: await enabledDistricts(),
  });
}

export async function PUT(request: Request): Promise<NextResponse> {
  const token = new URL(request.url).searchParams.get("t") ?? "";
  const account = token ? await accountForToken(token) : null;
  if (!account) return NextResponse.json({ error: "link expired" }, { status: 404 });
  if (account.subscription_id === null) {
    return NextResponse.json({ error: "there is no active filter to change" }, { status: 409 });
  }

  let criteria;
  try {
    criteria = enforceLimits(
      parseForm((await request.json()) as Record<string, unknown>, await enabledDistricts()),
      { maxDistricts: account.max_districts },
    );
  } catch (error) {
    if (error instanceof InvalidForm) {
      return NextResponse.json({ error: error.message }, { status: 400 });
    }
    return NextResponse.json({ error: "could not read the form" }, { status: 400 });
  }

  await transaction(async (run) => {
    // Spending the token in the same transaction as the change means a replayed
    // request cannot alter the filter a second time.
    const spent = await run(
      `UPDATE user_tokens SET used_at = now()
        WHERE token = $1 AND used_at IS NULL AND expires_at > now()
        RETURNING user_id`,
      [token],
    );
    if (spent[0] === undefined) throw new InvalidForm("link expired");
    await run(
      `UPDATE subscriptions SET criteria = $1::jsonb WHERE id = $2 AND user_id = $3`,
      [JSON.stringify(criteria), account.subscription_id, account.user_id],
    );
  });

  return NextResponse.json({ ok: true, criteria });
}
