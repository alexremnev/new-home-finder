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
// ── sliders, not number boxes ─────────────────────────────────────────────────
//
// Rent, bedrooms and bathrooms are all "how much", and a pair of number boxes asks
// somebody to invent a figure before they know what the market looks like. A slider
// shows the range that exists and lets them narrow it, which is the same question
// asked in a way that can be answered by dragging.
//
// ── the token and the redirect ───────────────────────────────────────────────
//
// It posts JSON and then offers a link rather than following a redirect. The link
// carries a one-time token, and a redirect would leave it in history and in the
// Referer header of whatever they open next.

import { useState } from "react";

import { neighbourhoodAreas, type Area } from "../lib/neighbourhoods";

type Props = {
  districts: string[];
  maxDistricts: number;
  propertyTypes: string[];
  furnished: string[];
  /** Neighbourhood name (lower case) to district code, from listings already seen. */
  names?: Record<string, string>;
};

// The rent track. Not the filter's range: `criteria` accepts up to £20,000 because
// somebody renting in Mayfair exists, and a track that long puts every ordinary
// London rent in its first fifth where a pixel is £80. £400–£10,000 covers what
// people actually search for, and the top notch means "no upper limit" so the tail
// is included honestly rather than pretended away.
const RENT_MIN = 400;
const RENT_MAX = 10_000;
const RENT_STEP = 100;

// Rooms. Zero is "any" rather than "no bedrooms": a studio has none and is still a
// home, so a filter of zero would be indistinguishable from no filter — and reading
// it as "any" is the meaning somebody dragging to the left end intends.
const ROOMS_MAX = 5;

const money = (value: number) => "£" + value.toLocaleString("en-GB");
const percent = (value: number, min: number, max: number) =>
  ((value - min) / (max - min)) * 100;

export function SubscribeForm({
  districts, maxDistricts, propertyTypes: _types, furnished, names = {},
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<Area[]>([]);
  const [typed, setTyped] = useState("");
  const [areaNote, setAreaNote] = useState<{ text: string; bad: boolean } | null>(null);
  // Neighbourhood first because it is the question people can answer without
  // looking anything up. A postcode district is something you know or you don't, and
  // asking for one first reads as a demand for information rather than a question
  // about where you want to live.
  const [mode, setMode] = useState<"name" | "postcode">("name");
  const [rent, setRent] = useState<[number, number]>([RENT_MIN, RENT_MAX]);
  const [beds, setBeds] = useState(0);
  const [baths, setBaths] = useState(0);
  const [link, setLink] = useState<string | null>(null);

  // Every neighbourhood by name, so Canary Wharf and Poplar both appear even though
  // both are E14. Postcode mode lists the districts themselves.
  const named = neighbourhoodAreas(names, districts);
  const options: Area[] =
    mode === "name" ? named : [...districts].sort().map((code) => ({ code, name: code }));

  const full = chosen.length >= maxDistricts;

  function add(area: Area) {
    if (chosen.some((one) => one.code === area.code)) {
      // Two neighbourhoods in one district are one filter entry. Said plainly,
      // because silently accepting it would promise a precision the filter does not
      // have — it matches on districts, the only location the feed states reliably.
      setAreaNote({
        text: `${area.name} is in ${area.code}, which you have already added.`,
        bad: false,
      });
      setTyped("");
      return;
    }
    if (full) {
      setAreaNote({
        text: `${maxDistricts} areas is the most one filter can cover. Remove one to add another.`,
        bad: true,
      });
      return;
    }
    const next = [...chosen, area];
    setChosen(next);
    setTyped("");
    setAreaNote(
      next.length === maxDistricts
        ? {
            // A warning at the limit rather than only a refusal past it: finding out
            // you are full by being told "no" is worse than being told you are full.
            text: `That is all ${maxDistricts}. More areas mean more alerts — most people settle on two or three.`,
            bad: false,
          }
        : null,
    );
  }

  function remove(code: string) {
    setAreaNote(null);
    setChosen((current) => current.filter((one) => one.code !== code));
  }

  /**
   * Resolve whatever was typed against the list on screen.
   *
   * A datalist is a suggestion, not a constraint — the field still accepts free text
   * and a browser may ignore the list entirely. So the value is matched rather than
   * trusted, and a code typed in neighbourhood mode is accepted too: refusing "E14"
   * because a radio button says "neighbourhood" would be pedantry.
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
      setAreaNote({ text: `I don't know "${text}" — pick one from the list.`, bad: true });
      return;
    }
    add(hit);
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

    // Only bounds that mean something are sent. A thumb parked at either end is the
    // absence of a limit, not a limit of £400 or £10,000, and sending it as a number
    // would quietly exclude everything beyond it.
    if (rent[0] > RENT_MIN) payload.price_min = String(rent[0]);
    if (rent[1] < RENT_MAX) payload.price_max = String(rent[1]);
    if (beds > 0) payload.bedrooms_min = String(beds);
    if (baths > 0) payload.bathrooms_min = String(baths);

    // A chosen date becomes the window the matcher already understands: ten days
    // either side. Generous on purpose — an advertised availability date is a
    // landlord's intention, not a fact, and demanding the exact day would reject the
    // same flat for being ready a week early.
    const wanted = String(data.get("available_on") ?? "").trim();
    delete payload.available_on;
    if (wanted) {
      const day = new Date(`${wanted}T00:00:00Z`);
      if (!Number.isNaN(day.getTime())) {
        const shift = (days: number) =>
          new Date(day.getTime() + days * 86_400_000).toISOString().slice(0, 10);
        payload.available_after = shift(-10);
        payload.available_before = shift(10);
      }
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

  if (link) return <AllDone url={link} token={tokenOf(link)} />;

  const placeholder =
    mode === "name" ? "Canary Wharf, Stratford, Chelsea…" : "E14, E15, SW3…";

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
                  setAreaNote(null);
                  setTyped("");
                }}
              />
              {text}
            </label>
          ))}
        </div>
      </fieldset>

      <div>
        <label htmlFor="area" className="field-label">
          {mode === "name" ? "Which neighbourhood?" : "Which postcode district?"}
        </label>
        {chosen.map((area) => (
          <input key={area.code} type="hidden" name="districts" value={area.code} />
        ))}

        <input
          id="area"
          type="text"
          list="area-list"
          value={typed}
          autoComplete="off"
          placeholder={placeholder}
          onChange={(event) => {
            setTyped(event.target.value);
            setAreaNote(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              // Otherwise Enter submits the form with no areas chosen, which reads
              // as the form rejecting itself.
              event.preventDefault();
              commitTyped();
            }
          }}
          onBlur={commitTyped}
        />
        {/* A datalist rather than a select: one control that both drops down and
            filters as you type, and on a phone the platform turns it into a picker.
            A 200-entry select does neither. */}
        <datalist id="area-list">
          {options.map((one) => (
            <option key={`${one.name}-${one.code}`} value={one.name}>
              {mode === "name" ? one.code : ""}
            </option>
          ))}
        </datalist>

        <p className="hint">
          Start typing and pick from the list. Up to {maxDistricts} areas.
        </p>

        {chosen.length > 0 && (
          <div className="chips">
            {chosen.map((area) => (
              <span
                key={area.code}
                role="button"
                tabIndex={0}
                title="Remove"
                onClick={() => remove(area.code)}
                onKeyDown={(event) => event.key === "Enter" && remove(area.code)}
                className="chip on"
              >
                {mode === "name" && area.name !== area.code
                  ? `${area.name} · ${area.code}`
                  : area.code}{" "}
                ✕
              </span>
            ))}
          </div>
        )}
        {areaNote && (
          <p className={areaNote.bad ? "error" : "notice"}>{areaNote.text}</p>
        )}
      </div>

      <div>
        <span className="field-label">Rent per month</span>
        <RangeSlider
          min={RENT_MIN}
          max={RENT_MAX}
          step={RENT_STEP}
          value={rent}
          onChange={setRent}
          format={money}
          openTop="+"
        />
      </div>

      <div>
        <span className="field-label">Bedrooms</span>
        <Stepper
          value={beds}
          onChange={setBeds}
          max={ROOMS_MAX}
          label={(n) => (n === 0 ? "Any" : `${n}+`)}
        />
        <p className="hint">Any includes studios.</p>
      </div>

      <div>
        <span className="field-label">Bathrooms</span>
        <Stepper
          value={baths}
          onChange={setBaths}
          max={ROOMS_MAX}
          label={(n) => (n === 0 ? "Any" : `${n}+`)}
        />
        <p className="hint">
          Listings that do not state it are still sent — most do not state it.
        </p>
      </div>

      <label>
        <span>Desired let available date</span>
        <input type="date" name="available_on" />
        <p className="hint">
          Listings available within about ten days of it. Leave blank for any date —
          and a listing that gives no date is sent either way.
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

/**
 * One slider with two thumbs.
 *
 * ── how, and why it is built rather than imported ────────────────────────────
 *
 * HTML has no two-thumb range input. The usual answers are a 30kB library or two
 * stacked sliders that look like two controls. This is two `input[type=range]`
 * elements sharing one track: the inputs are transparent and ignore pointer events,
 * their thumbs accept them, and the visible track and highlight are drawn behind.
 * The result is one control to look at and two to grab, and it keeps the keyboard
 * and screen-reader behaviour the platform already gives a range input.
 *
 * The thumbs clamp rather than swap. Dragging one past the other should push it, not
 * silently change which one you are holding — that is disorienting in a way no
 * amount of correctness makes up for.
 */
function RangeSlider({
  min, max, step, value, onChange, format, openTop = "",
}: {
  min: number;
  max: number;
  step: number;
  value: [number, number];
  onChange: (next: [number, number]) => void;
  format: (n: number) => string;
  openTop?: string;
}) {
  const [low, high] = value;
  const atFloor = low === min;
  const atCeiling = high === max;

  return (
    <div className="range2">
      <output className="range2-value">
        {atFloor && atCeiling
          ? "Any"
          : `${format(low)} — ${format(high)}${atCeiling ? openTop : ""}`}
      </output>

      <div className="range2-track">
        <div
          className="range2-fill"
          style={{
            left: `${percent(low, min, max)}%`,
            right: `${100 - percent(high, min, max)}%`,
          }}
        />
        <input
          type="range"
          className="range2-input"
          min={min}
          max={max}
          step={step}
          value={low}
          aria-label="Lowest"
          onChange={(event) => onChange([Math.min(Number(event.target.value), high), high])}
        />
        <input
          type="range"
          className="range2-input"
          min={min}
          max={max}
          step={step}
          value={high}
          aria-label="Highest"
          onChange={(event) => onChange([low, Math.max(Number(event.target.value), low)])}
        />
      </div>

      <div className="range2-ends">
        <span>{format(min)}</span>
        <span>
          {format(max)}
          {openTop}
        </span>
      </div>
    </div>
  );
}

/**
 * A single-thumb slider for a small count.
 *
 * Kept separate from `RangeSlider` rather than made a mode of it: a count from zero
 * to five wants tick marks and a word for each stop ("Any", "2+"), and a rent slider
 * wants neither. One component doing both would be a parameter list longer than
 * either.
 */
function Stepper({
  value, onChange, max, label,
}: {
  value: number;
  onChange: (next: number) => void;
  max: number;
  label: (n: number) => string;
}) {
  return (
    <div className="stepper">
      <output className="range2-value">{label(value)}</output>
      <input
        type="range"
        min={0}
        max={max}
        step={1}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
      <div className="stepper-ticks">
        {Array.from({ length: max + 1 }, (_, n) => (
          <span key={n} className={n === value ? "on" : undefined}>
            {n === 0 ? "Any" : n}
          </span>
        ))}
      </div>
    </div>
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
 * they get from another channel existing. Asked on the landing page it would be a
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
