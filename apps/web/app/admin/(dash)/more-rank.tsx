"use client";

import { useState, useTransition } from "react";

import { Rank, type Point } from "./charts";

// "Show more" for a ranked bar list.
//
// Unlike a table, this one is handed rows rather than rendered markup: the bars
// are drawn as a share of the largest value in the list, so a second batch
// appended as its own block would be drawn to its own scale and a smaller
// number could end up with a longer bar. The whole list is redrawn each time
// instead, which is both correct and what the eye expects.
export function MoreRank({
  rows: first,
  more: moreToStart,
  load,
  suffix,
  unit,
}: {
  rows: Point[];
  more: boolean;
  load: (page: number) => Promise<{ rows: Point[]; more: boolean }>;
  suffix?: string;
  unit: string;
}) {
  const [rows, setRows] = useState(first);
  const [page, setPage] = useState(1);
  const [more, setMore] = useState(moreToStart);
  const [failed, setFailed] = useState(false);
  const [pending, start] = useTransition();

  const next = () => {
    setFailed(false);
    start(async () => {
      try {
        const got = await load(page + 1);
        setRows((was) => [...was, ...got.rows]);
        setPage((was) => was + 1);
        setMore(got.more);
      } catch {
        setFailed(true);
      }
    });
  };

  return (
    <>
      <Rank data={rows} suffix={suffix} />
      {(more || failed) && (
        <div className="more">
          <button type="button" className="more-button" onClick={next} disabled={pending}>
            {pending ? "Loading…" : failed ? "That failed — try again" : "Show more"}
          </button>
          <span className="more-count">
            {rows.length} {unit}
          </span>
        </div>
      )}
    </>
  );
}
