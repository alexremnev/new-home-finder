"use client";

import { Suspense, useState, useTransition, type ReactNode } from "react";

import { Why } from "./charts";

// One card, with its own refresh.
//
// The bar's Refresh re-ran every query on the page and replaced the whole
// thing; wanting one table's numbers again meant paying for all of them and
// losing your scroll position. So each card carries a button that re-runs only
// its own query and swaps only its own contents.
//
// The mechanism is a server action passed in as `refresh`: it runs the same
// query the card was first rendered from and returns the rendered result, so
// there is one definition of what a card contains rather than a page copy and
// an endpoint copy.
export function Panel({
  title,
  why,
  refresh,
  wide = false,
  foldable = false,
  children,
}: {
  title?: string;
  why?: string;
  /** A server action returning this card's contents, freshly queried. */
  refresh?: () => Promise<ReactNode>;
  wide?: boolean;
  /**
   * Foldable, for a card that sits above everything else and is not always
   * what you came for. Open by default — a problem nobody sees is a problem
   * nobody fixes.
   */
  foldable?: boolean;
  children: ReactNode;
}) {
  // The first contents come from the page's own render; every later set comes
  // from the action. Null means "show what the server gave us".
  const [fresh, setFresh] = useState<ReactNode>(null);
  const [pending, start] = useTransition();
  const [failed, setFailed] = useState(false);

  const again = () => {
    if (!refresh) return;
    setFailed(false);
    start(async () => {
      try {
        setFresh(await refresh());
      } catch {
        // A failed refresh leaves the numbers that were already there. Wiping
        // them would turn a hiccup into an empty card.
        setFailed(true);
      }
    });
  };

  const head = (title || refresh) && (
    <div className="panel-head">
      {title && (
        <h2>
          {title}
          {why && <Why text={why} />}
        </h2>
      )}
      {refresh && (
        <button
          type="button"
          className="panel-refresh"
          onClick={(event) => {
            // Inside a <summary> a click would otherwise fold the card.
            event.preventDefault();
            event.stopPropagation();
            again();
          }}
          disabled={pending}
          aria-label={`Refresh ${title ?? "this panel"}`}
          title={failed ? "That refresh failed — try again" : "Refresh just this"}
        >
          <Spinner spinning={pending} />
          {failed && <span className="panel-warn">!</span>}
        </button>
      )}
    </div>
  );

  // The action may hand back a component that fetches its own data, so the
  // boundary is here rather than at each call site.
  const contents = <Suspense fallback={<PanelWait />}>{fresh ?? children}</Suspense>;

  if (foldable) {
    return (
      <details className={wide ? "card panel panel-wide panel-fold" : "card panel panel-fold"} open>
        <summary>{head}</summary>
        {contents}
      </details>
    );
  }

  return (
    <div className={wide ? "card panel panel-wide" : "card panel"}>
      {head}
      {contents}
    </div>
  );
}

// Shown while a card's first query is still running, so the page arrives in one
// piece instead of waiting for its slowest panel.
export function PanelWait() {
  return <div className="panel-wait" aria-hidden="true" />;
}

function Spinner({ spinning }: { spinning: boolean }) {
  return (
    <svg
      className={spinning ? "panel-spin panel-spin-on" : "panel-spin"}
      viewBox="0 0 16 16"
      aria-hidden="true"
      focusable="false"
    >
      {/* An arrow that goes round: the same shape whether it is turning or not,
          so the button does not change size when pressed. */}
      <path
        d="M13.5 8a5.5 5.5 0 1 1-1.6-3.9M13.5 1.5v3.2h-3.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
