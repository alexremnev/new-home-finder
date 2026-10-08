"use client";

import { useState, useTransition, type MouseEvent, type ReactNode } from "react";

import { moreLog } from "./more";
import { Paged } from "./paged";

// The run log's two filters, changed without reloading the dashboard.
//
// ── why not a link ──────────────────────────────────────────────────────
//
// They used to be <Link>s to `/admin?job=…`, and the page is force-dynamic:
// picking `drain` re-ran every query on the tab — health, portals, duplicates,
// four charts — to change twelve lines in one card. The range picker navigates
// because the range is what every panel is about; these two filters are about
// this panel only, so they fetch this panel only, through the same server
// action the pager already uses.
//
// The filters still end up in the URL, because they are part of what you would
// send somebody, but by `replaceState` rather than a navigation: the address
// bar is updated, a reload comes back to the same list, and no server
// component re-renders. Cmd-click and "copy link address" keep working because
// each chip is still a real <a> with a real href.

type Page = { rows: ReactNode; more: boolean; total: number };

export function LogView({
  span,
  jobNames,
  job: jobAtFirst,
  level: levelAtFirst,
  per,
  first,
}: {
  span: string;
  jobNames: string[];
  job?: string;
  level?: string;
  /** The server's page size, for the footer's count. */
  per: number;
  /** The first page, rendered by the server for the filters in the URL. */
  first: Page;
}) {
  const [job, setJob] = useState(jobAtFirst);
  const [level, setLevel] = useState(levelAtFirst);
  const [page, setPage] = useState<Page>(first);
  const [failed, setFailed] = useState(false);
  const [pending, start] = useTransition();

  const bad = Boolean(level);

  const href = (nextJob?: string, nextLevel?: string) => {
    const query = new URLSearchParams();
    for (const [name, value] of Object.entries({
      w: span,
      job: nextJob,
      level: nextLevel,
    })) {
      if (value) query.set(name, value);
    }
    return `/admin?${query.toString()}`;
  };

  const choose = (nextJob?: string, nextLevel?: string) => {
    if (nextJob === job && nextLevel === level) return;
    setJob(nextJob);
    setLevel(nextLevel);
    setFailed(false);
    window.history.replaceState(null, "", href(nextJob, nextLevel));
    start(async () => {
      try {
        setPage(await moreLog(span, nextJob, nextLevel, 1));
      } catch {
        setFailed(true);
      }
    });
  };

  // A modified click is somebody asking for a second tab or a copied link —
  // that is the browser's job, not ours.
  const click = (nextJob?: string, nextLevel?: string) =>
    (event: MouseEvent<HTMLAnchorElement>) => {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      choose(nextJob, nextLevel);
    };

  return (
    <>
      <div className="dash-head" style={{ marginBottom: "0.6rem" }}>
        <div className="window-picker">
          <a
            className={!job ? "win win-on" : "win"}
            href={href(undefined, level)}
            onClick={click(undefined, level)}
          >
            all jobs
          </a>
          {jobNames.map((name) => (
            <a
              key={name}
              className={job === name ? "win win-on" : "win"}
              href={href(name, level)}
              onClick={click(name, level)}
            >
              {name}
            </a>
          ))}
        </div>
        <div className="window-picker">
          <a
            className={bad ? "win win-on" : "win"}
            href={href(job, bad ? undefined : "bad")}
            onClick={click(job, bad ? undefined : "bad")}
          >
            only with problems
          </a>
        </div>
        {pending && <span className="range-working" aria-label="Loading" />}
      </div>

      <div className={pending ? "log-rows log-rows-busy" : "log-rows"} aria-busy={pending}>
        {failed ? (
          <p className="metric-note bad">
            That filter failed to load.{" "}
            <button
              type="button"
              className="more-button"
              onClick={() => {
                setFailed(false);
                start(async () => {
                  try {
                    setPage(await moreLog(span, job, level, 1));
                  } catch {
                    setFailed(true);
                  }
                });
              }}
            >
              Try again
            </button>
          </p>
        ) : page.total === 0 ? (
          <p className="metric-note">
            {bad
              ? "No run in this range had anything to complain about."
              : "No job ran in this range."}
          </p>
        ) : (
          // Keyed on the filters: a different question is a different list, and
          // the pages already loaded for the old one must not be kept.
          <Paged
            key={`${span}|${job ?? ""}|${level ?? ""}`}
            load={(next) => moreLog(span, job, level, next)}
            more={page.more}
            per={per}
            total={page.total}
            unit="runs"
          >
            {page.rows}
          </Paged>
        )}
      </div>
    </>
  );
}
