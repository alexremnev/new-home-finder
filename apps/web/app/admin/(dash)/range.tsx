"use client";

import { useRouter, usePathname, useSearchParams } from "next/navigation";
import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
  useTransition, type ReactNode,
} from "react";

import {
  DEFAULT_SPAN, SPAN_PRESETS, absoluteKey, londonToMs, msToLondon, spanFrom,
} from "./span";

// The one control that changes what every panel is about, and the one piece of
// state that says the dashboard is busy changing it.
//
// ── why a navigation rather than a fetch ─────────────────────────────────
//
// The range lives in the URL, so it is shareable, survives a reload and comes
// back with the browser's Back button. Changing it is therefore a navigation —
// but an asynchronous one: `router.push` inside a transition re-renders the
// server components in place, streaming each panel in as its query finishes.
// Nothing unmounts, the scroll position is kept, and `pending` stays true for
// the whole of it, which is what dims the body and draws the top bar's
// progress line.
//
// `RangeFrame` owns that transition because the thing it has to mark as busy
// (the body) is a sibling of the thing that starts it (the picker).

type Busy = { pending: boolean; go: (href: string) => void };

const Nav = createContext<Busy>({ pending: false, go: () => {} });

export function RangeFrame({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [pending, start] = useTransition();

  const go = useCallback(
    (href: string) => {
      // scroll: false — the panel you were reading when you changed the range
      // is the panel you want to still be looking at.
      start(() => router.push(href, { scroll: false }));
    },
    [router],
  );

  return (
    <Nav.Provider value={{ pending, go }}>
      <div className={pending ? "dash dash-busy" : "dash"} aria-busy={pending}>
        {children}
      </div>
    </Nav.Provider>
  );
}

export function RangePicker() {
  const { pending, go } = useContext(Nav);
  const here = usePathname();
  const params = useSearchParams();
  const span = spanFrom(params.get("w") ?? DEFAULT_SPAN);

  const [open, setOpen] = useState(false);
  const [calendar, setCalendar] = useState(false);
  const box = useRef<HTMLDivElement>(null);

  // Closed by a click anywhere else and by Escape, which is what every other
  // menu on the web does and so the only thing nobody has to be told.
  useEffect(() => {
    if (!open) return;
    const away = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  const href = (key: string) => {
    const next = new URLSearchParams(params.toString());
    next.set("w", key);
    return `${here}?${next.toString()}`;
  };

  const choose = (key: string) => {
    setOpen(false);
    setCalendar(false);
    if (key !== span.key) go(href(key));
  };

  return (
    <div className="range" ref={box}>
      <button
        type="button"
        className={open ? "range-button range-button-on" : "range-button"}
        onClick={() => setOpen((was) => !was)}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <Clock />
        <span className="range-label">{span.label}</span>
        {pending && <span className="range-working" aria-label="Loading" />}
        <span className="range-chevron" aria-hidden="true">
          ▾
        </span>
      </button>

      {open && (
        <div className="range-menu" role="dialog" aria-label="Time range">
          {!calendar ? (
            <>
              <ul className="range-list">
                {SPAN_PRESETS.map((one) => (
                  <li key={one.key}>
                    <button
                      type="button"
                      className={one.key === span.key ? "range-pick range-pick-on" : "range-pick"}
                      onClick={() => choose(one.key)}
                    >
                      {one.label}
                      {one.key === span.key && <span aria-hidden="true">✓</span>}
                    </button>
                  </li>
                ))}
              </ul>
              <div className="range-rule" />
              <button
                type="button"
                className={span.from ? "range-pick range-pick-on" : "range-pick"}
                onClick={() => setCalendar(true)}
              >
                {span.from ? span.label : "Select from calendar…"}
                <span aria-hidden="true">›</span>
              </button>
            </>
          ) : (
            <Calendar
              from={span.from ?? Date.now() - span.mins * 60_000}
              to={span.to ?? Date.now()}
              onBack={() => setCalendar(false)}
              onApply={(from, to) => choose(absoluteKey(from, to))}
            />
          )}
        </div>
      )}
    </div>
  );
}

// Two instants, typed or picked. A native datetime-local input rather than a
// grid of days drawn by hand: it brings its own calendar, its own keyboard
// handling and its own locale, and it is the one control a browser already
// knows how to make accessible.
function Calendar({
  from,
  to,
  onBack,
  onApply,
}: {
  from: number;
  to: number;
  onBack: () => void;
  onApply: (from: number, to: number) => void;
}) {
  const [start, setStart] = useState(() => msToLondon(from));
  const [end, setEnd] = useState(() => msToLondon(to));

  const fromMs = londonToMs(start);
  const toMs = londonToMs(end);
  const wrong =
    fromMs === null || toMs === null
      ? "Both ends are needed."
      : fromMs >= toMs
        ? "The start has to come before the end."
        : null;

  // The browser's own limit, so the picker cannot offer a future it has no
  // rows for. Expressed in London, like the values themselves.
  const latest = msToLondon(Date.now());

  return (
    <form
      className="range-calendar"
      onSubmit={(event) => {
        event.preventDefault();
        if (fromMs !== null && toMs !== null && !wrong) onApply(fromMs, toMs);
      }}
    >
      <button type="button" className="range-back" onClick={onBack}>
        ‹ Presets
      </button>

      <label>
        From
        <input
          type="datetime-local"
          value={start}
          max={latest}
          onChange={(event) => setStart(event.target.value)}
        />
      </label>
      <label>
        To
        <input
          type="datetime-local"
          value={end}
          max={latest}
          onChange={(event) => setEnd(event.target.value)}
        />
      </label>

      <p className="range-note">{wrong ?? "London time."}</p>

      <button type="submit" className="range-apply" disabled={Boolean(wrong)}>
        Apply
      </button>
    </form>
  );
}

function Clock() {
  return (
    <svg className="range-clock" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path
        d="M8 4.6V8l2.4 1.6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}
