"use client";

import { useEffect, useRef, useState } from "react";

import type { Criteria } from "@/lib/criteria";
import { pounds } from "@/lib/money";
import { matchAreas, neighbourhoodAreas, type Area } from "@/lib/neighbourhoods";
// Every bound of every control on this form — the rent floor, the room
// ceiling, the size stops — lives there, next to the code that reads a saved
// filter back onto them. Two copies of "the rent slider starts at £400" is two
// places for it to drift, and the drift would be invisible: the form would draw
// one scale and reopen a saved filter on another.
import {
  AREA_MAX,
  AREA_MIN,
  AREA_STOPS,
  asSqft,
  BATHS_MIN,
  BEDS_MIN,
  DAY_WINDOW_MAX,
  prefill,
  RENT_MAX,
  RENT_MIN,
  RENT_STEP,
  ROOMS_MAX,
  SQFT_PER_SQM,
} from "@/lib/prefill";

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
    /**
     * The token the page was opened with, posted back when they save. It is
     * what lets the save land on their own account instead of opening a new
     * one — see `saveEditedFilter`.
     */
    token: string;
    full: boolean;
    upgradeUrl: string | null;
    /** The filter they already have. Every control below opens on it. */
    criteria: Criteria;
  } | null;

  names?: Record<string, string>;
};

export type Offer = {
  trialDays: number | null;
  /**
   * Alerts included in a paid period, or null for as many as there are.
   *
   * From `plans.alert_allowance`, so the card cannot promise a number the
   * delivery rules do not honour — which it did while this was a string typed
   * into the list below.
   */
  allowance: number | null;
  // Every price this messenger is sold at, cheapest first. Telegram has two —
  // a week and a month — and WhatsApp one, so the band is a list rather than a
  // single figure.
  prices: { pence: number; unit: string }[];
};

type Channel = "telegram" | "whatsapp";

const rooms = (value: number) => String(value);

// "room" on its own reads as a bedroom count rather than as what it is.
const TYPE_LABELS: Record<string, string> = {
  flat: "Flat",
  house: "House",
  room: "Room",
};
const beds = (value: number) => (value === 0 ? "Studio" : String(value));

const sqm = (value: number) => value.toLocaleString("en-GB") + " m²";
const sqft = (value: number) => asSqft(value).toLocaleString("en-GB") + " ft²";

const money = (value: number) => "£" + value.toLocaleString("en-GB");
const percent = (value: number, min: number, max: number) =>
  ((value - min) / (max - min)) * 100;

export function SubscribeForm({
  districts, maxDistricts, furnished, types, whatsappReady, offers,
  returning = null, names = {},
}: Props) {
  // Where every control starts. Blank for a first visit; for somebody arriving
  // from /update it is the filter they already have, read back off the criteria
  // the bot is matching on — so the page they land on is their search rather
  // than a sign-up form they have to fill in again from memory.
  //
  // Computed once, as the initial value of each piece of state: after that the
  // controls own themselves, and re-deriving them would undo typing.
  const start = prefill(returning?.criteria, districts);

  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Channel | null>(null);
  const [chosen, setChosen] = useState<Area[]>(() =>
    // The district code is the whole of what was saved — a neighbourhood name
    // is a way of choosing one, not part of the filter — so the chip says the
    // code. Guessing "Canary Wharf" for an E14 that was chosen as Poplar would
    // be a detail we invented.
    start.districts.map((code) => ({ code, name: code })),
  );
  const [typed, setTyped] = useState("");
  const [areaNote, setAreaNote] = useState<{ text: string; bad: boolean } | null>(() =>
    // Said rather than swallowed. Saving replaces the filter, so an area that
    // has gone out of coverage and quietly disappeared from this list would
    // disappear from their search too, and the first they would know of it is
    // the alerts thinning out.
    start.dropped.length
      ? {
          text:
            `${start.dropped.join(", ")} ` +
            `${start.dropped.length === 1 ? "is" : "are"} not covered any more, ` +
            `so ${start.dropped.length === 1 ? "it is" : "they are"} not in the ` +
            "list below. Saving without it drops it from your search.",
          bad: true,
        }
      : null,
  );
  // The list below the box, and which of its rows the keyboard is on. A
  // `datalist` was doing this job and could not be styled at all — its width,
  // type and colours are the browser's, so it landed on the page as somebody
  // else's control. See `.combo` in globals.css.
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const listRef = useRef<HTMLUListElement | null>(null);

  // The list is no longer capped, so the highlighted row can be below the fold
  // of its own scroller: arrowing down has to bring it along.
  useEffect(() => {
    if (!open) return;
    listRef.current
      ?.querySelector('[aria-selected="true"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [open, cursor, typed]);

  const [mode, setMode] = useState<"name" | "postcode">("name");
  const [rent, setRent] = useState<[number, number]>(start.rent);
  const [bedrooms, setBedrooms] = useState<[number, number]>(start.bedrooms);
  const [bathrooms, setBathrooms] = useState<[number, number]>(start.bathrooms);
  // Controlled, because two other fields depend on them: bedrooms is
  // meaningless for a room, and the date's window only matters with a date.
  const [wantedTypes, setWantedTypes] = useState<string[]>(start.types);
  const [availableOn, setAvailableOn] = useState(start.availableOn);
  const [dayWindow, setDayWindow] = useState(start.dayWindow);
  const [area, setArea] = useState<[number, number]>(start.size);
  const [leaving, setLeaving] = useState<{
    channel: Channel;
    url: string;
    /** A changed filter, already saved — rather than one waiting to be connected. */
    updated: boolean;
  } | null>(null);

  const named = neighbourhoodAreas(names, districts);
  const options: Area[] =
    mode === "name" ? named : [...districts].sort().map((code) => ({ code, name: code }));

  const full = chosen.length >= maxDistricts;

  // Only when rooms are the *only* thing wanted. Ticking Room beside Flat still
  // leaves bedrooms meaningful, for the flats.
  const roomsOnly = wantedTypes.length > 0 && wantedTypes.every((one) => one === "room");
  // Rooms alongside something countable: the sliders stay, but they only apply
  // to the flats and houses — so say so rather than let them look universal.
  const roomsToo = !roomsOnly && wantedTypes.includes("room");

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

  function choose(area: Area) {
    add(area);
    setCursor(0);
    // Closed on every pick. It used to stay open while there was room for
    // another, to save reopening it — but the open list covers the chips and
    // the note underneath, so the one thing a pick should show you is the one
    // thing you cannot see. The input keeps focus, so typing or ArrowDown
    // brings the list straight back for the next area.
    setOpen(false);
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

    // What used to be a greyed-out button. Checked here instead, so the answer
    // arrives next to the field that is missing rather than as a control that
    // will not respond.
    if (chosen.length === 0) {
      setAreaNote({
        text: "Pick at least one area first — that is what the alerts are about.",
        bad: true,
      });
      document.getElementById("area")?.focus();
      return;
    }

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
      if (bathrooms[0] > BATHS_MIN) payload.bathrooms_min = String(bathrooms[0]);
      if (bathrooms[1] < ROOMS_MAX) payload.bathrooms_max = String(bathrooms[1]);
    }
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
        // The edit token when there is one: saving then changes the filter
        // this person already has, and the answer comes back in their chat,
        // rather than opening a second account to be merged on arrival.
        body: JSON.stringify({ ...payload, channel, edit: returning?.token }),
      });
      const body = (await response.json()) as {
        url?: string;
        error?: string;
        updated?: boolean;
      };
      if (!response.ok || !body.url) {
        setError(body.error ?? "Something went wrong. Please try again.");
        setBusy(null);
        return;
      }

      // Navigating the current tab rather than opening a window: a popup
      // blocked after an await is the usual way this breaks, and a same-tab
      // navigation is never blocked. The panel below is what shows if the
      // handover does not happen — a desktop browser with no app installed.
      setLeaving({ channel, url: body.url, updated: Boolean(body.updated) });
      window.location.href = body.url;
    } catch {
      setError("Could not reach the server. Please try again.");
      setBusy(null);
    }
  }

  if (leaving)
    return (
      <Handover channel={leaving.channel} url={leaving.url} updated={leaving.updated} />
    );

  const placeholder =
    mode === "name" ? "Canary Wharf, Stratford, Chelsea…" : "E14, E15, SW3…";

  // What the list shows: everything that matches, minus what is already
  // chosen, best match first.
  //
  // The "E1 against E14" problem this used to work around is gone with the
  // list. Typing an exact district no longer commits it on the spot — which is
  // what made E14 unreachable once E1 had been typed — because choosing is now
  // a click or Enter on a row, and both districts are rows.
  // Uncapped: London has 594 outcodes and more named neighbourhoods, and the
  // list scrolls, so there is no reason to hide the tail behind "type more".
  const shown = matchAreas(options, typed, chosen.map((one) => one.code));

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

        {/* A combobox, written out rather than a <datalist>: the native one
            renders a dropdown the page cannot reach — its own width, its own
            type, its own colours — so on a styled form it reads as a browser
            dialog that wandered in. This one is ordinary markup, so it matches
            the field it belongs to. */}
        <div className="combo">
          <input
            id="area"
            type="text"
            role="combobox"
            aria-expanded={open}
            aria-controls="area-list"
            aria-autocomplete="list"
            aria-activedescendant={
              open && shown[cursor] ? `area-option-${shown[cursor].code}` : undefined
            }
            value={typed}
            autoComplete="off"
            placeholder={placeholder}
            onFocus={() => setOpen(true)}
            // Focus alone is not enough now that a pick closes the list: the
            // input still holds focus afterwards, so the click asking for the
            // list again would fire no focus event and look like a dead box.
            onClick={() => setOpen(true)}
            onChange={(event) => {
              setTyped(event.target.value);
              setAreaNote(null);
              // Back to the top of a list that has just changed under it:
              // keeping the old index would leave the highlight on whatever
              // row happens to be in that position now.
              setCursor(0);
              setOpen(true);
            }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                setOpen(true);
                if (shown.length === 0) return;
                const step = event.key === "ArrowDown" ? 1 : -1;
                setCursor((at) => (at + step + shown.length) % shown.length);
                return;
              }
              if (event.key === "Enter") {
                event.preventDefault();
                // The highlighted row if the list is showing one, and
                // otherwise whatever the text names — so typing a postcode in
                // full and pressing Enter still works without looking down.
                const pick = open ? shown[cursor] : undefined;
                if (pick) choose(pick);
                else commitTyped();
                return;
              }
              if (event.key === "Escape") {
                setOpen(false);
              }
            }}
            // Closed, and nothing added: picking is a click or Enter on a row.
            // Committing on blur meant clicking anywhere else on the page could
            // add an area nobody had chosen.
            onBlur={() => setOpen(false)}
          />

          {open && (
            <ul className="combo-list" id="area-list" role="listbox" ref={listRef}>
              {shown.length === 0 && (
                <li className="combo-empty">
                  {typed.trim()
                    ? `Nothing matches “${typed.trim()}”.`
                    : "Every area is already on your list."}
                </li>
              )}
              {shown.map((one, index) => (
                <li
                  key={one.code}
                  id={`area-option-${one.code}`}
                  role="option"
                  aria-selected={index === cursor}
                  className={index === cursor ? "combo-option on" : "combo-option"}
                  // The input keeps focus, so the list does not close out from
                  // under the click that is choosing from it.
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setCursor(index)}
                  onClick={() => choose(one)}
                >
                  <span className="combo-name">{one.name}</span>
                  {one.code !== one.name && (
                    <span className="combo-code">{one.code}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {full && <p className="hint">{`That is all ${maxDistricts} areas.`}</p>}

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

      {/* Straight after the areas, and before the rent: it decides whether the
          bedroom and bathroom sliders below are shown at all, so asking it
          later would move the fields under the hand already reaching for them. */}
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
      </fieldset>

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

      {/* Gone, not greyed out, when only rooms are wanted: a room is one room
          in somebody else's flat, so neither count says anything about it and
          a disabled slider only invites a second look. */}
      {!roomsOnly && (
        <>
          <div>
            <span className="field-label">Bedrooms</span>
            <RangeSlider
              min={BEDS_MIN}
              max={ROOMS_MAX}
              step={1}
              value={bedrooms}
              onChange={setBedrooms}
              ticks
              format={beds}
              openTop="+"
            />
          </div>

          <div>
            <span className="field-label">Bathrooms</span>
            <RangeSlider
              min={BATHS_MIN}
              max={ROOMS_MAX}
              step={1}
              value={bathrooms}
              onChange={setBathrooms}
              ticks
              format={rooms}
              openTop="+"
            />
            {roomsToo && (
              <small className="note">
                Bedroom and bathroom counts only narrow the flats and houses.
                Rooms come through regardless of where these sit.
              </small>
            )}
          </div>
        </>
      )}

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

      <fieldset aria-labelledby="furnishing-label">
        <span id="furnishing-label" className="field-label">Furnishing</span>
        <div className="choices">
          {furnished.map((option) => (
            <label key={option}>
              <input
                type="checkbox"
                name="furnished"
                value={option}
                defaultChecked={start.furnished.includes(option)}
              />{" "}
              {option}
            </label>
          ))}
        </div>
      </fieldset>

      <label>
        <span>Pets</span>
        <span className="choices">
          <label>
            <input
              type="checkbox"
              name="pets_allowed"
              defaultChecked={start.pets}
            />{" "}
            Must allow pets
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

      {/* What the header's buttons scroll to. On the actions rather than on the
          form, because the thing somebody pressing "Start free trial" is
          looking for is the button, and the filter above it is the part they
          read on the way down. */}
      <div className="hero-actions" id="start">
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
          <p className="offers-lead">
            Start with a free trial. Cancel anytime. No credit card required to
            start.
          </p>

          <div className={whatsappReady ? "offers" : "offers offers-one"}>
            <Choice
              channel="telegram"
              // Nobody has connected anything yet, so the button names what
              // happens rather than the plumbing. Which messenger is already
              // said by the logo on the button and the colour of the card.
              label="Start free trial"
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
                label="Start free trial"
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
          disabled={busy !== null}
          aria-describedby={ready ? undefined : "needs-area-save"}
        >
          {channel === "whatsapp" ? <WhatsAppMark /> : <TelegramMark />}
          {busy === channel ? "One moment…" : `Save and go back to ${where}`}
        </button>

        {!ready && (
          <p className="offer-needs" id="needs-area-save">
            Pick at least one area above first.
          </p>
        )}

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
  /** Whether an area has been chosen. Not what disables the button — see below. */
  ready: boolean;
  flag?: string;
}) {
  const { trialDays, prices, allowance } = offer;

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
        {/* Metered or not, said either way. "Unlimited" is worth a line of its
            own next to a messenger that is not. */}
        <li>
          <Tick />{" "}
          {allowance === null
            ? "Unlimited alerts"
            : `${allowance} alerts per month`}
        </li>
      </ul>

      {/* Lit, not greyed out, even with no area chosen yet.
          A disabled button is the one thing on the page that cannot say why it
          is disabled: it does not take a click, so it cannot answer one. The
          button stays live, the line under it says what is missing, and
          pressing it puts the cursor in the area box — which is the whole of
          what a disabled button was trying to prevent, done where somebody can
          read it. Still disabled while a submission is in flight, because that
          is about this button rather than about the form. */}
      <button
        type="submit"
        name="channel"
        value={channel}
        className={`cta cta-${channel}`}
        disabled={busy !== null}
        aria-describedby={ready ? undefined : `needs-area-${channel}`}
      >
        {mark}
        {busy === channel ? "One moment…" : label}
      </button>

      {!ready && (
        <p className="offer-needs" id={`needs-area-${channel}`}>
          Pick at least one area above first.
        </p>
      )}
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
  stops, ticks = false,
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
  /**
   * Print every stop under the track, not just the two ends.
   *
   * Only for a scale short enough to read: bedrooms and bathrooms are six
   * positions each, and without the numbers the only way to find out which one
   * a handle is on is to drag it and watch the figure above change. Rent and
   * size have far too many stops for this and keep their two ends.
   */
  ticks?: boolean;
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

      {ticks ? (
        /* One label per stop, each centred on where its handle sits. The
           strip is inset by half a thumb on both sides because that is the
           travel a native range input gives its thumb — without the inset the
           first and last labels sit a thumb's width away from their ends. */
        <div className="range2-ticks">
          {Array.from(
            { length: Math.floor((ceiling - floor) / tick) + 1 },
            (_, step) => floor + step * tick,
          ).map((position) => {
            const value = from(position);
            const inside = position >= lowAt && position <= highAt;
            return (
              <span
                key={position}
                className={inside ? "range2-tick on" : "range2-tick"}
                style={{ left: `${percent(position, floor, ceiling)}%` }}
              >
                {format(value)}
                {position === ceiling ? openTop : ""}
              </span>
            );
          })}
        </div>
      ) : (
        <div className="range2-ends">
          <span>{format(from(floor))}</span>
          <span>
            {format(from(ceiling))}
            {openTop}
          </span>
        </div>
      )}
    </div>
  );
}

// What is on screen while the messenger opens, and what stays there if it does
// not.
//
// `updated` is a changed filter rather than a new one: it is already saved and
// already being matched on, and the chat has the receipt — so there is nothing
// to press and nothing to connect, and saying "press Start" to somebody who
// has had alerts for a fortnight reads as though their search did not take.
function Handover({
  channel,
  url,
  updated,
}: {
  channel: Channel;
  url: string;
  updated: boolean;
}) {
  const app = channel === "whatsapp" ? "WhatsApp" : "Telegram";

  return (
    <div className="panel">
      <div className="done-tick">✓</div>
      <h1>{updated ? "Your search is updated" : "Your search is saved"}</h1>
      <p className="lede">
        {updated ? (
          <>
            Opening {app} now — the new filter is already running, and the chat
            has it in full. Only listings posted from now on.
          </>
        ) : (
          <>
            Opening {app} now. Press Start there and the alerts begin — only
            listings posted from that moment on, never a backlog.
          </>
        )}
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
