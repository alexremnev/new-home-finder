"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

import type { Criteria } from "@/lib/criteria";
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
  FLEX_DAYS,
  nearestFlex,
  prefill,
  RENT_MAX,
  RENT_MIN,
  RENT_STEP,
  ROOMS_MAX,
  SQFT_PER_SQM,
} from "@/lib/prefill";

import { TelegramMark, Tick, WhatsAppMark } from "./logos";
import { PhonePreview } from "./phone";

type Props = {
  /**
   * The hero's headline and lede, drawn as the first cell of the grid this
   * component lays out. It is passed in rather than written here because the
   * words belong to the page — but the grid belongs to the form, and the phone
   * can only be raised to the level of the first line of the heading if the
   * heading is one of the grid's own children.
   */
  say?: ReactNode;
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
   * The extra free day a quiet first day can be given, on top of `trialDays`,
   * or null where there is none. WhatsApp only — see `giftDaysOn`.
   *
   * Said as a second line rather than added to the first. A day on the plan
   * plus a day for a trial that delivered almost nothing is two days for some
   * people and one for the rest, and "2 days free" on the card would be the
   * version of that sentence we cannot keep.
   */
  giftDays: number | null;
  /**
   * Every price this messenger is sold at, cheapest first, and what each one
   * includes. Both channels have two — a week and a month — so the band is a
   * list rather than a single figure.
   *
   * `allowance` is the messages included at that price, from
   * `plans.message_allowance`, or null where there is no limit. Null on every
   * Telegram plan, because delivery there is free and there is nothing to
   * meter. Read and never typed into the copy, so the card cannot promise a
   * number the delivery rules do not honour — which it did while it was a
   * string in the list below, and did again while it was one figure per
   * channel standing for two plans that include different amounts.
   */
  prices: { pence: number; unit: string; allowance: number | null }[];
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
  say = null,
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
  // Which messenger the alerts are going to. One piece of state for two jobs:
  // it is what the submit button posts, and it is what the phone beside the
  // card is drawn as — the preview used to own a switch of its own, which left
  // somebody looking at a WhatsApp screenshot while signing up on Telegram.
  //
  // Telegram first because delivery there is free, which is why it is the one
  // the plans below recommend. Somebody editing an existing filter is already
  // on a messenger, and that one is not up for discussion here.
  const [channel, setChannel] = useState<Channel>(returning?.channel ?? "telegram");
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
  // How far either side of that date a listing may be available, in days, as
  // one of the four the select offers. Nought is "any date", which is the same
  // state as an empty date box — choosing it clears the date, and clearing the
  // date puts it back — so the two controls can never disagree about whether
  // the date is being filtered on at all.
  const [dayWindow, setDayWindow] = useState(() =>
    start.availableOn === "" ? 0 : nearestFlex(start.dayWindow),
  );
  const [area, setArea] = useState<[number, number]>(start.size);
  // Controlled, and the only control on the form whose default is "on": an
  // uncontrolled checkbox would send nothing when it is left alone, and nothing
  // is the shape the submit below uses for "duplicates wanted".
  const [oneAlertPerFlat, setOneAlertPerFlat] = useState(start.oneAlertPerFlat);
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

  // The free days this messenger is good for: the plan's own, plus the one a
  // quiet first day is given back on WhatsApp. Added up the same way the plans
  // band adds them, so the button and the card cannot say different numbers.
  const { trialDays, giftDays } = offers[channel];
  const freeDays = trialDays === null ? null : trialDays + (giftDays ?? 0);

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
    // Nothing from the three controls a rooms-only search does not show. They
    // are not on the screen, so whatever they are still holding is a filter
    // nobody set — and a floor area filed against a room in a shared flat
    // quietly matches nothing, because a room is advertised by its own size
    // and rarely by any size at all.
    if (!roomsOnly) {
      if (bedrooms[0] > BEDS_MIN) payload.bedrooms_min = String(bedrooms[0]);
      if (bedrooms[1] < ROOMS_MAX) payload.bedrooms_max = String(bedrooms[1]);
      if (bathrooms[0] > BATHS_MIN) payload.bathrooms_min = String(bathrooms[0]);
      if (bathrooms[1] < ROOMS_MAX) payload.bathrooms_max = String(bathrooms[1]);
      // Metres on the slider, feet in the database — converted here, once. The
      // top handle at its ceiling means "and above", so no maximum is sent.
      if (area[0] > AREA_MIN) payload.area_min = String(asSqft(area[0]));
      if (area[1] < AREA_MAX) payload.area_max = String(asSqft(area[1]));
    }

    // Only when the toggle is off, because the stored field means "send the
    // copies" and its absence is the suppressing default — see `Criteria`.
    if (!oneAlertPerFlat) payload.send_duplicates = "on";

    // A date and a window, or neither. `dayWindow` is nought only while the
    // date box is empty, so the second test is a guard rather than a case —
    // but a date that reached here with no window would be sent as a single
    // day, which is the one reading nobody chose.
    const wanted = String(data.get("available_on") ?? "").trim();
    delete payload.available_on;
    if (wanted && dayWindow > 0) {
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
    <div className="hero-grid">
      {say}

      <form onSubmit={submit} className="filter-card">
        <div className="filter-head">
          <h2>Your alert</h2>
          <span>Criteria</span>
        </div>
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
          in somebody else's flat, so none of these three says anything about
          it — not the counts, and not the floor area, which a room ad gives as
          the room rather than the flat and usually does not give at all. A
          disabled slider only invites a second look. */}
      {!roomsOnly && (
        <>
          {/* Side by side, at half the rent slider's width: they are the two
              halves of one question — how many rooms — and six stops each
              still reads at half the width. Rent and floor area keep the full
              row, because those are the scales where a pixel is a hundred
              pounds or ten square metres. */}
          <div className="pair">
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
            </div>

            {/* Across both, because it is true of both. */}
            {roomsToo && (
              <small className="note pair-note">
                Bedroom and bathroom counts only narrow the flats and houses.
                Rooms come through regardless of where these sit.
              </small>
            )}
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
            {/* The same numbers in feet, under the metres. Not a second
                control: one slider, read twice. */}
            <p className="range2-metric">
              {area[0] === AREA_MIN && area[1] === AREA_MAX
                ? "Any size"
                : `${sqft(area[0])} — ${sqft(area[1])}${area[1] === AREA_MAX ? "+" : ""}`}
            </p>
          </div>
        </>
      )}

      <div>
        <span className="field-label">Desired let available date</span>
        <div className="date-row">
          <label className="date-on">
            <span>Available from</span>
            <input
              type="date"
              name="available_on"
              value={availableOn}
              onChange={(event) => {
                setAvailableOn(event.target.value);
                // A date with "any date" beside it filters on nothing, so the
                // first date typed brings the narrowest window with it. Any
                // window already chosen is left alone.
                if (event.target.value !== "" && dayWindow === 0) {
                  setDayWindow(FLEX_DAYS[0]);
                }
              }}
            />
          </label>
          {/* Second in the markup as well as on the screen, so tabbing goes
              date then window — the order they are decided in. */}
          <label className="date-days">
            <span>Flexibility</span>
            <select
              value={dayWindow}
              onChange={(event) => {
                const next = Number(event.target.value);
                setDayWindow(next);
                // "Any date" is the empty date box, so choosing it empties the
                // box. The alternative — a date sitting above a window that
                // says the date is not being used — is a control contradicting
                // the one beside it.
                if (next === 0) setAvailableOn("");
              }}
            >
              <option value={0}>Any date</option>
              {FLEX_DAYS.map((days) => (
                <option key={days} value={days}>
                  ± {days} days
                </option>
              ))}
            </select>
          </label>
        </div>
        <small className="note">
          {availableOn === ""
            ? "Leave blank for any date — a listing that gives no date is sent either way."
            : `Listings available within ${dayWindow} days either side of it.`}
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

      {/* The two answers that are yes or no, as two switches on one row. Both
          used to carry a paragraph of their own, and between them they were a
          sixth of the card for two settings most people leave alone. The
          paragraphs are still here as the tooltip each switch carries — on the
          control, where somebody who wants to know goes looking, rather than
          permanently underneath it.

          Switches and not the pills the rest of the card uses: a pill says
          "one of these", and neither of these is a set to choose from. */}
      <div className="pair pair-switches">
        <label
          className="toggle"
          title={
            "Only leaves out listings that say pets are not allowed. " +
            "Silence still comes through."
          }
        >
          <input
            type="checkbox"
            role="switch"
            name="pets_allowed"
            defaultChecked={start.pets}
          />
          <span className="toggle-label">Must allow pets</span>
        </label>

        {/* Not a filter: it narrows nothing, it says what happens when two
            portals carry the same flat. One agent syndicates a flat to
            Rightmove and Zoopla and it arrives twice, minutes apart, which is
            the complaint this answers — but the second copy is a second link,
            and somebody comparing agents wants both. On by default, which is
            why `submit` posts the inverse. */}
        <label
          className="toggle"
          title={
            oneAlertPerFlat
              ? "The same flat on two portals is sent once — the listing " +
                "that appeared first."
              : "Every copy is sent, so a flat carried by two portals " +
                "arrives twice, once per link."
          }
        >
          <input
            type="checkbox"
            role="switch"
            // Deliberately unnamed: the value is posted by `submit` from this
            // state, inverted. A name here would put the unflipped answer in
            // the form data as well.
            checked={oneAlertPerFlat}
            onChange={(event) => setOneAlertPerFlat(event.target.checked)}
          />
          <span className="toggle-label">Hide duplicates</span>
        </label>
      </div>

        {/* What the header's buttons and the plans below scroll to: the last
            thing in the card, which is the button and the one question left to
            answer before it. The filter above is the part read on the way
            down. */}
        <div className="send-to" id="start">
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
              <span className="field-label">Where should we send alerts?</span>

              {/* The one decision the form cannot make for anybody, asked
                  where it is acted on. Buttons rather than radios because the
                  answer also redraws the phone beside the card — so pressing
                  one shows what it buys as well as choosing it. With no
                  WhatsApp number configured there is nothing to choose
                  between, and the pair is not drawn at all. */}
              {whatsappReady && (
                <div className="send-pick" role="group" aria-label="Messenger">
                  {(
                    [
                      ["telegram", "Telegram", <TelegramMark key="t" />],
                      ["whatsapp", "WhatsApp", <WhatsAppMark key="w" />],
                    ] as const
                  ).map(([one, name, mark]) => (
                    <button
                      key={one}
                      type="button"
                      className={
                        channel === one ? `pick pick-${one} on` : `pick pick-${one}`
                      }
                      aria-pressed={channel === one}
                      onClick={() => setChannel(one)}
                    >
                      {mark}
                      {name}
                    </button>
                  ))}
                </div>
              )}

              {/* One submit, carrying whichever messenger is chosen: `submit`
                  reads the pressed button's value, so the choice above travels
                  with the press and nothing else has to be posted. Lit even
                  with no area chosen — see the note underneath. */}
              <button
                type="submit"
                name="channel"
                value={channel}
                className={`cta cta-${channel}`}
                disabled={busy !== null}
                aria-describedby={chosen.length > 0 ? undefined : "needs-area"}
              >
                {channel === "whatsapp" ? <WhatsAppMark /> : <TelegramMark />}
                {busy === channel ? "One moment…" : "Start free trial"}
              </button>

              {chosen.length === 0 && (
                <p className="offer-needs" id="needs-area">
                  Pick at least one area above first.
                </p>
              )}

              {/* The length comes from `plans`, so the line cannot promise a
                  trial the bot will not grant — and it is omitted rather than
                  guessed at where no plan is configured. */}
              {freeDays !== null && (
                <p className="send-note">
                  {freeDays} {freeDays === 1 ? "day" : "days"} free on{" "}
                  {channel === "whatsapp" ? "WhatsApp" : "Telegram"}. No card
                  required, cancel anytime.
                </p>
              )}
            </>
          )}
        </div>
      </form>

      {/* A picture of the product rather than the product, which is why it is
          outside the form and why a narrow screen does not draw it: there it
          would sit between somebody and the button they came for. */}
      <aside className="hero-aside">
        <PhonePreview channel={channel} />
      </aside>
    </div>
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
