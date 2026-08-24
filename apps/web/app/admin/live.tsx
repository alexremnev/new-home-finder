"use client";

// Keeping the console current.
//
// ── polling, and why not SSE ─────────────────────────────────────────────────
//
// This asks the server for the page again every so often. The alternative — an open
// connection the server pushes into — buys latency this page has no use for, and
// costs a great deal here:
//
//   * The data changes when a job runs, which is every couple of minutes. A push
//     arriving instantly would show the same figures a fifteen-second poll shows.
//   * On serverless the connection lives as long as a function invocation is allowed
//     to, which is minutes. So SSE here IS polling, plus reconnection logic, plus a
//     heartbeat, plus a class of bug that only appears after the socket has been open
//     a while.
//
// So: a poll, with the interval visible and switchable, and the age of what is on
// screen stated. That last part is the one thing a live dashboard must not get wrong
// — a stale number with no timestamp is worse than no number, because it is believed.
//
// `router.refresh()` re-runs the server components and swaps the result in. Nothing
// flashes and nothing is re-mounted: no skeleton, no scroll jump, and a filter typed
// into the URL survives.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const CHOICES = [0, 15, 60] as const;

export function Live() {
  const router = useRouter();
  const [every, setEvery] = useState<number>(15);
  const [age, setAge] = useState(0);

  useEffect(() => {
    // The age ticks whether or not refreshing is on, because "off" still needs to
    // say how old what you are looking at is.
    const clock = setInterval(() => setAge((seconds) => seconds + 1), 1000);
    return () => clearInterval(clock);
  }, []);

  useEffect(() => {
    if (every === 0) return;
    const timer = setInterval(() => {
      router.refresh();
      setAge(0);
    }, every * 1000);
    return () => clearInterval(timer);
  }, [every, router]);

  return (
    <div className="live">
      <span className={age > 90 ? "live-age stale" : "live-age"}>
        {age < 5 ? "just now" : `${age}s ago`}
      </span>
      <button
        type="button"
        className="ghost"
        onClick={() => {
          router.refresh();
          setAge(0);
        }}
      >
        Refresh
      </button>
      <div className="live-choices">
        {CHOICES.map((seconds) => (
          <button
            key={seconds}
            type="button"
            className={seconds === every ? "range-on" : undefined}
            onClick={() => setEvery(seconds)}
          >
            {seconds === 0 ? "Off" : `${seconds}s`}
          </button>
        ))}
      </div>
    </div>
  );
}
