import Link from "next/link";

import {
  paymentSeries, paymentSummary, paymentsByPlan, paymentsByProvider,
  recentComps, recentPayments,
} from "@/lib/admin-queries";
import { at } from "@/lib/when";

import { pounds } from "@/lib/money";

import { Metric, Rank, Series } from "../charts";
import { spanFrom } from "../span";

// One definition per card, rendered by the page inside Suspense and again by
// the refresh action for a single card. See ../panel.tsx.

export async function PaymentTiles({ span }: { span: string }) {
  const summary = await paymentSummary(spanFrom(span));
  const average = summary.payments > 0 ? summary.taken_pence / summary.payments : 0;

  return (
    <div className="tiles">
      <Metric
        label="Taken"
        value={pounds(summary.taken_pence)}
        tone={summary.taken_pence > 0 ? "good" : "warn"}
        note={`${summary.payments} payment${summary.payments === 1 ? "" : "s"}`}
        why="Сумма всех платежей за период, в фунтах. Считается по payments.amount_pence — по тому, что реально прошло, а не по цене тарифа."
      />
      <Metric
        label="Payers"
        value={summary.payers}
        // Green when somebody paid, amber when nobody did. Neutral would mean
        // "not judged", and whether anyone paid is exactly the judgement.
        tone={summary.payers > 0 ? "good" : "warn"}
        note="distinct accounts"
      />
      <Metric
        label="Average"
        value={summary.payments > 0 ? pounds(average) : "—"}
        tone={summary.payments > 0 ? "good" : "warn"}
        note="per payment"
      />
      <Metric
        label="Refunds"
        value={summary.refunds}
        tone={summary.refunds > 0 ? "bad" : "good"}
        note="negative amounts"
        why="Возвраты пишутся отрицательной суммой в ту же таблицу, поэтому в «Taken» они не попадают, а здесь видно их число. Сам возврат делается в дашборде Stripe — оттуда webhook приносит его сюда."
      />
    </div>
  );
}

export async function TakenPerDay({ span }: { span: string }) {
  const series = await paymentSeries(spanFrom(span));
  return (
    <Series
      data={series.map((d) => ({ ...d, value: Math.round(d.value / 100) }))}
      suffix=" £"
    />
  );
}

export async function ByPlan({ span }: { span: string }) {
  const rows = await paymentsByPlan(spanFrom(span));
  if (rows.length === 0) return <p className="hint">Nothing was paid in this range.</p>;
  return (
    <Rank
      data={rows.map((d) => ({ ...d, value: Math.round(d.value / 100) }))}
      suffix=" £"
    />
  );
}

export async function ByProvider({ span }: { span: string }) {
  const rows = await paymentsByProvider(spanFrom(span));
  if (rows.length === 0) return <p className="hint">Nothing was paid in this range.</p>;
  return <Rank data={rows} />;
}

export async function FreeExtensions() {
  const comps = await recentComps().catch(() => []);
  if (comps.length === 0) {
    return <p className="hint">Nothing has been granted by hand.</p>;
  }

  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Account</th>
            <th>What was granted</th>
          </tr>
        </thead>
        <tbody>
          {comps.map((row) => (
            <tr key={row.id}>
              <td className="mono">{at(row.created_at)}</td>
              <td>
                {row.user_id === null ? (
                  "erased"
                ) : (
                  <Link href={`/admin/subscribers/${row.user_id}`}>{row.user_id}</Link>
                )}
              </td>
              <td className="mono">
                {Object.entries(row.detail ?? {})
                  .map(([name, value]) => `${name} ${value}`)
                  .join(" · ") || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export async function LatestPayments() {
  const recent = await recentPayments();
  if (recent.length === 0) return <p className="hint">No payment has been taken yet.</p>;

  return (
    <div className="scroll-x">
      <table>
        <thead>
          <tr>
            <th>When</th>
            <th>Account</th>
            <th>Plan</th>
            <th className="num">Amount</th>
            <th>How</th>
            <th className="num">Days</th>
          </tr>
        </thead>
        <tbody>
          {recent.map((row) => (
            <tr key={`${row.created_at}-${row.user_id}`}>
              <td className="mono">{at(row.created_at)}</td>
              <td>
                <Link href={`/admin/subscribers/${row.user_id}`}>{row.user_id}</Link>
              </td>
              <td>{row.plan}</td>
              <td className="num">{pounds(row.amount_pence)}</td>
              <td>{row.provider}</td>
              <td className="num">{row.granted_days ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
