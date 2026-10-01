import type { ReactNode } from "react";

import { Why } from "./charts";

// One card: a heading, an explanation and some contents.
//
// No refresh button. Each card used to carry one, which meant a server action
// per card, a copy of every card's arguments bound into a button, and a second
// definition of what the card contains. Changing the range re-queries every
// panel asynchronously anyway — that is the only reason anybody pressed them —
// so the button, the actions and the spinner are gone and this is a plain
// server component again.
export function Panel({
  title,
  why,
  wide = false,
  foldable = false,
  children,
}: {
  title?: string;
  why?: string;
  wide?: boolean;
  /**
   * Foldable, for a card that sits above everything else and is not always
   * what you came for. Open by default — a problem nobody sees is a problem
   * nobody fixes.
   */
  foldable?: boolean;
  children: ReactNode;
}) {
  const head = title && (
    <div className="panel-head">
      <h2>
        {title}
        {why && <Why text={why} />}
      </h2>
    </div>
  );

  if (foldable) {
    return (
      <details
        className={wide ? "card panel panel-wide panel-fold" : "card panel panel-fold"}
        open
      >
        <summary>{head}</summary>
        {children}
      </details>
    );
  }

  return (
    <div className={wide ? "card panel panel-wide" : "card panel"}>
      {head}
      {children}
    </div>
  );
}

// Shown while a card's query is still running, so the page arrives in one
// piece instead of waiting for its slowest panel.
export function PanelWait() {
  return <div className="panel-wait" aria-hidden="true" />;
}
