"use client";

// The form. One page, and the setup path for every channel.
//
// ── why one page and no wizard ────────────────────────────────────────────────
//
// The bot had a six-step wizard, and it worked, but it could only ever work in
// Telegram: the state lived in `wizard_sessions`, the questions were inline
// keyboards, and none of that transfers to WhatsApp or email. A form is the one
// setup surface every channel can link to, so the channel becomes a delivery choice
// rather than a second implementation of the same questions.
//
// ── why six questions and not fifteen ────────────────────────────────────────
//
// There was an "advanced filters" section holding move-in date, property type,
// bills, landlord-direct and minimum tenancy. It is gone. Every one of those is a
// field most people leave alone, and the cost of offering them was that the two
// that decide almost every search — where, and how much — arrived buried in a page
// of things to skip.
//
// The criteria model still understands them all, so nothing was lost from the
// filter; they simply are not asked here. If one turns out to be wanted, it comes
// back as a question, not as an accordion.
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

// The rent slider's range, which is not the filter's range.
//
// `criteria` accepts £300–£20,000, because somebody renting a house in Mayfair
// exists. A slider spanning that would put every ordinary London rent inside the
// first tenth of the track, where a pixel is £40 and the control is useless. So the
// track stops at £5,000 and the last notch means "no upper limit" — which covers
// the long tail honestly rather than pretending it is not there.
const RENT_MIN = 300;
const RENT_MAX = 5000;
const RENT_STEP = 50;

/** How many areas to offer as one-tap examples above the field. */
const QUICK = 4;

const money = (value: number) => "£" + value.toLocaleString("en-GB");

export function SubscribeForm({
  districts, maxDistricts, propertyTypes: _types, furnished, names = {},
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<string[]>([]);
  const [typed, setTyped] = useState("");
  const [areaError, setAreaError] = useState<string | null>(null);
  // Neighbourhood first because it is the question people can answer without
  // looking anything up. A postcode district is something you know or you don't,
  // and asking for one first reads as a demand for information rather than a
  // question about where you want to live.
  const [mode, setMode] = useState<"name" | "postcode">("name");
  const [rentMin, setRentMin] = useState(RENT_MIN);
  const [rentMax, setRentMax] = useState(RENT_MAX);
  // Set once the subscription exists. Until then there is nothing to connect a
  // channel to; afterwards the token is the only thing standing between this page
  // and a live filter, which is why it is held in memory and not in the URL.
  const [link, setLink] = useState<string | null>(null);

  // Two lists, never one. A single list mixing "Canary Wharf · E14" with a bare
  // "SE8" asks somebody to hold two different ideas of what a place is at the same
  // time, and the mixed entries look like an oversight rather than a choice.
  const named = neighbourhoodNames(names, districts);
  const options =
    mode === "name" ? named : [...districts].sort().map((code) => ({ code, name: code }));

  const nameOf = (code: string) =>
    named.find((one) => one.code === code)?.name ?? code;
  const labelOf = (code: string) => (mode === "name" ? nameOf(code) : code);

  const quick = options.filter((one) => !chosen.includes(one.code)).slice(0, QUICK);

  function add(code: string) {
    setAreaError(null);
    if (chosen.includes(code)) return;
    if (chosen.length >= maxDistricts) {
      setAreaError(`Your plan covers ${maxDistricts} areas. Remove one to add another.`);
      return;
    }
    setChosen((current) => [...current, code]);
    setTyped("");
  }

  function remove(code: string) {
    setAreaError(null);
    setChosen((current) => current.filter((one) => one !== code));
  }

  /**
   * Resolve whatever was typed against the list on screen.
   *
   * A datalist is a suggestion, not a constraint — the field still accepts free
   * text, and a browser that ignores the list entirely is within its rights. So the
   * value is matched against the options rather than trusted, and a code typed in
   * neighbourhood mode is accepted too: refusing "E14" because the radio says
   * "neighbourhood" would be pedantry.
   */
  function commitTyped() {
    const text = typed.trim();
    if (!text) return;
    const wanted = text.toLowerCase();
    const hit =
      options.find((one) => one.name.toLowerCase() === wanted) ??
      options.find((one) => one.code.toLowerCase() === wanted) ??
      options.find((one) => one.name.toLowerCase().startsWith(wanted));
    if (!hit) {
      setAreaError(`I don't know "${text}" — pick one from the list.`);
      return;
    }
    add(hit.code);
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

    // Only bounds that mean something are sent. A slider left at either end is not
    // a limit of £300 or £5,000, it is the absence of one, and sending it as a
    // number would quietly exclude the listings beyond it.
    if (rentMin > RENT_MIN) payload.price_min = String(rentMin);
    else delete payload.price_min;
    if (rentMax < RENT_MAX) payload.price_max = String(rentMax);
    else delete payload.price_max;

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

  if (link) return <AllDone url={link} token={tokenOf(link)} />;

  return (
    <form onSubmit={submit}>
      <fieldset>
        <legend>How would you like to search?</legend>
        <div className="choices">
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
                  setAreaError(null);
                  setTyped("");
                }}
              />
              {text}
            </label>
          ))}
        </div>
        {named.length === 0 && mode === "name" && (
          <p className="hint">No area names yet — search by postcode for now.</p>
        )}
      </fieldset>

      <div>
        <label htmlFor="area" className="field-label">
          {mode === "name" ? "Which neighbourhood?" : "Which postcode district?"}
        </label>
        {chosen.map((code) => (
          <input key={code} type="hidden" name="districts" value={code} />
        ))}

        {quick.length > 0 && chosen.length < maxDistricts && (
          <div className="quick">
            {quick.map((one) => (
              <span
                key={one.code}
                role="button"
                tabIndex={0}
                onClick={() => add(one.code)}
                onKeyDown={(event) => event.key === "Enter" && add(one.code)}
                className="chip"
              >
                + {one.name}
              </span>
            ))}
          </div>
        )}

        <input
          id="area"
          type="text"
          list="area-list"
          value={typed}
          autoComplete="off"
          placeholder={mode === "name" ? "Start typing, e.g. Canary Wharf" : "e.g. E14"}
          onChange={(event) => {
            setTyped(event.target.value);
            setAreaError(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              // Otherwise Enter here submits the form with no areas chosen, which
              // reads as the form rejecting itself.
              event.preventDefault();
              commitTyped();
            }
          }}
          onBlur={commitTyped}
        />
        {/* A datalist rather than a select: it is one control that both drops down
            and filters as you type, and on a phone the platform turns it into a
            picker. A 240-entry select does neither. */}
        <datalist id="area-list">
          {options
            .filter((one) => !chosen.includes(one.code))
            .map((one) => (
              <option key={one.code} value={one.name} />
            ))}
        </datalist>

        <p className="hint">
          Up to {maxDistricts} areas. Pick from the list or start typing.
        </p>

        {chosen.length > 0 && (
          <div className="chips">
            {chosen.map((code) => (
              <span
                key={code}
                role="button"
                tabIndex={0}
                title="Remove"
                onClick={() => remove(code)}
                onKeyDown={(event) => event.key === "Enter" && remove(code)}
                className="chip on"
              >
                {labelOf(code)} ✕
              </span>
            ))}
          </div>
        )}
        {areaError && <p className="error">{areaError}</p>}
      </div>

      <div>
        <span className="field-label">Rent per month</span>
        <div className="slider">
          <div className="slider-value">
            {rentMin === RENT_MIN && rentMax === RENT_MAX
              ? "Any rent"
              : `${money(rentMin)} — ${rentMax === RENT_MAX ? money(RENT_MAX) + "+" : money(rentMax)}`}
          </div>
          <div className="slider-row">
            <span>From</span>
            <input
              type="range"
              min={RENT_MIN}
              max={RENT_MAX}
              step={RENT_STEP}
              value={rentMin}
              aria-label="Lowest rent"
              onChange={(event) => {
                const value = Number(event.target.value);
                // Clamped rather than swapped: dragging past the other thumb should
                // push it, not silently reverse which one you are holding.
                setRentMin(Math.min(value, rentMax));
              }}
            />
          </div>
          <div className="slider-row">
            <span>To</span>
            <input
              type="range"
              min={RENT_MIN}
              max={RENT_MAX}
              step={RENT_STEP}
              value={rentMax}
              aria-label="Highest rent"
              onChange={(event) => {
                const value = Number(event.target.value);
                setRentMax(Math.max(value, rentMin));
              }}
            />
          </div>
        </div>
        <p className="hint">
          {money(RENT_MAX)}+ means no upper limit.
        </p>
      </div>

      <label>
        <span>Bedrooms</span>
        <input type="number" name="bedrooms_min" placeholder="from" min={0} max={10} />{" "}
        <input type="number" name="bedrooms_max" placeholder="to" min={0} max={10} />
        <p className="hint">0 includes studios. Leave either blank for no limit.</p>
      </label>

      <label>
        <span>Bathrooms</span>
        <input type="number" name="bathrooms_min" placeholder="from" min={0} max={10} />{" "}
        <input type="number" name="bathrooms_max" placeholder="to" min={0} max={10} />
        <p className="hint">
          Listings that do not state it are still sent — most do not state it.
        </p>
      </label>

      <fieldset>
        <legend>Furnishing</legend>
        <div className="choices">
          {furnished.map((option) => (
            <label key={option}>
              <input type="checkbox" name="furnished" value={option} /> {option}
            </label>
          ))}
        </div>
        <p className="hint">Nothing ticked means any.</p>
      </fieldset>

      <label>
        <span>Pets</span>
        <span className="choices">
          <label>
            <input type="checkbox" name="pets_allowed" /> Must allow pets
          </label>
        </span>
        <p className="hint">
          Only leaves out listings that say pets are not allowed. Silence still comes
          through.
        </p>
      </label>

      {error && <p className="error">{error}</p>}

      <button type="submit" disabled={busy || chosen.length === 0}>
        {busy ? "One moment…" : "All done — let's go"}
      </button>
      {chosen.length === 0 && <p className="hint">Add at least one area first.</p>}
    </form>
  );
}

/** The start token out of the bot link, for the interest vote to attach to. */
function tokenOf(url: string): string {
  const match = /[?&]start=([^&]+)/.exec(url);
  return match?.[1] ? decodeURIComponent(match[1]) : "";
}

/**
 * Saved, and where it can go.
 *
 * One action, because there is only one thing to do next and a second button would
 * make somebody choose between "connect" and something that is not connecting.
 *
 * The vote underneath is not an action — it is a question, and it is asked here
 * because this is the one moment somebody has just done the work and can see what
 * they get for another channel existing. Asked on the landing page it would be a
 * survey from a stranger.
 */
function AllDone({ url, token }: { url: string; token: string }) {
  const [wanted, setWanted] = useState<Record<string, boolean>>({});
  const [failed, setFailed] = useState(false);

  async function vote(channel: string) {
    // Flipped before the request and rolled back on failure: the press has to feel
    // immediate, and a vote is not worth a spinner.
    const next = !wanted[channel];
    setWanted((current) => ({ ...current, [channel]: next }));
    setFailed(false);
    try {
      const response = await fetch("/api/interest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token, channel }),
      });
      if (!response.ok) throw new Error("rejected");
      const body = (await response.json()) as { wanted?: boolean };
      if (typeof body.wanted === "boolean") {
        setWanted((current) => ({ ...current, [channel]: body.wanted as boolean }));
      }
    } catch {
      setWanted((current) => ({ ...current, [channel]: !next }));
      setFailed(true);
    }
  }

  return (
    <div className="panel">
      <div className="done-tick">✓</div>
      <h1>All done</h1>
      <p className="lede">
        Your search is saved. Connect Telegram and the alerts start — only listings
        posted from that moment on, never a backlog.
      </p>

      <a href={url} className="cta">
        Connect to Telegram
      </a>

      <div className="vote">
        <h2>Would you rather get these somewhere else?</h2>
        <p className="hint">
          Both are on the way. Tell us which to build first — tap to vote, tap again
          to take it back.
        </p>
        <div className="vote-row">
          <button
            type="button"
            className={wanted.whatsapp ? "vote-button voted" : "vote-button"}
            onClick={() => vote("whatsapp")}
            aria-pressed={Boolean(wanted.whatsapp)}
          >
            <span className="heart">💬</span>
            {wanted.whatsapp ? "WhatsApp — counted!" : "I'd want WhatsApp"}
          </button>
          <button
            type="button"
            className={wanted.email ? "vote-button voted" : "vote-button"}
            onClick={() => vote("email")}
            aria-pressed={Boolean(wanted.email)}
          >
            <span className="heart">{wanted.email ? "❤️" : "🤍"}</span>
            {wanted.email ? "Email — counted!" : "Email, please"}
          </button>
        </div>
        {failed && (
          <p className="error">That did not save. Your search is safe either way.</p>
        )}
      </div>
    </div>
  );
}
