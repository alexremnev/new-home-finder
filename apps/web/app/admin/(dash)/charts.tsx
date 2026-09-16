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

export function Spark({
  data,
  suffix = "",
}: {
  data: Point[];
  suffix?: string;
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
      <Spark data={data} suffix={suffix} />
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: FAINT }}>
        {ticks.map((at) => (
          <span key={at}>{clock(data[at]?.label ?? "")}</span>
        ))}
      </div>
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
    <div>
      <svg viewBox="0 0 100 72" width="100%" height={72} preserveAspectRatio="none"
           role="img" aria-label="runs over time">
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
        <div key={d.label} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
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
    <div className="card metric">
      <h3>
        {label}
        {why && <Why text={why} />}
      </h3>
      <div className={tone ? `metric-value ${tone}` : "metric-value"}>
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
