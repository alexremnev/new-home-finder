"use client";

import { Fragment, useState, useTransition, type ReactNode } from "react";

// "Show more", under a list, loading the next page without touching the page.
//
// ── why not the old pager ────────────────────────────────────────────────
//
// Page 2 of the run log used to be a link: it put `?p=2` in the URL, which
// re-ran every query on the tab and rebuilt the whole page to change twelve
// lines in one card. And the panels did not agree on what a page number meant,
// so one `?p=` served two cards at once — turning the log to page three also
// turned the subscriber table to page three.
//
// Now each list keeps its own position in its own component. The first page
// comes from the server render; every page after it comes from a server action
// that renders the same rows, and they are appended rather than swapped, so
// nothing already read moves or disappears.
//
// The action returns rendered rows rather than data because a row is a server
// component with database types inside it: handing back markup keeps one
// definition of what a row looks like instead of a server copy and a client
// copy that have to be kept in step.

export type MorePage = {
  rows: ReactNode;
  /** Whether a page after this one exists. */
  more: boolean;
};

export function Paged({
  load,
  children,
  more: moreToStart,
  head,
  grid = false,
  per,
  total,
  unit,
  label = "Show more",
}: {
  /** Renders page `n` of this list, 1-based; page 1 is already on screen. */
  load: (page: number) => Promise<MorePage>;
  /** The first page, rendered by the server. */
  children: ReactNode;
  /** Whether the server found a second page — whether to offer the button. */
  more: boolean;
  /**
   * A table's header row. Given one, every page's rows go into the same
   * <tbody>: appending a second table below the first would give one list two
   * sets of column widths.
   */
  head?: ReactNode;
  grid?: boolean;
  /** Page size, for the footer's count. */
  per?: number;
  /** Only where the query counts cheaply. Left out, the footer says "shown". */
  total?: number;
  unit?: string;
  label?: string;
}) {
  const [extra, setExtra] = useState<ReactNode[]>([]);
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(moreToStart);
  const [failed, setFailed] = useState(false);
  const [pending, start] = useTransition();

  const next = () => {
    setFailed(false);
    start(async () => {
      try {
        const got = await load(page + 1);
        // Appended, not replaced: a slow or failed query leaves everything
        // already on screen exactly where it was.
        setExtra((was) => [...was, got.rows]);
        setPage((was) => was + 1);
        setMore(got.more);
      } catch {
        setFailed(true);
      }
    });
  };

  const rows = (
    <>
      {children}
      {extra.map((one, index) => (
        <Fragment key={index}>{one}</Fragment>
      ))}
    </>
  );

  const shown =
    per === undefined
      ? undefined
      : total === undefined
        ? per * page
        : Math.min(per * page, total);

  return (
    <>
      {head ? (
        <div className="scroll-x">
          <table className={grid ? "grid" : undefined}>
            <thead>{head}</thead>
            <tbody>{rows}</tbody>
          </table>
        </div>
      ) : (
        rows
      )}

      {(more || failed || shown !== undefined) && (
        <div className="more">
          {(more || failed) && (
            <button
              type="button"
              className="more-button"
              onClick={next}
              disabled={pending}
            >
              {pending ? "Loading…" : failed ? "That failed — try again" : label}
            </button>
          )}
          {shown !== undefined && (
            <span className="more-count">
              {total === undefined
                ? `${shown} shown`
                : more
                  ? `${shown} of ${total}`
                  : `all ${total}`}
              {unit ? ` ${unit}` : ""}
            </span>
          )}
        </div>
      )}
    </>
  );
}
