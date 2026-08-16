"use client";

// The form. One page, and the setup path for every channel.
//
// ── why one page and not a wizard ────────────────────────────────────────────
//
// The bot had a six-step wizard, and it worked, but it could only ever work in
// Telegram: the state lived in `wizard_sessions`, the questions were inline
// keyboards, and none of that transfers to WhatsApp or email. A form is the one
// setup surface that every channel can link to, so the channel becomes a delivery
// choice rather than a second implementation of the same questions.
//
// Everything on one screen rather than paged, because the whole thing is nine
// fields and a paged version of nine fields is mostly Next buttons. What is
// optional is folded away instead: the four questions that matter are visible, the
// rest is behind one summary the person can ignore.
//
// ── the token and the redirect ───────────────────────────────────────────────
//
// It posts JSON and then offers a link rather than following a redirect. The link
// carries a one-time token, and a redirect would leave it in history and in the
// Referer header of whatever they open next.

import { useState } from "react";

import { readDistricts } from "../lib/districts";

type Props = {
  districts: string[];
  maxDistricts: number;
  propertyTypes: string[];
  furnished: string[];
  /** Neighbourhood name (lower case) to district code, from listings already seen. */
  names?: Record<string, string>;
};

const row: React.CSSProperties = { display: "block", marginBottom: "1.1rem" };
const label: React.CSSProperties = { display: "block", fontWeight: 600, marginBottom: "0.3rem" };
const hint: React.CSSProperties = { color: "#777", fontSize: "0.85rem", margin: "0.2rem 0 0" };
const input: React.CSSProperties = {
  padding: "0.4rem 0.5rem",
  border: "1px solid #ccc",
  borderRadius: 4,
  width: "8rem",
};
const wide: React.CSSProperties = { ...input, width: "100%", maxWidth: "28rem" };
const chip = (on: boolean): React.CSSProperties => ({
  display: "inline-block",
  padding: "0.3rem 0.6rem",
  marginRight: "0.4rem",
  marginBottom: "0.4rem",
  borderRadius: 14,
  border: on ? "1px solid #0a7" : "1px solid #ccc",
  background: on ? "#e6f7f1" : "#fff",
  cursor: "pointer",
  fontSize: "0.9rem",
});

// How many districts to offer as buttons. The feed reaches every London district,
// which is roughly 240 of them — a page of 240 buttons is not a choice, it is a
// wall. So a handful are offered and the rest are typed.
const SAMPLE = 12;

export function SubscribeForm({
  districts, maxDistricts, propertyTypes, furnished, names = {},
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [typedError, setTypedError] = useState<string | null>(null);
  // Set once the subscription exists. Until then there is nothing to connect a
  // channel to; afterwards the token is the only thing standing between this page
  // and a live filter, which is why it is held in memory and not in the URL.
  const [link, setLink] = useState<string | null>(null);

  const sample = [
    ...chosen,
    ...districts.filter((code) => !chosen.includes(code)).slice(0, SAMPLE),
  ];

  function toggle(code: string) {
    setTypedError(null);
    setChosen((current) =>
      current.includes(code)
        ? current.filter((one) => one !== code)
        : current.length >= maxDistricts
          ? current
          : [...current, code],
    );
  }

  /** Add whatever was typed — names, codes, full postcodes, mixed. */
  function addTyped() {
    if (!typed.trim()) return;
    // The same parser the bot used, imported rather than reimplemented: "Camden
    // Town" and "E11 4EG" have to mean here exactly what they meant there, and two
    // copies of that rule would drift.
    const { codes, unknown, notCovered } = readDistricts(typed, districts, names);
    const complaints: string[] = [];
    if (unknown.length) complaints.push(`I don't know: ${unknown.join(", ")}`);
    if (notCovered.length) complaints.push(`not covered yet: ${notCovered.join(", ")}`);

    const merged = [...chosen];
    let full = false;
    for (const code of codes) {
      if (merged.includes(code)) continue;
      if (merged.length >= maxDistricts) { full = true; continue; }
      merged.push(code);
    }
    if (full) complaints.push(`your plan covers ${maxDistricts}`);

    setChosen(merged);
    setTypedError(complaints.length ? complaints.join(" · ") : null);
    if (codes.length) setTyped("");
  }

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

    // A date turns into the window the matcher already understands. Ten days
    // either side, and it is generous on purpose: an advertised availability date
    // is a landlord's intention, not a fact, and a filter that demanded the exact
    // day would reject the same flat for being ready a week early.
    const wanted = String(data.get("available_on") ?? "").trim();
    if (wanted) {
      const day = new Date(`${wanted}T00:00:00Z`);
      if (!Number.isNaN(day.getTime())) {
        const shift = (days: number) =>
          new Date(day.getTime() + days * 86_400_000).toISOString().slice(0, 10);
        payload.available_after = shift(-10);
        payload.available_before = shift(10);
      }
      delete payload.available_on;
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
      setLink(body.url);
      setBusy(false);
    } catch {
      setError("Could not reach the server. Please try again.");
      setBusy(false);
    }
  }

  // Saved. The filter exists but has nowhere to be sent, so this screen is the
  // whole point of the channel step rather than a confirmation of it.
  if (link) return <ChannelStep url={link} />;

  return (
    <form onSubmit={submit}>
      <h1 style={{ marginBottom: "0.2rem" }}>Let&apos;s set up your search</h1>
      <p style={{ ...hint, marginBottom: "1.5rem" }}>
        Four questions, then where to send the alerts. A minute, no account.
      </p>

      <div style={row}>
        <span style={label}>Where do you want to live?</span>
        {chosen.map((code) => (
          <input key={code} type="hidden" name="districts" value={code} />
        ))}
        <input
          style={wide}
          value={typed}
          placeholder="Leytonstone, SE16, E11 4EG"
          onChange={(event) => setTyped(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              // Otherwise Enter in this field submits the whole form with no
              // districts chosen, which reads as the form rejecting itself.
              event.preventDefault();
              addTyped();
            }
          }}
          onBlur={addTyped}
        />
        <p style={hint}>
          An area name or a postcode — either works, several separated by commas.
          Up to {maxDistricts}.
        </p>
        <div style={{ marginTop: "0.5rem" }}>
          {sample.map((code) => (
            <span
              key={code}
              role="button"
              tabIndex={0}
              onClick={() => toggle(code)}
              onKeyDown={(event) => event.key === "Enter" && toggle(code)}
              style={chip(chosen.includes(code))}
            >
              {chosen.includes(code) ? `✓ ${code}` : code}
            </span>
          ))}
        </div>
        {chosen.length > 0 && (
          <p style={hint}>
            Chosen: {chosen.join(", ")} ({chosen.length} of {maxDistricts})
          </p>
        )}
        {typedError && (
          <p style={{ ...hint, color: "#900" }}>{typedError}</p>
        )}
      </div>

      <label style={row}>
        <span style={label}>Rent per month</span>
        <input style={input} type="number" name="price_min" placeholder="from" min={0} />{" "}
        <input style={input} type="number" name="price_max" placeholder="to" min={0} />
        <p style={hint}>Leave either blank for no limit at that end.</p>
      </label>

      <label style={row}>
        <span style={label}>Bedrooms</span>
        <input style={input} type="number" name="bedrooms_min" placeholder="from" min={0} />{" "}
        <input style={input} type="number" name="bedrooms_max" placeholder="to" min={0} />
        <p style={hint}>0 includes studios.</p>
      </label>

      <fieldset
        style={{ ...row, border: "1px solid #eee", borderRadius: 6, padding: "0.75rem" }}
      >
        <legend style={label}>Furnishing</legend>
        <p style={{ ...hint, marginTop: 0, marginBottom: "0.4rem" }}>
          Tick everything you would consider. Nothing ticked means any.
        </p>
        {furnished.map((option) => (
          <label key={option} style={{ marginRight: "1rem", display: "inline-block" }}>
            <input type="checkbox" name="furnished" value={option} /> {option}
          </label>
        ))}
      </fieldset>

      <button
        type="button"
        onClick={() => setAdvanced((open) => !open)}
        style={{
          background: "none",
          border: "none",
          padding: 0,
          marginBottom: "1rem",
          color: "#0a7",
          cursor: "pointer",
          fontSize: "0.95rem",
        }}
      >
        {advanced ? "▾" : "▸"} More filters (optional)
      </button>

      {advanced && (
        <div style={{ borderLeft: "2px solid #eee", paddingLeft: "0.9rem" }}>
          <p style={{ ...hint, marginTop: 0 }}>
            All optional. A listing that does not state one of these is still sent —
            most listings leave several unstated, and excluding them would leave you
            with almost nothing.
          </p>

          <label style={row}>
            <span style={label}>Bathrooms</span>
            <input style={input} type="number" name="bathrooms_min" placeholder="from" min={0} />{" "}
            <input style={input} type="number" name="bathrooms_max" placeholder="to" min={0} />
          </label>

          <label style={row}>
            <span style={label}>Move-in date</span>
            <input style={{ ...input, width: "11rem" }} type="date" name="available_on" />
            <p style={hint}>Listings available within about ten days of it.</p>
          </label>

          <fieldset
            style={{ ...row, border: "1px solid #eee", borderRadius: 6, padding: "0.75rem" }}
          >
            <legend style={label}>Property type</legend>
            {propertyTypes.map((type) => (
              <label key={type} style={{ marginRight: "1rem", display: "inline-block" }}>
                <input type="checkbox" name="property_types" value={type} /> {type}
              </label>
            ))}
          </fieldset>

          <fieldset
            style={{ ...row, border: "1px solid #eee", borderRadius: 6, padding: "0.75rem" }}
          >
            <legend style={label}>Must say</legend>
            <p style={{ ...hint, marginTop: 0, marginBottom: "0.4rem" }}>
              These only leave out listings that say no. Silence still comes through.
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

          <label style={row}>
            <span style={label}>Longest tenancy you would sign up to (months)</span>
            <input style={input} type="number" name="min_tenancy_max_months" min={1} max={60} />
          </label>
        </div>
      )}

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
          background: busy || chosen.length === 0 ? "#999" : "#0a7",
          color: "#fff",
          cursor: busy ? "default" : "pointer",
        }}
      >
        {busy ? "One moment…" : "Save my search"}
      </button>
      {chosen.length === 0 && (
        <p style={hint}>Choose at least one area first.</p>
      )}
    </form>
  );
}

/**
 * Where to send it.
 *
 * Asked after the filter is saved rather than before, because at this point the
 * person has done the work and can see what they get for connecting; asked before,
 * it is a demand for a contact detail from a stranger.
 *
 * WhatsApp is shown and disabled rather than hidden. Hiding it would be the honest
 * option if it were never coming; naming it as not yet ready is what stops somebody
 * who only wants WhatsApp from connecting Telegram and being disappointed.
 */
function ChannelStep({ url }: { url: string }) {
  return (
    <div>
      <h1 style={{ marginBottom: "0.2rem" }}>Saved. Where should the alerts go?</h1>
      <p style={{ ...hint, marginBottom: "1.5rem" }}>
        Nothing is sent until you connect a channel below — that press is your
        consent. Only listings posted from then on are sent, not what is already on
        the market.
      </p>

      <a
        href={url}
        style={{
          display: "inline-block",
          padding: "0.7rem 1.2rem",
          borderRadius: 5,
          background: "#0a7",
          color: "#fff",
          textDecoration: "none",
          fontSize: "1rem",
        }}
      >
        Connect Telegram
      </a>
      <p style={hint}>Opens the bot. Press Start there and the alerts begin.</p>

      <div style={{ marginTop: "1.6rem" }}>
        <span
          aria-disabled="true"
          style={{
            display: "inline-block",
            padding: "0.7rem 1.2rem",
            borderRadius: 5,
            background: "#f2f2f2",
            color: "#888",
            fontSize: "1rem",
            cursor: "not-allowed",
          }}
        >
          WhatsApp — coming soon
        </span>
        <p style={hint}>
          Your filter is saved either way. Connect Telegram now and WhatsApp can be
          added to the same filter later.
        </p>
      </div>
    </div>
  );
}
