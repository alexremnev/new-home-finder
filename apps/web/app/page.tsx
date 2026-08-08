// The landing page and the form.
//
// The district list comes from the database rather than from a constant, so the
// page can only offer what is actually being collected. Offering a district the
// worker does not visit produces a subscription that waits for ever, and the
// person has no way to tell the difference between "nothing matched" and "nothing
// was ever looked at".

import { query } from "@/lib/db";
import { FURNISHED, PROPERTY_TYPES } from "@/lib/criteria";
import { SubscribeForm } from "./form";

export const dynamic = "force-dynamic";

async function districts(): Promise<string[]> {
  try {
    const rows = await query<{ code: string }>(
      `SELECT DISTINCT l.code
         FROM source_locations sl
         JOIN locations l ON l.id = sl.location_id
         JOIN sources s   ON s.key = sl.source_key AND s.enabled
        WHERE sl.enabled
        ORDER BY l.code`,
    );
    return rows.map((r) => r.code.toUpperCase());
  } catch {
    // The page still renders and says so, rather than showing a stack trace to
    // someone who only wanted to sign up.
    return [];
  }
}

export default async function Page() {
  const codes = await districts();

  return (
    <main>
      <h1 style={{ fontSize: "1.6rem", marginBottom: "0.25rem" }}>London Rent Alerts</h1>
      <p style={{ color: "#555", marginTop: 0 }}>
        New rental listings in Telegram, minutes after they appear. Only what comes on the
        market from the moment you subscribe — never a backlog.
      </p>

      {codes.length === 0 ? (
        <p style={{ background: "#fff4f4", padding: "0.75rem", borderRadius: 6 }}>
          No districts are being covered right now, so there is nothing to subscribe to yet.
        </p>
      ) : (
        <SubscribeForm
          districts={codes}
          propertyTypes={[...PROPERTY_TYPES]}
          furnished={[...FURNISHED]}
        />
      )}

      <p style={{ color: "#777", fontSize: "0.85rem", marginTop: "2rem" }}>
        Sending <em>/stop</em> to the bot deletes your filter and stops the messages
        immediately. We store the criteria you choose and your Telegram chat id, nothing
        else.
      </p>
    </main>
  );
}
