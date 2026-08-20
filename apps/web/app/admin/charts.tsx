// The charts, as inline SVG.
//
// ── why hand-rolled and not a library ────────────────────────────────────────
//
// A charting library is 40–150kB of JavaScript shipped to a browser so that four
// shapes can be drawn from numbers the server already has. These are server
// components: the SVG arrives as markup, there is no hydration, and the console
// works with scripting off. The cost is that every mark below is written out — which
// is a one-off, and it is why the mark specs are stated as constants rather than
// left to each caller's taste.
//
// ── the colour rules these follow ────────────────────────────────────────────
//
// Colour has one job per chart and the job decides the palette:
//
//   * magnitude of one measure (per day, per district, per price band) → ONE hue.
//     A rainbow of districts would imply the colours mean something; they would
//     only mean "next in the list".
//   * identity (which plan) → the categorical slots, in fixed order, never cycled.
//
// The palette is the validated default instance, checked with the skill's validator
// against this console's own surface (#ffffff) rather than the validator's default:
// all four gates pass on adjacent pairs; contrast on aqua and yellow lands below 3:1,
// which obliges visible direct labels — so the stacked bar labels every segment and
// the table below it repeats every number.
//
// ── marks ────────────────────────────────────────────────────────────────────
//
// Bars ≤24px with a 4px rounded data-end and a square foot at the baseline; lines
// 2px with round joins; markers r≥4 with a 2px surface ring; area fills at ~10%;
// gridlines hairline, solid, recessive. Values are labelled selectively — the end of
// a line, the tip of a bar — never on every point.

const SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"] as const;
const MAGNITUDE = SERIES[0];
const SURFACE = "#ffffff";
const GRID = "#e1e0d9";
const AXIS = "#c3c2b7";
const MUTED = "#898781";
const INK = "#262637";

const BAR_MAX = 24;   // never fill the slot; the leftover is air
const END_R = 4;      // rounded data-end
const RING = 2;       // surface ring on markers, surface gap between marks

export type Point = { label: string; value: number };

const comma = (n: number) => n.toLocaleString("en-GB");

/**
 * A round number at or above the data's peak.
 *
 * Axis ticks have to be numbers a person would say out loud. 1, 2 or 5 times a
 * power of ten covers that for every scale this console sees.
 */
function niceMax(peak: number): number {
  if (peak <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(peak));
  for (const step of [1, 2, 2.5, 5, 10]) {
    if (power * step >= peak) return power * step;
  }
  return power * 10;
}

function Empty({ label }: { label: string }) {
  return <p className="chart-empty">{label}</p>;
}

// ── time series ────────────────────────────────────────────────────────────

/**
 * One measure over days: area wash, 2px line, a dot per day, the last value
 * labelled.
 *
 * A single series, so there is no legend — the title names it. Zero days are drawn
 * as zero rather than skipped: a line that closes its gaps invents a shape.
 */
export function TimeSeries({
  data, title, unit = "",
}: {
  data: Point[];
  title: string;
  unit?: string;
}) {
  if (data.length === 0) return <Empty label="Nothing yet." />;

  const W = 720;
  const H = 190;
  const PAD = { top: 16, right: 46, bottom: 26, left: 44 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;

  const top = niceMax(Math.max(...data.map((d) => d.value)));
  const x = (i: number) =>
    PAD.left + (data.length === 1 ? plotW / 2 : (i / (data.length - 1)) * plotW);
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH;

  const line = data.map((d, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(d.value)}`).join(" ");
  const area = `${line} L${x(data.length - 1)},${PAD.top + plotH} L${x(0)},${PAD.top + plotH} Z`;
  const last = data[data.length - 1];
  // Every day gets a dot only while they can be told apart; past that the dots
  // become a texture and the line reads better alone.
  const showDots = data.length <= 31;

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} className="chart-svg">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line
              x1={PAD.left} x2={W - PAD.right}
              y1={PAD.top + plotH * (1 - f)} y2={PAD.top + plotH * (1 - f)}
              stroke={f === 0 ? AXIS : GRID} strokeWidth="1"
            />
            <text x={PAD.left - 8} y={PAD.top + plotH * (1 - f) + 4}
                  textAnchor="end" className="tick">
              {comma(Math.round(top * f))}
            </text>
          </g>
        ))}

        <path d={area} fill={MAGNITUDE} fillOpacity="0.1" />
        <path d={line} fill="none" stroke={MAGNITUDE} strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round" />

        {showDots &&
          data.map((d, i) => (
            <g key={d.label}>
              <circle cx={x(i)} cy={y(d.value)} r="4"
                      fill={MAGNITUDE} stroke={SURFACE} strokeWidth={RING} />
              {/* An invisible target wider than the mark, over it. The hover layer
                  is the browser's own tooltip — no script, and it survives a page
                  served with JavaScript off — but an 8px dot you have to land on
                  dead centre is a tooltip nobody reaches. */}
              <circle cx={x(i)} cy={y(d.value)} r="11" fill="transparent">
                <title>{`${d.label}: ${comma(d.value)}${unit}`}</title>
              </circle>
            </g>
          ))}

        {last && (
          <text x={x(data.length - 1) + 10} y={y(last.value) + 4} className="end-label">
            {comma(last.value)}
          </text>
        )}

        {[0, Math.floor((data.length - 1) / 2), data.length - 1]
          .filter((i, at, all) => all.indexOf(i) === at && data[i])
          .map((i) => (
            <text key={i} x={x(i)} y={H - 8}
                  textAnchor={i === 0 ? "start" : i === data.length - 1 ? "end" : "middle"}
                  className="tick">
              {data[i]!.label.slice(5)}
            </text>
          ))}
      </svg>
    </figure>
  );
}

// ── horizontal bars ───────────────────────────────────────────────────────

/**
 * One measure across named categories, biggest first.
 *
 * Horizontal because the categories are words: a district code under a vertical
 * column has to be rotated to fit, and rotated text is text nobody reads.
 */
export function Bars({ data, title }: { data: Point[]; title: string }) {
  if (data.length === 0) return <Empty label="Nothing in this period." />;

  const rowH = 30;
  const W = 720;
  const labelW = 84;
  const valueW = 56;
  const trackW = W - labelW - valueW;
  const top = Math.max(...data.map((d) => d.value));

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${data.length * rowH + 8}`} role="img" aria-label={title}
           className="chart-svg">
        {data.map((d, i) => {
          const w = Math.max(3, (d.value / top) * trackW);
          const barH = Math.min(BAR_MAX, rowH - 10);
          const yTop = i * rowH + (rowH - barH) / 2;
          return (
            <g key={d.label}>
              <text x={labelW - 10} y={i * rowH + rowH / 2 + 4} textAnchor="end"
                    className="cat-label">
                {d.label}
              </text>
              {/* Square at the baseline, rounded at the data end — so the bar reads
                  as growing from the axis rather than floating. */}
              <path
                d={`M${labelW},${yTop} H${labelW + w - END_R}
                    a${END_R},${END_R} 0 0 1 ${END_R},${END_R}
                    V${yTop + barH - END_R}
                    a${END_R},${END_R} 0 0 1 -${END_R},${END_R}
                    H${labelW} Z`}
                fill={MAGNITUDE}
              >
                <title>{`${d.label}: ${comma(d.value)}`}</title>
              </path>
              <text x={labelW + w + 10} y={i * rowH + rowH / 2 + 4} className="value-label">
                {comma(d.value)}
              </text>
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

// ── columns ───────────────────────────────────────────────────────────────

/**
 * A distribution across ordered bands.
 *
 * Columns, not bars: the bands have an order and a left-to-right axis is how an
 * order reads. Only the tallest is labelled — a number on every column is a table
 * pretending to be a chart.
 */
export function Columns({
  data, title, format = comma,
}: {
  data: Point[];
  title: string;
  format?: (n: number) => string;
}) {
  if (data.length === 0) return <Empty label="Nothing in this period." />;

  const W = 720;
  const H = 190;
  const PAD = { top: 20, right: 8, bottom: 30, left: 44 };
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const top = niceMax(Math.max(...data.map((d) => d.value)));
  const slot = plotW / data.length;
  const barW = Math.min(BAR_MAX, slot - RING * 2);
  const peak = data.reduce((best, d) => (d.value > best.value ? d : best), data[0]!);

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} className="chart-svg">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={PAD.left} x2={W - PAD.right}
                  y1={PAD.top + plotH * (1 - f)} y2={PAD.top + plotH * (1 - f)}
                  stroke={f === 0 ? AXIS : GRID} strokeWidth="1" />
            <text x={PAD.left - 8} y={PAD.top + plotH * (1 - f) + 4} textAnchor="end"
                  className="tick">
              {comma(Math.round(top * f))}
            </text>
          </g>
        ))}

        {data.map((d, i) => {
          const h = Math.max(2, (d.value / top) * plotH);
          const x = PAD.left + slot * i + (slot - barW) / 2;
          const yTop = PAD.top + plotH - h;
          const r = Math.min(END_R, h / 2, barW / 2);
          return (
            <g key={d.label}>
              <path
                d={`M${x},${PAD.top + plotH} V${yTop + r}
                    a${r},${r} 0 0 1 ${r},-${r} H${x + barW - r}
                    a${r},${r} 0 0 1 ${r},${r} V${PAD.top + plotH} Z`}
                fill={MAGNITUDE}
              >
                <title>{`${format(Number(d.label))}: ${comma(d.value)}`}</title>
              </path>
              {/* Every other label when the bands are dense, so they never collide. */}
              {(data.length <= 10 || i % 2 === 0) && (
                <text x={x + barW / 2} y={H - 10} textAnchor="middle" className="tick">
                  {format(Number(d.label))}
                </text>
              )}
            </g>
          );
        })}

        {peak.value > 0 && (
          <text
            x={PAD.left + slot * data.indexOf(peak) + slot / 2}
            y={PAD.top + plotH - (peak.value / top) * plotH - 8}
            textAnchor="middle" className="end-label"
          >
            {comma(peak.value)}
          </text>
        )}
      </svg>
    </figure>
  );
}

// ── plan mix ──────────────────────────────────────────────────────────────

/**
 * Which plans people are on, as one bar of the whole.
 *
 * A stacked bar rather than a pie: comparing lengths along one edge is something
 * people do accurately, and comparing angles is not. One bar because the parts are
 * of one whole and the question is the split, not the total.
 *
 * Every segment is labelled and the legend is always present — two segments already
 * means colour alone would be carrying identity, and two of these hues sit below
 * 3:1 on white, which obliges visible labels rather than allowing them.
 */
export function PlanMix({ data, title }: { data: Point[]; title: string }) {
  const total = data.reduce((sum, d) => sum + d.value, 0);
  if (total === 0) return <Empty label="No subscribers yet." />;

  // One plan is not a split, and a bar with one segment full width says only
  // "100%". The honest form for one number is the number.
  if (data.length === 1) {
    return <Stat label={title} value={comma(data[0]!.value)} note={`all on ${data[0]!.label}`} />;
  }

  // Past the fourth slot the palette would have to cycle, and a repeated hue means
  // two different plans wear the same colour. Folding the tail into one labelled
  // "Other" keeps every colour unambiguous, and the table below still names them.
  const shown = data.length <= SERIES.length
    ? data
    : [
        ...data.slice(0, SERIES.length - 1),
        {
          label: `Other (${data.length - SERIES.length + 1} plans)`,
          value: data.slice(SERIES.length - 1).reduce((sum, d) => sum + d.value, 0),
        },
      ];

  const W = 720;
  const barH = 34;
  let x = 0;

  return (
    <figure className="chart">
      <figcaption>{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${barH + 6}`} role="img" aria-label={title} className="chart-svg">
        {shown.map((d, i) => {
          const raw = (d.value / total) * W;
          // The 2px surface gap between touching segments, taken off every
          // segment but the last so the bar still ends flush.
          const w = Math.max(2, raw - (i < shown.length - 1 ? RING : 0));
          const at = x;
          x += raw;
          const share = Math.round((d.value / total) * 100);
          return (
            <g key={d.label}>
              <rect x={at} y={0} width={w} height={barH} rx="3"
                    fill={SERIES[i % SERIES.length]}>
                <title>{`${d.label}: ${comma(d.value)} (${share}%)`}</title>
              </rect>
              {/* Inside when it fits, and only then. A label that would be clipped
                  is not placed at all — the legend and the table carry it. */}
              {w > 56 && (
                <text x={at + w / 2} y={barH / 2 + 5} textAnchor="middle"
                      className="on-mark">
                  {share}%
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <ul className="legend">
        {shown.map((d, i) => (
          <li key={d.label}>
            <span className="swatch" style={{ background: SERIES[i % SERIES.length] }} />
            {d.label} — {comma(d.value)}
          </li>
        ))}
      </ul>
    </figure>
  );
}

// ── stat tile ─────────────────────────────────────────────────────────────

/** Label in sentence case, one value, one optional note. No sparkline, no arrow. */
export function Stat({
  label, value, note,
}: {
  label: string;
  value: string;
  note?: string;
}) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {note && <span className="stat-note">{note}</span>}
    </div>
  );
}

export const INK_COLOUR = INK;
export const MUTED_COLOUR = MUTED;
