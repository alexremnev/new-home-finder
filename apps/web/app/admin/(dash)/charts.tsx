"use client";

import { useState } from "react";

// Dense, small, quiet. A dashboard chart is read in a glance beside five
// others, so it carries no legend it can do without, no gradient, and no label
// that repeats what the card's heading already said.
//
// One hue for magnitude (a quantity of one thing at different times), fixed
// categorical hues where two things are compared, and nothing coloured by rank.

const INK = "#1c2330";
const SOFT = "#6b7684";
const FAINT = "#97a0ad";
const GRID = "#eef0f3";
const LINE = "#5b4cf5";
const LINE_WASH = "rgba(91, 76, 245, 0.10)";
const OK = "#1f9d55";
const BAD = "#d6353b";

const H = 120;

export type Point = { label: string; value: number };

const comma = (n: number) => n.toLocaleString("en-GB");

function niceMax(peak: number): number {
  if (peak <= 4) return 4;
  const size = 10 ** Math.floor(Math.log10(peak));
  for (const step of [1, 2, 2.5, 5, 10]) {
    const top = step * size;
    if (top >= peak) return top;
  }
  return 10 * size;
}

function Empty({ height = H }: { height?: number }) {
  return (
    <div
      style={{
        height,
        display: "grid",
        placeItems: "center",
        color: FAINT,
        fontSize: 11,
        background: GRID,
        borderRadius: 4,
      }}
    >
      nothing in this window
    </div>
  );
}

// Only the ends and the middle, so a 60-point series does not stack its labels.
function ticksOf(points: { label: string }[]): number[] {
  if (points.length < 2) return points.map((_, i) => i);
  const middle = Math.floor((points.length - 1) / 2);
  return [0, middle, points.length - 1];
}

const clock = (label: string) => (label.length > 10 ? label.slice(11, 16) : label.slice(5));

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// "2026-09-17 14:30" -> "17 Sep, 14:30"; "2026-09-17" -> "17 Sep 2026".
function spell(label: string): string {
  const [date, time] = label.split(" ");
  const [year, month, day] = (date ?? "").split("-");
  const name = month ? MONTHS[Number(month) - 1] : undefined;
  if (!name || !day) return label;
  return time ? `${Number(day)} ${name}, ${time}` : `${Number(day)} ${name} ${year}`;
}

// Where the pointer is, as an index into the series. Read from the element's
// own box rather than from the svg, whose viewBox is stretched.
function indexFrom(event: React.MouseEvent<HTMLDivElement>, count: number): number {
  const box = event.currentTarget.getBoundingClientRect();
  const across = (event.clientX - box.left) / Math.max(1, box.width);
  return Math.min(count - 1, Math.max(0, Math.round(across * (count - 1))));
}

function Tip({ at, count, children }: { at: number; count: number; children: React.ReactNode }) {
  // Pinned inside its own box at the ends, so a tooltip on the first or last
  // point is not half off the card.
  const left = count < 2 ? 50 : (at / (count - 1)) * 100;
  const side = left < 22 ? "0%" : left > 78 ? "100%" : `${left}%`;
  const shift = left < 22 ? "0" : left > 78 ? "-100%" : "-50%";
  return (
    <div
      style={{
        position: "absolute",
        left: side,
        transform: `translateX(${shift})`,
        bottom: "100%",
        zIndex: 5,
        padding: "0.3rem 0.45rem",
        borderRadius: 5,
        background: INK,
        color: "#fff",
        fontSize: 11,
        lineHeight: 1.35,
        whiteSpace: "nowrap",
        pointerEvents: "none",
      }}
    >
      {children}
    </div>
  );
}

export function Spark({
  data,
  suffix = "",
  marker = null,
}: {
  data: Point[];
  suffix?: string;
  marker?: number | null;
}) {
  if (!data.length) return <Empty />;

  const top = niceMax(Math.max(...data.map((d) => d.value)));
  const w = 100;
  const step = data.length > 1 ? w / (data.length - 1) : 0;
  const y = (v: number) => H - 18 - (v / top) * (H - 30);
  const path = data.map((d, i) => `${i ? "L" : "M"}${i * step},${y(d.value)}`).join("");
  const area = `${path}L${(data.length - 1) * step},${H - 18}L0,${H - 18}Z`;

  return (
    <svg viewBox={`0 0 ${w} ${H}`} width="100%" height={H} preserveAspectRatio="none"
         role="img" aria-label={`${comma(data[data.length - 1]?.value ?? 0)}${suffix}`}>
      {[0, 0.5, 1].map((at) => (
        <line key={at} x1="0" x2={w} y1={y(top * at)} y2={y(top * at)}
              stroke={GRID} strokeWidth="1" vectorEffect="non-scaling-stroke" />
      ))}
      <path d={area} fill={LINE_WASH} />
      <path d={path} fill="none" stroke={LINE} strokeWidth="2"
            strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      {marker !== null && data[marker] && (
        <>
          <line x1={marker * step} x2={marker * step} y1="6" y2={H - 18}
                stroke={FAINT} strokeWidth="1" vectorEffect="non-scaling-stroke" />
          {/* A ring in the surface colour, so the dot reads against the line. */}
          <circle cx={marker * step} cy={y(data[marker].value)} r="3.5"
                  fill={LINE} stroke="#fff" strokeWidth="2"
                  vectorEffect="non-scaling-stroke" />
        </>
      )}
    </svg>
  );
}

// The axis labels sit outside the stretched svg, so they are never squashed.
export function Series({
  data,
  suffix = "",
}: {
  data: Point[];
  suffix?: string;
}) {
  if (!data.length) return <Empty />;
  const top = niceMax(Math.max(...data.map((d) => d.value)));
  const ticks = ticksOf(data);

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: FAINT }}>
        <span>{comma(top)}{suffix}</span>
        <span style={{ color: SOFT }}>
          {comma(data.reduce((sum, d) => sum + d.value, 0))}{suffix} total
        </span>
      </div>

      <Hover data={data} suffix={suffix} />

      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: FAINT }}>
        {ticks.map((at) => (
          <span key={at}>{clock(data[at]?.label ?? "")}</span>
        ))}
      </div>
    </div>
  );
}

function Hover({ data, suffix }: { data: Point[]; suffix: string }) {
  const [at, setAt] = useState<number | null>(null);
  const point = at === null ? null : data[at];

  return (
    <div
      style={{ position: "relative" }}
      onMouseMove={(event) => setAt(indexFrom(event, data.length))}
      onMouseLeave={() => setAt(null)}
    >
      <Spark data={data} suffix={suffix} marker={at} />
      {point && (
        <Tip at={at as number} count={data.length}>
          <strong>{comma(point.value)}{suffix}</strong>
          <br />
          {spell(point.label)}
        </Tip>
      )}
    </div>
  );
}

// Green over red, stacked: "it ran and it was fine" against "it ran and it was
// not". Two fixed hues, never assigned by size.
export function RunBars({ data }: { data: { label: string; ok: number; bad: number }[] }) {
  if (!data.length) return <Empty height={72} />;
  const top = niceMax(Math.max(...data.map((d) => d.ok + d.bad), 1));
  const ticks = ticksOf(data);
  const width = 100 / data.length;

  return (
    <RunHover data={data} top={top} width={width} ticks={ticks} />
  );
}

function RunHover({
  data, top, width, ticks,
}: {
  data: { label: string; ok: number; bad: number }[];
  top: number;
  width: number;
  ticks: number[];
}) {
  const [at, setAt] = useState<number | null>(null);
  const point = at === null ? null : data[at];

  return (
    <div
      style={{ position: "relative" }}
      onMouseMove={(event) => setAt(indexFrom(event, data.length))}
      onMouseLeave={() => setAt(null)}
    >
      {point && (
        <Tip at={at as number} count={data.length}>
          <strong>{point.ok} clean</strong>
          {point.bad > 0 && <span style={{ color: "#ff9d9f" }}> · {point.bad} failed</span>}
          <br />
          {spell(point.label)}
        </Tip>
      )}
      <svg viewBox="0 0 100 72" width="100%" height={72} preserveAspectRatio="none"
           role="img" aria-label="runs over time">
        {at !== null && (
          <rect x={at * width} y="0" width={Math.max(width, 0.5)} height="62"
                fill="rgba(28, 35, 48, 0.06)" />
        )}
        {data.map((d, i) => {
          const okH = (d.ok / top) * 60;
          const badH = (d.bad / top) * 60;
          const x = i * width;
          const w = Math.max(width * 0.7, 0.35);
          return (
            <g key={d.label}>
              {badH > 0 && <rect x={x} y={62 - badH} width={w} height={badH} fill={BAD} rx="0.3" />}
              {okH > 0 && (
                <rect x={x} y={62 - badH - okH} width={w} height={okH} fill={OK} rx="0.3" />
              )}
            </g>
          );
        })}
        <line x1="0" x2="100" y1="62" y2="62" stroke={GRID} strokeWidth="1"
              vectorEffect="non-scaling-stroke" />
      </svg>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: FAINT }}>
        {ticks.map((at) => (
          <span key={at}>{clock(data[at]?.label ?? "")}</span>
        ))}
      </div>
    </div>
  );
}

export function Rank({
  data,
  suffix = "",
}: {
  data: Point[];
  suffix?: string;
}) {
  if (!data.length) return <Empty height={60} />;
  const top = Math.max(...data.map((d) => d.value), 1);

  return (
    <div>
      {data.map((d) => (
        <div key={d.label} title={`${d.label}: ${comma(d.value)}${suffix}`}
             style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span style={{ width: "7rem", flex: "none", color: SOFT, fontSize: 11,
                         overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {d.label}
          </span>
          <span style={{ flex: 1, height: 8, background: GRID, borderRadius: 4, minWidth: 0 }}>
            <span style={{ display: "block", width: `${(d.value / top) * 100}%`, height: 8,
                           background: LINE, borderRadius: 4 }} />
          </span>
          <span style={{ width: "4.5rem", flex: "none", textAlign: "right", color: INK,
                         fontSize: 11, fontWeight: 600 }}>
            {comma(d.value)}{suffix}
          </span>
        </div>
      ))}
    </div>
  );
}

export function Metric({
  label,
  value,
  note,
  tone,
  why,
}: {
  label: string;
  value: string | number;
  note?: string;
  tone?: "good" | "bad" | "warn";
  why?: string;
}) {
  return (
    <div className={tone ? `card metric card-${tone}` : "card metric"}>
      <h3>
        {label}
        {why && <Why text={why} />}
      </h3>
      <div className="metric-value">
        {typeof value === "number" ? comma(value) : value}
      </div>
      {note && <div className="metric-note">{note}</div>}
    </div>
  );
}

// Russian, because the person reading this dashboard is the one who asked for
// the explanations — and the words being explained are English job names.
export function Why({ text }: { text: string }) {
  return (
    <button type="button" className="why" aria-label={text}>
      ?<span className="why-text">{text}</span>
    </button>
  );
}
