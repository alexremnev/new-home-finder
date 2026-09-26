import {
  alertBuckets, deliveredTo, historyOf, paymentsBy,
} from "@/lib/admin-queries";
import { at } from "@/lib/when";

import { pounds } from "@/lib/money";

import { Series } from "../../charts";
import { spanFrom } from "../../span";

// One definition per card on an account's page, so each can be refreshed on its
// own. See ../../panel.tsx.

const comma = (n: number) => n.toLocaleString("en-GB");

export async function AlertBuckets({
  userId,
  span,
}: {
  userId: number;
  span: string;
}) {
  return <Series data={await alertBuckets(userId, spanFrom(span), 30)} />;
}

export async function AccountPayments({ userId }: { userId: number }) {
  const paid = await paymentsBy(userId);
  return (
    <>
      {paid.length === 0 ? (
        <p className="hint">Never paid.</p>
      ) : (
        <div className="scroll-x">
          <table className="grid">
            <thead>
              <tr>
                <th>When</th><th>Plan</th><th className="num">Amount</th>
                <th className="num">Days</th><th>How</th><th>Reference</th>
              </tr>
            </thead>
            <tbody>
              {paid.map((one) => (
                <tr key={one.id}>
                  <td className="muted">{at(one.created_at)}</td>
                  <td>{one.plan}</td>
                  <td className="num">{pounds(one.amount_pence)}</td>
                  <td className="num muted">{one.granted_days ?? "—"}</td>
                  <td>{one.provider}</td>
                  <td className="muted wrap">{one.provider_ref ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export async function AccountSent({ userId }: { userId: number }) {
  const feed = await deliveredTo(userId);
  return (
    <>
      {feed.length === 0 ? (
        <p className="hint">Nothing yet.</p>
      ) : (
        <div className="scroll-x">
          <table className="grid">
            <thead>
              <tr>
                <th>Queued</th><th>Sent</th><th>State</th>
                <th className="num">Rent</th><th className="num">Beds</th>
                <th>Where</th><th>Listing</th>
              </tr>
            </thead>
            <tbody>
              {feed.map((one) => (
                <tr key={one.id}>
                  <td className="muted">{at(one.created_at)}</td>
                  <td className="muted">{at(one.sent_at)}</td>
                  <td>
                    <span
                      className={`badge ${
                        one.status === "sent"
                          ? "good"
                          : one.status === "failed"
                            ? "critical"
                            : "warning"
                      }`}
                    >
                      {one.status}
                      {one.error ? ` · ${one.error}` : ""}
                    </span>
                  </td>
                  <td className="num">
                    {one.price_pcm === null ? "—" : "£" + comma(one.price_pcm)}
                  </td>
                  <td className="num muted">{one.bedrooms ?? "—"}</td>
                  <td className="muted">{one.district ?? "—"}</td>
                  <td className="wrap">
                    {one.url ? (
                      <a href={one.url} target="_blank" rel="noreferrer">
                        open
                      </a>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}

export async function AccountHistory({ userId }: { userId: number }) {
  const history = await historyOf(userId);
  return (
    <>
      {history.length === 0 ? (
        <p className="hint">Nothing. Every action from this page is recorded here.</p>
      ) : (
        <div className="scroll-x">
          <table className="grid">
            <thead>
              <tr><th>When</th><th>What</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {history.map((one) => (
                <tr key={one.id}>
                  <td className="muted">{at(one.created_at)}</td>
                  <td><strong>{one.action}</strong></td>
                  <td className="wrap muted counters">{JSON.stringify(one.detail)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
