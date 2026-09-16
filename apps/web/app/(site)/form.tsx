"use client";

import { useState } from "react";

import { neighbourhoodAreas, type Area } from "@/lib/neighbourhoods";

import { TelegramMark, WhatsAppMark } from "./logos";

type Props = {
  districts: string[];
  maxDistricts: number;
  furnished: string[];
  whatsappReady: boolean;

  names?: Record<string, string>;
};

type Channel = "telegram" | "whatsapp";

const RENT_MIN = 400;
const RENT_MAX = 10_000;
const RENT_STEP = 100;

const ROOMS_MAX = 5;

const money = (value: number) => "£" + value.toLocaleString("en-GB");
const percent = (value: number, min: number, max: number) =>
  ((value - min) / (max - min)) * 100;

export function SubscribeForm({
  districts, maxDistricts, furnished, whatsappReady, names = {},
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [chosen, setChosen] = useState<Area[]>([]);
  const [typed, setTyped] = useState("");
  const [areaNote, setAreaNote] = useState<{ text: string; bad: boolean } | null>(null);

  const [mode, setMode] = useState<"name" | "postcode">("name");
  const [rent, setRent] = useState<[number, number]>([RENT_MIN, RENT_MAX]);
  const [beds, setBeds] = useState(0);
  const [baths, setBaths] = useState(0);
  const [leaving, setLeaving] = useState<{ channel: Channel; url: string } | null>(null);

  const named = neighbourhoodAreas(names, districts);
  const options: Area[] =
    mode === "name" ? named : [...districts].sort().map((code) => ({ code, name: code }));

  const full = chosen.length >= maxDistricts;

  function add(area: Area) {
    if (chosen.some((one) => one.code === area.code)) {

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

    // Which button was pressed. `new FormData(form)` does not include the
    // submitter, so it is read from the event.
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const channel =
      submitter instanceof HTMLButtonElement && submitter.value === "whatsapp"
        ? "whatsapp"
        : "telegram";

    const data = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = {};
    for (const key of new Set(data.keys())) {
      const values = data.getAll(key).map(String);
      payload[key] = values.length > 1 ? values : values[0];
    }

    if (rent[0] > RENT_MIN) payload.price_min = String(rent[0]);
    if (rent[1] < RENT_MAX) payload.price_max = String(rent[1]);
    if (beds > 0) payload.bedrooms_min = String(beds);
    if (baths > 0) payload.bathrooms_min = String(baths);

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
        body: JSON.stringify({ ...payload, channel }),
      });
      const body = (await response.json()) as { url?: string; error?: string };
      if (!response.ok || !body.url) {
        setError(body.error ?? "Something went wrong. Please try again.");
        setBusy(false);
        return;
      }

      // Navigating the current tab rather than opening a window: a popup
      // blocked after an await is the usual way this breaks, and a same-tab
      // navigation is never blocked. The panel below is what shows if the
      // handover does not happen — a desktop browser with no app installed.
      setLeaving({ channel, url: body.url });
      window.location.href = body.url;
    } catch {
      setError("Could not reach the server. Please try again.");
      setBusy(false);
    }
  }

  if (leaving) return <Handover channel={leaving.channel} url={leaving.url} />;

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

              event.preventDefault();
              commitTyped();
            }
          }}
          onBlur={commitTyped}
        />

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

      <div className="connect">
        <button
          type="submit"
          name="channel"
          value="telegram"
          className="cta cta-telegram"
          disabled={busy || chosen.length === 0}
        >
          <TelegramMark /> {busy ? "One moment…" : "Connect to Telegram"}
        </button>

        {whatsappReady && (
          <button
            type="submit"
            name="channel"
            value="whatsapp"
            className="cta cta-whatsapp"
            disabled={busy || chosen.length === 0}
          >
            <WhatsAppMark /> {busy ? "One moment…" : "Connect to WhatsApp"}
          </button>
        )}
      </div>

      <p className="hint">
        {chosen.length === 0
          ? "Add at least one area first."
          : whatsappReady
            ? "One search goes to one app, so a listing never arrives twice. Want both? Fill this in again afterwards."
            : "Only listings posted from the moment you connect — never a backlog."}
      </p>
    </form>
  );
}

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

function Handover({ channel, url }: { channel: Channel; url: string }) {
  const app = channel === "whatsapp" ? "WhatsApp" : "Telegram";

  return (
    <div className="panel">
      <div className="done-tick">✓</div>
      <h1>Your search is saved</h1>
      <p className="lede">
        Opening {app} now. Press Start there and the alerts begin — only listings
        posted from that moment on, never a backlog.
      </p>

      <a
        href={url}
        className={channel === "whatsapp" ? "cta cta-whatsapp" : "cta cta-telegram"}
      >
        {channel === "whatsapp" ? <WhatsAppMark /> : <TelegramMark />} Open {app}
      </a>

      <p className="hint">
        If nothing happened, use the button — some desktop browsers will not hand
        over on their own.
      </p>
    </div>
  );
}
