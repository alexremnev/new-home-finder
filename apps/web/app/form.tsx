"use client";

// The form.
//
// It posts JSON and then sends the person to Telegram itself rather than letting
// the browser follow a redirect. The reason is the link: it carries a one-time
// token, and a redirect would leave it in history and in the Referer header of
// whatever they open next.

import { useState } from "react";

type Props = {
  districts: string[];
  maxDistricts: number;
  propertyTypes: string[];
  furnished: string[];
};

const row: React.CSSProperties = { display: "block", marginBottom: "1rem" };
const label: React.CSSProperties = { display: "block", fontWeight: 600, marginBottom: "0.25rem" };
const input: React.CSSProperties = {
  padding: "0.4rem 0.5rem",
  border: "1px solid #ccc",
  borderRadius: 4,
  width: "8rem",
};

export function SubscribeForm({ districts, maxDistricts, propertyTypes, furnished }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Pre-ticking one district rather than all of them: the plan limit is enforced
  // on the server anyway, and a form that arrives already invalid teaches people
  // that the limit is a nuisance instead of a choice.
  const [chosen, setChosen] = useState<string[]>(districts.slice(0, maxDistricts));

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);

    const data = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = {};
    for (const key of new Set(data.keys())) {
      const values = data.getAll(key).map(String);
      payload[key] = values.length > 1 ? values : values[0];
    }

    try {
      const response = await fetch("/api/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const body = (await response.json()) as { url?: string; error?: string };
      if (!response.ok || !body.url) {
        setError(body.error ?? "Something went wrong. Please try again.");
        setBusy(false);
        return;
      }
      // `replace`, not `assign`: the token should not sit in the back button.
      window.location.replace(body.url);
    } catch {
      setError("Could not reach the server. Please try again.");
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit}>
      <fieldset style={{ ...row, border: "1px solid #eee", borderRadius: 6, padding: "0.75rem" }}>
        <legend style={{ fontWeight: 600 }}>Districts</legend>
        <p style={{ margin: "0 0 0.5rem", color: "#777", fontSize: "0.85rem" }}>
          Your plan covers {maxDistricts}.{" "}
          {chosen.length > maxDistricts && (
            <strong style={{ color: "#900" }}>Untick {chosen.length - maxDistricts} to continue.</strong>
          )}
        </p>
        {districts.map((code) => (
          <label key={code} style={{ marginRight: "1rem", display: "inline-block" }}>
            <input
              type="checkbox"
              name="districts"
              value={code}
              checked={chosen.includes(code)}
              onChange={(e) =>
                setChosen((prev) =>
                  e.target.checked ? [...prev, code] : prev.filter((c) => c !== code),
                )
              }
            />{" "}
            {code}
          </label>
        ))}
      </fieldset>

      <div style={row}>
        <span style={label}>Rent, £ per month</span>
        <input style={input} type="number" name="price_min" placeholder="from" min={0} />{" "}
        <input style={input} type="number" name="price_max" placeholder="to" min={0} />
      </div>

      <div style={row}>
        <span style={label}>Bedrooms (a studio is 0)</span>
        <input style={input} type="number" name="bedrooms_min" placeholder="from" min={0} />{" "}
        <input style={input} type="number" name="bedrooms_max" placeholder="to" min={0} />
      </div>

      <fieldset style={{ ...row, border: "1px solid #eee", borderRadius: 6, padding: "0.75rem" }}>
        <legend style={{ fontWeight: 600 }}>Property type</legend>
        <p style={{ margin: "0 0 0.5rem", color: "#777", fontSize: "0.85rem" }}>
          Leave all unticked for no preference.
        </p>
        {propertyTypes.map((type) => (
          <label key={type} style={{ marginRight: "1rem", display: "inline-block" }}>
            <input type="checkbox" name="property_types" value={type} /> {type}
          </label>
        ))}
      </fieldset>

      <fieldset style={{ ...row, border: "1px solid #eee", borderRadius: 6, padding: "0.75rem" }}>
        <legend style={{ fontWeight: 600 }}>Furnishing</legend>
        {furnished.map((option) => (
          <label key={option} style={{ marginRight: "1rem", display: "inline-block" }}>
            <input type="checkbox" name="furnished" value={option} /> {option}
          </label>
        ))}
      </fieldset>

      <fieldset style={{ ...row, border: "1px solid #eee", borderRadius: 6, padding: "0.75rem" }}>
        <legend style={{ fontWeight: 600 }}>Must be stated on the listing</legend>
        <p style={{ margin: "0 0 0.5rem", color: "#777", fontSize: "0.85rem" }}>
          Ticking one of these excludes listings that say nothing about it. That is
          deliberate — a listing that is silent about pets is not an answer you can act on.
        </p>
        <label style={{ display: "block" }}>
          <input type="checkbox" name="pets_allowed" /> Pets allowed
        </label>
        <label style={{ display: "block" }}>
          <input type="checkbox" name="bills_included" /> Bills included
        </label>
        <label style={{ display: "block" }}>
          <input type="checkbox" name="landlord_direct_only" /> Direct from the landlord, no agent
        </label>
      </fieldset>

      <div style={row}>
        <span style={label}>Longest minimum tenancy you would accept, months</span>
        <input style={input} type="number" name="min_tenancy_max_months" min={1} max={60} />
      </div>


      {error && (
        <p style={{ background: "#fff4f4", padding: "0.6rem", borderRadius: 4, color: "#900" }}>
          {error}
        </p>
      )}

      <button
        type="submit"
        disabled={busy || chosen.length === 0 || chosen.length > maxDistricts}
        style={{
          padding: "0.6rem 1.1rem",
          fontSize: "1rem",
          borderRadius: 5,
          border: "none",
          background: busy || chosen.length > maxDistricts ? "#999" : "#0a7",
          color: "#fff",
          cursor: busy ? "default" : "pointer",
        }}
      >
        {busy ? "One moment…" : "Continue in Telegram"}
      </button>
      <p style={{ color: "#777", fontSize: "0.85rem" }}>
        The next step opens the bot. Alerts start once you press Start there — that press
        is your consent, and nothing is sent before it.
      </p>
    </form>
  );
}
