import Link from "next/link";
import {
  paymentSeries, paymentSummary, paymentsByPlan, paymentsByProvider,
  recentComps, recentPayments,
} from "@/lib/admin-queries";

import { Metric, Rank, Series, Why } from "../charts";
import { DEFAULT_SPAN, spanFrom } from "../span";

import { at } from "@/lib/when";

export const dynamic = "force-dynamic";

const pounds = (pence: number) =>
  "£" + (pence / 100).toLocaleString("en-GB", { maximumFractionDigits: 0 });

export default async function PaymentsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const params = await searchParams;
  const win = spanFrom(params.w ?? DEFAULT_SPAN);

  const [summary, series, byPlan, byProvider, recent, comps] = await Promise.all([
    paymentSummary(win),
    paymentSeries(win),
    paymentsByPlan(win),
    paymentsByProvider(win),
    recentPayments(),
    recentComps().catch(() => []),
  ]);

  const average = summary.payments > 0 ? summary.taken_pence / summary.payments : 0;

  return (
    <>
      <div className="dash-head">
        <h1>Payments</h1>
      </div>

      <div className="dash-row">
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
          why="Возвраты пишутся отрицательной суммой в ту же таблицу, поэтому в «Taken» они не попадают, а здесь видно их число."
        />
      </div>

      <div className="dash-row dash-row-wide">
        <div className="card">
          <h2>Taken per day</h2>
          <Series
            data={series.map((d) => ({ ...d, value: Math.round(d.value / 100) }))}
            suffix=" £"
          />
        </div>
        <div className="card">
          <h2>
            By plan
            <Why text="Сколько денег принёс каждый тариф за период. Пусто — значит за это время не платили." />
          </h2>
          <Rank
            data={byPlan.map((d) => ({ ...d, value: Math.round(d.value / 100) }))}
            suffix=" £"
          />
          <h2 style={{ marginTop: "1rem" }}>
            By provider
            <Why text="stripe — оплата картой; bank_transfer — выдано вручную через /grant; manual — правка из админки." />
          </h2>
          <Rank data={byProvider} />
        </div>
      </div>

      {comps.length > 0 && (
        <div className="card" style={{ marginBottom: "0.75rem" }}>
          <h2>
            Free extensions
            <Why text="Дни, выданные без оплаты из админки — аудит действий extend_plan. Это не выручка, но это доступ, поэтому отдельной таблицей." />
          </h2>
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
        </div>
      )}

      <div className="card">
        <h2>Latest</h2>
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
      </div>
    </>
  );
}
