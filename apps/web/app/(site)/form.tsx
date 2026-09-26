"use client";

import { useState } from "react";

import { pounds } from "@/lib/money";
import { neighbourhoodAreas, type Area } from "@/lib/neighbourhoods";

import { TelegramMark, WhatsAppMark } from "./logos";
import { PhonePreview } from "./phone";

type Props = {
  districts: string[];
  maxDistricts: number;
  furnished: string[];
  types: string[];
  whatsappReady: boolean;
  // Both read from `plans`, so neither card can promise a trial or a price the
  // bot then contradicts. The trial differs by messenger because a WhatsApp
  // alert is billed per message; the price differs for the same reason.
  offers: Record<Channel, Offer>;

  /**
   * Set when somebody who already has a filter is changing it, from a /update
   * link. Then there is nothing to sell: they are on a messenger already and
   * the only question is whether to save. Prices and a free trial on this page
   * would be answering a question they did not ask.
   */
  returning?: {
    channel: Channel;
    full: boolean;
    upgradeUrl: string | null;
  } | null;

  names?: Record<string, string>;
};

export type Offer = {
  trialDays: number | null;
  // Every price this messenger is sold at, cheapest first. Telegram has two —
  // a week and a month — and WhatsApp one, so the band is a list rather than a
  // single figure.
  prices: { pence: number; unit: string }[];
};

type Channel = "telegram" | "whatsapp";

const RENT_MIN = 400;
const RENT_MAX = 10_000;
const RENT_STEP = 100;

// Bedrooms start at nought because a studio is a real thing to search for.
// Bathrooms do not: no flat is let with none, so a floor of zero was a value
// nobody could ever want and a label that read as a mistake.
const BEDS_MIN = 0;
const BATHS_MIN = 1;
const ROOMS_MAX = 5;

const rooms = (value: number) => String(value);

// "room" on its own reads as a bedroom count rather than as what it is.
const TYPE_LABELS: Record<string, string> = {
  flat: "Flat",
  house: "House",
  room: "Room",
};
const beds = (value: number) => (value === 0 ? "Studio" : String(value));

// How many days either side of the desired date a listing may be available.
// Ten was hard-coded; it is now the starting point of a field.
const DAY_WINDOW = 10;
const DAY_WINDOW_MAX = 90;

// The slider is in square metres, and so is everything the person reads. The
// database keeps square feet, because that is the unit British listings quote
// and one of the two sources states it that way — so the conversion happens
// once, where the form is submitted. See `submit`.
//
// ── why the steps are uneven ─────────────────────────────────────────────
//
// The interesting part of this scale is the bottom. A studio is about 30 m² and
// a two-bed about 70; the difference between 240 and 260 decides nothing. An
// even step fine enough for the bottom would make the handle crawl across the
// top, and one coarse enough for the top cannot tell a studio from a one-bed.
//
// So: 5 m² up to 100, then 10 to 200, then 20 to 300. Each stop gets the same
// travel, which puts the precision where the pointing is hard.
const AREA_STOPS = [
  ...Array.from({ length: 21 }, (_, i) => i * 5),        // 0 … 100
  ...Array.from({ length: 10 }, (_, i) => 110 + i * 10), // 110 … 200
  ...Array.from({ length: 5 }, (_, i) => 220 + i * 20),  // 220 … 300
];
const AREA_MIN = AREA_STOPS[0]!;
const AREA_MAX = AREA_STOPS[AREA_STOPS.length - 1]!;

// 10.7639 square feet to the square metre.
const SQFT_PER_SQM = 10.7639;
const asSqft = (sqm: number) => Math.round(sqm * SQFT_PER_SQM);
const sqm = (value: number) => value.toLocaleString("en-GB") + " m²";
const sqft = (value: number) => asSqft(value).toLocaleString("en-GB") + " ft²";

const money = (value: number) => "£" + value.toLocaleString("en-GB");
const percent = (value: number, min: number, max: number) =>
  ((value - min) / (max - min)) * 100;

export function SubscribeForm({
  districts, maxDistricts, furnished, types, whatsappReady, offers,
  returning = null, names = {},
}: Props) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Channel | null>(null);
  const [chosen, setChosen] = useState<Area[]>([]);
  const [typed, setTyped] = useState("");
  const [areaNote, setAreaNote] = useState<{ text: string; bad: boolean } | null>(null);

  const [mode, setMode] = useState<"name" | "postcode">("name");
  const [rent, setRent] = useState<[number, number]>([RENT_MIN, RENT_MAX]);
  const [bedrooms, setBedrooms] = useState<[number, number]>([BEDS_MIN, ROOMS_MAX]);
  const [bathrooms, setBathrooms] = useState<[number, number]>([BATHS_MIN, ROOMS_MAX]);
  // Controlled, because two other fields depend on them: bedrooms is
  // meaningless for a room, and the date's window only matters with a date.
  const [wantedTypes, setWantedTypes] = useState<string[]>([]);
  const [availableOn, setAvailableOn] = useState("");
  const [dayWindow, setDayWindow] = useState(DAY_WINDOW);
  const [area, setArea] = useState<[number, number]>([AREA_MIN, AREA_MAX]);
  const [leaving, setLeaving] = useState<{ channel: Channel; url: string } | null>(null);

  const named = neighbourhoodAreas(names, districts);
  const options: Area[] =
    mode === "name" ? named : [...districts].sort().map((code) => ({ code, name: code }));

  const full = chosen.length >= maxDistricts;

  // Only when rooms are the *only* thing wanted. Ticking Room beside Flat still
  // leaves bedrooms meaningful, for the flats.
  const roomsOnly = wantedTypes.length > 0 && wantedTypes.every((one) => one === "room");

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

    // Which button was pressed. `new FormData(form)` does not include the
    // submitter, so it is read from the event — and it is read before the
    // waiting state is set, so only the pressed button shows it.
    const submitter = (event.nativeEvent as SubmitEvent).submitter;
    const channel: Channel =
      submitter instanceof HTMLButtonElement && submitter.value === "whatsapp"
        ? "whatsapp"
        : "telegram";

    setBusy(channel);

    const data = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = {};
    for (const key of new Set(data.keys())) {
      const values = data.getAll(key).map(String);
      payload[key] = values.length > 1 ? values : values[0];
    }

    if (rent[0] > RENT_MIN) payload.price_min = String(rent[0]);
    if (rent[1] < RENT_MAX) payload.price_max = String(rent[1]);
    // Only the ends that were actually moved. A slider left at its ceiling
    // means "and above", not "at most five" — sending the max there would hide
    // every six-bedroom house from somebody who asked for no maximum.
    // Nothing from a slider that is switched off: a bedroom count filed against
    // a rooms-only search would quietly match nothing.
    if (!roomsOnly) {
      if (bedrooms[0] > BEDS_MIN) payload.bedrooms_min = String(bedrooms[0]);
      if (bedrooms[1] < ROOMS_MAX) payload.bedrooms_max = String(bedrooms[1]);
    }
    if (bathrooms[0] > BATHS_MIN) payload.bathrooms_min = String(bathrooms[0]);
    if (bathrooms[1] < ROOMS_MAX) payload.bathrooms_max = String(bathrooms[1]);
    // Metres on the slider, feet in the database — converted here, once. The
    // top handle at its ceiling means "and above", so no maximum is sent.
    if (area[0] > AREA_MIN) payload.area_min = String(asSqft(area[0]));
    if (area[1] < AREA_MAX) payload.area_max = String(asSqft(area[1]));

    const wanted = String(data.get("available_on") ?? "").trim();
    delete payload.available_on;
    if (wanted) {
      const day = new Date(`${wanted}T00:00:00Z`);
      if (!Number.isNaN(day.getTime())) {
        const shift = (days: number) =>
          new Date(day.getTime() + days * 86_400_000).toISOString().slice(0, 10);
        payload.available_after = shift(-dayWindow);
        payload.available_before = shift(dayWindow);
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
        setBusy(null);
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
      setBusy(null);
    }
  }

  if (leaving) return <Handover channel={leaving.channel} url={leaving.url} />;

  const placeholder =
    mode === "name" ? "Canary Wharf, Stratford, Chelsea…" : "E14, E15, SW3…";

  // What has been typed is a district in its own right, and also the start of
  // another one. "E1" against "E14": the person may mean either, so nothing is
  // committed until they say so.
  const said = typed.trim().toLowerCase();
  const stillAmbiguous =
    said !== "" &&
    options.some(
      (one) => one.name.toLowerCase() === said || one.code.toLowerCase() === said,
    ) &&
    options.some(
      (one) =>
        one.name.toLowerCase().startsWith(said) && one.name.toLowerCase() !== said,
    );

  return (
    <form onSubmit={submit} className="hero-form">
      <div className="filter-card">
      <fieldset aria-labelledby="search-by-label">
        <span id="search-by-label" className="field-label">
          How would you like to search?
        </span>
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
            const value = event.target.value;
            setAreaNote(null);

            // Picking from the list is the choice. A datalist reports the pick
            // as an ordinary change whose value is the option in full, so an
            // exact match is a pick rather than someone halfway through typing
            // — and nobody should have to press Enter after choosing.
            //
            // Except when the exact match is also the start of another option.
            // "E1" is a district and so is "E14": committing on the exact match
            // added E1 the moment it was typed and made E14 unreachable. In that
            // case the typing is allowed to continue, and Enter or clicking away
            // commits — which the hint below says while it is ambiguous.
            const said = value.trim().toLowerCase();
            const exact = options.find(
              (one) =>
                one.name.toLowerCase() === said || one.code.toLowerCase() === said,
            );
            const alsoAPrefix =
              said !== "" &&
              options.some(
                (one) =>
                  one.name.toLowerCase().startsWith(said) &&
                  one.name.toLowerCase() !== said,
              );

            if (exact && !alsoAPrefix) {
              add(exact);
              return;
            }
            setTyped(value);
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
          {stillAmbiguous
            ? `Press Enter to add ${typed.trim().toUpperCase()}, or keep typing.`
            : "Start typing and pick from the list."}
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
        <RangeSlider
          min={BEDS_MIN}
          max={ROOMS_MAX}
          step={1}
          value={bedrooms}
          onChange={setBedrooms}
          format={beds}
          openTop="+"
          disabled={roomsOnly}
        />
        {roomsOnly && (
          <small className="note">
            A room is one room in somebody else's flat, so a bedroom count says
            nothing about it. Tick Flat or House as well to use this again.
          </small>
        )}
      </div>

      <div>
        <span className="field-label">Bathrooms</span>
        <RangeSlider
          min={BATHS_MIN}
          max={ROOMS_MAX}
          step={1}
          value={bathrooms}
          onChange={setBathrooms}
          format={rooms}
          openTop="+"
        />
      </div>

      <div>
        <span className="field-label">Desired property size</span>
        <RangeSlider
          min={AREA_MIN}
          max={AREA_MAX}
          step={1}
          stops={AREA_STOPS}
          value={area}
          onChange={setArea}
          format={sqm}
          openTop="+"
        />
        {/* The same numbers in feet, under the metres. Not a second control:
            one slider, read twice. */}
        <p className="range2-metric">
          {area[0] === AREA_MIN && area[1] === AREA_MAX
            ? "Any size"
            : `${sqft(area[0])} — ${sqft(area[1])}${area[1] === AREA_MAX ? "+" : ""}`}
        </p>
        <small className="note">
          Most listings never say how big they are, and those still come through —
          this narrows the ones that do say.
        </small>
      </div>

      <div>
        <span className="field-label">Desired let available date</span>
        <div className="date-row">
          <label className="date-on">
            <span className="sr-only">Date</span>
            <input
              type="date"
              name="available_on"
              value={availableOn}
              onChange={(event) => setAvailableOn(event.target.value)}
            />
          </label>
          {/* Second in the markup as well as on the screen, so tabbing goes
              date then window — the order they are decided in. */}
          <label className="date-days">
            <span>± days</span>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              max={DAY_WINDOW_MAX}
              step={1}
              value={dayWindow}
              // Off until there is a date to be either side of.
              disabled={availableOn === ""}
              onChange={(event) => {
                const days = Number(event.target.value);
                setDayWindow(
                  Number.isFinite(days)
                    ? Math.min(DAY_WINDOW_MAX, Math.max(0, Math.round(days)))
                    : 0,
                );
              }}
            />
          </label>
        </div>
        <small className="note">
          {availableOn === ""
            ? "Leave blank for any date — a listing that gives no date is sent either way."
            : dayWindow === 0
              ? "Only listings available on exactly that day."
              : `Listings available within ${dayWindow} day${dayWindow === 1 ? "" : "s"} either side of it.`}
        </small>
      </div>

      <fieldset aria-labelledby="type-label">
        <span id="type-label" className="field-label">Property type</span>
        <div className="choices">
          {types.map((option) => (
            <label key={option}>
              <input
                type="checkbox"
                name="property_types"
                value={option}
                checked={wantedTypes.includes(option)}
                onChange={(event) =>
                  setWantedTypes((was) =>
                    event.target.checked
                      ? [...was, option]
                      : was.filter((one) => one !== option),
                  )
                }
              />{" "}
              {TYPE_LABELS[option] ?? option}
            </label>
          ))}
        </div>
        <small className="note">
          Ticking Flat and House is how you stop hearing about rooms. A studio is
          a flat — ask for one with the bedroom slider at Studio.
          A listing that does not say what it is still comes through.
        </small>
      </fieldset>

      <fieldset aria-labelledby="furnishing-label">
        <span id="furnishing-label" className="field-label">Furnishing</span>
        <div className="choices">
          {furnished.map((option) => (
            <label key={option}>
              <input type="checkbox" name="furnished" value={option} /> {option}
            </label>
          ))}
        </div>
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

      </div>

      <div className="hero-aside">
        <PhonePreview />
      </div>

      <div className="hero-actions">
      {error && <p className="error">{error}</p>}

      {returning ? (
        <Save
          channel={returning.channel}
          full={returning.full}
          upgradeUrl={returning.upgradeUrl}
          busy={busy}
          ready={chosen.length > 0}
        />
      ) : (
        <>
          <p className="offers-lead">Start with a free trial. Cancel anytime.</p>

          <div className={whatsappReady ? "offers" : "offers offers-one"}>
            <Choice
              channel="telegram"
              label="Connect Telegram"
              mark={<TelegramMark />}
              offer={offers.telegram}
              busy={busy}
              ready={chosen.length > 0}
              // Free to deliver on, so it is the one we would rather people use
              // — and saying so is more honest than pricing them towards it
              // quietly.
              flag="Highly recommended"
            />

            {whatsappReady && (
              <Choice
                channel="whatsapp"
                label="Connect WhatsApp"
                mark={<WhatsAppMark />}
                offer={offers.whatsapp}
                busy={busy}
                ready={chosen.length > 0}
              />
            )}
          </div>
        </>
      )}
      </div>
    </form>
  );
}

// Saving a changed filter. One button, back to the messenger they are already
// on — no price, no trial, nothing to choose between.
//
// The exception is somebody whose plan has run out: they are about to save a
// filter that will only be delivered in part, and not saying so would be the
// unpleasant surprise. So the offer appears here, and only here.
function Save({
  channel,
  full,
  upgradeUrl,
  busy,
  ready,
}: {
  channel: Channel;
  full: boolean;
  upgradeUrl: string | null;
  busy: Channel | null;
  ready: boolean;
}) {
  const where = channel === "whatsapp" ? "WhatsApp" : "Telegram";

  return (
    <div className="offers offers-one">
      <div className="offer">
        <ul className="offer-points">
          <li>
            <Tick /> Your new filter replaces the old one
          </li>
          <li>
            <Tick /> Only listings from now on
          </li>
        </ul>

        <button
          type="submit"
          name="channel"
          value={channel}
          className={`cta cta-${channel}`}
          disabled={busy !== null || !ready}
        >
          {channel === "whatsapp" ? <WhatsAppMark /> : <TelegramMark />}
          {busy === channel ? "One moment…" : `Save and back to ${where}`}
        </button>

        {!full && (
          <p className="offer-lapsed">
            Your plan has ended, so only part of what matches is being sent.
            {upgradeUrl ? (
              <>
                {" "}
                <a href={upgradeUrl}>Get full access</a>
              </>
            ) : (
              " Send /pay to the bot for full access."
            )}
          </p>
        )}
      </div>
    </div>
  );
}

// One messenger's offer: what it costs to try, then the button that starts it.
//
// The four points are the same on both cards except the trial length, which is
// the only thing that differs — so they are written once and the length is
// passed in.
function Choice({
  channel, label, mark, offer, busy, ready, flag,
}: {
  channel: Channel;
  label: string;
  mark: React.ReactNode;
  offer: Offer;
  busy: Channel | null;
  ready: boolean;
  flag?: string;
}) {
  const { trialDays, prices } = offer;

  return (
    <div className={flag ? "offer offer-best" : "offer"}>
      {flag && <span className="offer-flag">{flag}</span>}

      {/* What it costs once the trial ends, on the messenger's own colour and
          at the top of the card — the first thing worth knowing, and the thing
          that differs most between the two. Absent until a plan is priced for
          this channel, rather than invented. */}
      {prices.length > 0 && (
        <div className={`offer-prices offer-prices-${channel}`}>
          {prices.map((price) => (
            <span key={price.unit} className="offer-rate">
              <strong>{pounds(price.pence)}</strong>
              <span className="offer-unit">{price.unit}</span>
            </span>
          ))}
        </div>
      )}

      <ul className="offer-points">
        {/* Omitted rather than guessed at when no plan is configured: a trial
            this page invented is a promise the bot would not keep. */}
        {trialDays !== null && (
          <li>
            <Tick /> {trialDays} {trialDays === 1 ? "day" : "days"} free trial
          </li>
        )}
        <li>
          <Tick /> Cancel anytime
        </li>
        <li>
          <Tick /> Real-time alerts
        </li>
        <li>
          <Tick /> No credit card required to start
        </li>
      </ul>

      <button
        type="submit"
        name="channel"
        value={channel}
        className={`cta cta-${channel}`}
        disabled={busy !== null || !ready}
      >
        {mark}
        {busy === channel ? "One moment…" : label}
      </button>
    </div>
  );
}

function Tick() {
  return (
    <svg className="tick" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <path d="M3 8.5l3.5 3.5L13 5" fill="none" stroke="currentColor" strokeWidth="2.2"
            strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function RangeSlider({
  min, max, step, value, onChange, format, openTop = "", disabled = false,
  stops,
}: {
  min: number;
  max: number;
  step: number;
  value: [number, number];
  onChange: (next: [number, number]) => void;
  format: (n: number) => string;
  openTop?: string;
  disabled?: boolean;
  /**
   * The values the handles may take, when they should not be evenly spaced.
   *
   * A native range input has one step, so a scale that is fine at the bottom
   * and coarse at the top cannot be expressed with `step` alone. Given stops,
   * the input runs over their indices instead and each stop gets the same
   * amount of travel — which is the point: the crowded end of the scale is
   * where the pointing is hard.
   *
   * `value` and `onChange` still speak in real values either way, so no caller
   * has to know about indices.
   */
  stops?: number[];
}) {
  const [low, high] = value;

  // An index, or the value itself when the scale is even. `min`/`max`/`step`
  // are what the input is given in both cases, so the rest of the component
  // does not branch.
  const at = (one: number) => {
    if (!stops) return one;
    let best = 0;
    for (let i = 1; i < stops.length; i += 1) {
      if (Math.abs(stops[i]! - one) < Math.abs(stops[best]! - one)) best = i;
    }
    return best;
  };
  const from = (position: number) =>
    stops ? stops[Math.min(stops.length - 1, Math.max(0, Math.round(position)))]! : position;

  const floor = stops ? 0 : min;
  const ceiling = stops ? stops.length - 1 : max;
  const tick = stops ? 1 : step;
  const lowAt = at(low);
  const highAt = at(high);
  const atFloor = lowAt === floor;
  const atCeiling = highAt === ceiling;

  return (
    <div className={disabled ? "range2 range2-off" : "range2"}>
      <output className="range2-value">
        {atFloor && atCeiling
          ? "Any"
          : `${format(low)} — ${format(high)}${atCeiling ? openTop : ""}`}
      </output>

      <div className="range2-track">
        <div
          className="range2-fill"
          style={{
            left: `${percent(lowAt, floor, ceiling)}%`,
            right: `${100 - percent(highAt, floor, ceiling)}%`,
          }}
        />
        <input
          type="range"
          className="range2-input"
          style={{ zIndex: lowAt >= (floor + ceiling) / 2 ? 3 : 2 }}
          min={floor}
          max={ceiling}
          step={tick}
          value={lowAt}
          aria-label="Lowest"
          disabled={disabled}
          onChange={(event) =>
            onChange([Math.min(from(Number(event.target.value)), high), high])
          }
        />
        <input
          type="range"
          className="range2-input"
          style={{ zIndex: lowAt >= (floor + ceiling) / 2 ? 2 : 3 }}
          min={floor}
          max={ceiling}
          step={tick}
          value={highAt}
          aria-label="Highest"
          disabled={disabled}
          onChange={(event) =>
            onChange([low, Math.max(from(Number(event.target.value)), low)])
          }
        />
      </div>

      <div className="range2-ends">
        <span>{format(from(floor))}</span>
        <span>
          {format(from(ceiling))}
          {openTop}
        </span>
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
