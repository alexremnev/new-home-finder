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

import { neighbourhoodNames } from "../lib/neighbourhoods";

type Props = {
  districts: string[];
  maxDistricts: number;
  propertyTypes: string[];
  furnished: string[];
  /** Neighbourhood name (lower case) to district code, from listings already seen. */
  names?: Record<string, string>;
};

// Empty on purpose. Every rule that used to live here is in app/globals.css, and
// the reason for moving it is that inline styles cannot express a hover, a focus
// ring, or a phone-sized layout — the three things that made the page look unfinished.
const row: React.CSSProperties = {};
const label: React.CSSProperties = {};
const hint: React.CSSProperties = {};
const input: React.CSSProperties = {};
const wide: React.CSSProperties = {};

/** "chip" or "chip on" — a class, so :hover and :focus-visible are reachable. */
const chip = (on: boolean) => (on ? "chip on" : "chip");

export function SubscribeForm({
  districts, maxDistricts, propertyTypes, furnished, names = {},
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [tooMany, setTooMany] = useState(false);
  // Which of the two lists is shown. Neighbourhood first because it is the one
  // people can answer without looking anything up — a postcode district is
  // something you know or you don't, and being asked for one first reads as a
  // demand for information rather than a question about where you want to live.
  const [mode, setMode] = useState<"name" | "postcode">("name");
  // Set once the subscription exists. Until then there is nothing to connect a
  // channel to; afterwards the token is the only thing standing between this page
  // and a live filter, which is why it is held in memory and not in the URL.
  const [link, setLink] = useState<string | null>(null);

  // Two lists, never one. A single list mixing "Canary Wharf · E14" with bare
  // "SE8" asks the person to hold two different ideas of what a place is at the same
  // time, and the mixed entries look like an oversight rather than a choice.
  //
  // Names come from the gazetteer with observed names laid over it, so a place is
  // offered before the feed has ever mentioned it — otherwise the list grows as
  // listings arrive and looks like the service covers less than it does.
  const named = neighbourhoodNames(names, districts);
  const allCodes = [...districts].sort();

  const options =
    mode === "name"
      ? named
      : allCodes.map((code) => ({ code, name: code }));

  /** What to call a district on a chip, in whichever way it was being chosen. */
  const nameOf = (code: string) =>
    mode === "name" ? (named.find((one) => one.code === code)?.name ?? code) : code;

  function toggle(code: string) {
    setTooMany(false);
    setChosen((current) =>
      current.includes(code)
        ? current.filter((one) => one !== code)
        : current.length >= maxDistricts
          ? current
          : [...current, code],
    );
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

      <fieldset style={row}>
        <legend>How would you like to search?</legend>
        {(
          [
            ["name", "By neighbourhood"],
            ["postcode", "By postcode"],
          ] as const
        ).map(([value, text]) => (
          <label key={value}>
            <input
              type="radio"
              name="search_by"
              value={value}
              checked={mode === value}
              onChange={() => {
                setMode(value);
                setTooMany(false);
              }}
            />
            {text}
          </label>
        ))}
        {named.length === 0 && mode === "name" && (
          <p className="hint">
            No area names have come through yet — search by postcode for now.
          </p>
        )}
      </fieldset>

      <div style={row}>
        <label htmlFor="district" style={label}>
          {mode === "name" ? "Which neighbourhood?" : "Which postcode district?"}
        </label>
        {chosen.map((code) => (
          <input key={code} type="hidden" name="districts" value={code} />
        ))}
        <select
          id="district"
          value=""
          onChange={(event) => {
            const code = event.target.value;
            if (!code) return;
            if (chosen.length >= maxDistricts) { setTooMany(true); return; }
            toggle(code);
          }}
        >
          <option value="">
            {mode === "name" ? "Choose a neighbourhood…" : "Choose a district…"}
          </option>
          {options
            .filter((one) => !chosen.includes(one.code))
            .map((one) => (
              <option key={one.code} value={one.code}>
                {one.name}
              </option>
            ))}
        </select>
        <p className="hint">
          {mode === "name"
            ? `Up to ${maxDistricts} — add them one at a time.`
            : `The outward part only, like E14. Up to ${maxDistricts}.`}
        </p>
        {chosen.length > 0 && (
          <div className="chips">
            {chosen.map((code) => (
              <span
                key={code}
                role="button"
                tabIndex={0}
                title="Remove"
                onClick={() => toggle(code)}
                onKeyDown={(event) => event.key === "Enter" && toggle(code)}
                className="chip on"
              >
                {nameOf(code)} ✕
              </span>
            ))}
          </div>
        )}
        {tooMany && (
          <p className="error">
            Your plan covers {maxDistricts} areas. Remove one to add another.
          </p>
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
        className="disclosure"
      >
        {advanced ? "▾" : "▸"} More filters (optional)
      </button>

      {advanced && (
        <div className="more">
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
            <input style={input} type="date" name="available_on" />
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
        <p className="error">{error}</p>
      )}

      <button
        type="submit"
        disabled={busy || chosen.length === 0 || chosen.length > maxDistricts}

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
        className="cta"
      >
        Connect Telegram
      </a>
      <p style={hint}>Opens the bot. Press Start there and the alerts begin.</p>

      <div style={{ marginTop: "1.6rem" }}>
        <span
          aria-disabled="true"
          className="soon"
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
