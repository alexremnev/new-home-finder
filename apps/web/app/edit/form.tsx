"use client";

// Editing reuses the same field names as the sign-up form, so both go through one
// parser on the server. Two forms with two vocabularies would mean two sets of
// validation rules drifting apart.

import { useEffect, useState } from "react";

import { FURNISHED, PROPERTY_TYPES, type Criteria } from "@/lib/criteria";

type Loaded = {
  criteria: Criteria;
  plan: string;
  max_districts: number;
  districts: string[];
};

export function EditForm() {
  const [state, setState] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [token, setToken] = useState("");

  useEffect(() => {
    const t = new URLSearchParams(window.location.search).get("t") ?? "";
    setToken(t);
    if (!t) {
      setError("This link is missing its token. Send /filter to the bot for a new one.");
      return;
    }
    fetch(`/api/subscription?t=${encodeURIComponent(t)}`)
      .then(async (r) => (r.ok ? ((await r.json()) as Loaded) : Promise.reject(await r.json())))
      .then(setState)
      .catch(() => setError("This link has expired. Send /filter to the bot for a new one."));
  }, []);

  async function save(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const data = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = {};
    for (const key of new Set(data.keys())) {
      const values = data.getAll(key).map(String);
      payload[key] = values.length > 1 ? values : values[0];
    }
    const response = await fetch(`/api/subscription?t=${encodeURIComponent(token)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = (await response.json()) as { error?: string };
    if (!response.ok) {
      setError(body.error ?? "Could not save that.");
      return;
    }
    setSaved(true);
  }

  if (error) return <p style={{ color: "#900" }}>{error}</p>;
  if (!state) return <p>Loading your filter…</p>;
  if (saved) {
    return (
      <p>
        Saved. The next matching listing will use the new filter — you can close this page.
      </p>
    );
  }

  const c = state.criteria;
  const chosen = c.areas?.postcode_districts ?? [];

  return (
    <form onSubmit={save}>
      <p style={{ color: "#555" }}>
        {state.plan}: up to {state.max_districts} district{state.max_districts === 1 ? "" : "s"}.
      </p>

      <fieldset style={{ marginBottom: "1rem" }}>
        <legend style={{ fontWeight: 600 }}>Districts</legend>
        {state.districts.map((code) => (
          <label key={code} style={{ marginRight: "1rem" }}>
            <input
              type="checkbox"
              name="districts"
              value={code}
              defaultChecked={chosen.includes(code)}
            />{" "}
            {code}
          </label>
        ))}
      </fieldset>

      <p>
        <span style={{ fontWeight: 600 }}>Rent, £ per month</span>
        <br />
        <input type="number" name="price_min" defaultValue={c.price_pcm?.min} placeholder="from" />{" "}
        <input type="number" name="price_max" defaultValue={c.price_pcm?.max} placeholder="to" />
      </p>

      <p>
        <span style={{ fontWeight: 600 }}>Bedrooms</span>
        <br />
        <input type="number" name="bedrooms_min" defaultValue={c.bedrooms?.min} placeholder="from" />{" "}
        <input type="number" name="bedrooms_max" defaultValue={c.bedrooms?.max} placeholder="to" />
      </p>

      <fieldset style={{ marginBottom: "1rem" }}>
        <legend style={{ fontWeight: 600 }}>Property type</legend>
        {PROPERTY_TYPES.map((type) => (
          <label key={type} style={{ marginRight: "1rem" }}>
            <input
              type="checkbox"
              name="property_types"
              value={type}
              defaultChecked={(c.property_types ?? []).includes(type)}
            />{" "}
            {type}
          </label>
        ))}
      </fieldset>

      <fieldset style={{ marginBottom: "1rem" }}>
        <legend style={{ fontWeight: 600 }}>Furnishing</legend>
        {FURNISHED.map((option) => (
          <label key={option} style={{ marginRight: "1rem" }}>
            <input
              type="checkbox"
              name="furnished"
              value={option}
              defaultChecked={(c.furnished ?? []).includes(option)}
            />{" "}
            {option}
          </label>
        ))}
      </fieldset>

      <fieldset style={{ marginBottom: "1rem" }}>
        <legend style={{ fontWeight: 600 }}>Must be stated on the listing</legend>
        <label style={{ display: "block" }}>
          <input type="checkbox" name="pets_allowed" defaultChecked={c.pets_allowed === true} /> Pets
          allowed
        </label>
        <label style={{ display: "block" }}>
          <input type="checkbox" name="bills_included" defaultChecked={c.bills_included === true} />{" "}
          Bills included
        </label>
        <label style={{ display: "block" }}>
          <input
            type="checkbox"
            name="landlord_direct_only"
            defaultChecked={c.landlord_direct_only === true}
          />{" "}
          Direct from the landlord
        </label>
      </fieldset>


      <button type="submit" style={{ padding: "0.6rem 1.1rem", fontSize: "1rem" }}>
        Save
      </button>
    </form>
  );
}
