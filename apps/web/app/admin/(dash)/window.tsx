import Link from "next/link";

export const WINDOWS = [
  { key: "1h", hours: 1, label: "1h" },
  { key: "6h", hours: 6, label: "6h" },
  { key: "12h", hours: 12, label: "12h" },
  { key: "1d", hours: 24, label: "1d" },
  { key: "3d", hours: 72, label: "3d" },
  { key: "7d", hours: 168, label: "1w" },
] as const;

export type WindowKey = (typeof WINDOWS)[number]["key"];

// Today, not a week. A dashboard answers "what is happening" first and "what
// has been happening" second.
export const DEFAULT_WINDOW: WindowKey = "1d";

export function windowFrom(value: string | undefined): (typeof WINDOWS)[number] {
  return WINDOWS.find((one) => one.key === value) ?? WINDOWS[3];
}

// How wide a bucket keeps a chart readable at each width: about 30-60 points
// across, so a line has shape without turning into noise.
export function bucketMinutes(hours: number): number {
  if (hours <= 1) return 2;
  if (hours <= 6) return 10;
  if (hours <= 12) return 15;
  if (hours <= 24) return 30;
  if (hours <= 72) return 60;
  return 240;
}

export function WindowPicker({
  here,
  chosen,
  extra = {},
}: {
  here: string;
  chosen: WindowKey;
  extra?: Record<string, string | undefined>;
}) {
  const link = (key: string) => {
    const params = new URLSearchParams();
    for (const [name, value] of Object.entries(extra)) {
      if (value) params.set(name, value);
    }
    params.set("w", key);
    return `${here}?${params.toString()}`;
  };

  return (
    <div className="window-picker" role="group" aria-label="Time range">
      {WINDOWS.map((one) => (
        <Link
          key={one.key}
          href={link(one.key)}
          className={one.key === chosen ? "win win-on" : "win"}
        >
          {one.label}
        </Link>
      ))}
    </div>
  );
}
