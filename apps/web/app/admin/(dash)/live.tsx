"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

const CHOICES = [0, 15, 60] as const;

export function Live() {
  const router = useRouter();
  // Off by default. Auto-refresh moves the page under whoever is reading it,
  // and re-runs every query on it; asking for it is a choice, not a default.
  const [every, setEvery] = useState<number>(0);
  const [age, setAge] = useState(0);

  useEffect(() => {

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
