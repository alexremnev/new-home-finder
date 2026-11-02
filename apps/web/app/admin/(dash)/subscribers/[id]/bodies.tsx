import {
  alertBuckets, deliveredTo, historyOf, paymentsBy,
} from "@/lib/admin-queries";
import { at } from "@/lib/when";

import { pounds } from "@/lib/money";

import { AsyncForm } from "../../async-form";
import { windowFor } from "../../cached";
import { Series } from "../../charts";
import { Paged, type MorePage } from "../../paged";
import { moreHistory, moreSent } from "./more";

// One definition per card on an account's page, each fetching inside its own
// Suspense boundary so the four queries run in parallel. The two journals page
// on through a server action; see ../../paged.tsx.

const comma = (n: number) => n.toLocaleString("en-GB");

export async function AlertBuckets({
  userId,
  span,
}: {
  userId: number;
  span: string;
}) {
  return <Series data={await alertBuckets(userId, windowFor(span), 30)} />;
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

const SENT_PER_PAGE = 25;

export async function sentPage(
  userId: number,
  page: number,
): Promise<MorePage & { count: number }> {
  const feed = await deliveredTo(
    userId,
    SENT_PER_PAGE + 1,
    (page - 1) * SENT_PER_PAGE,
  ).catch(() => []);
  const shown = feed.slice(0, SENT_PER_PAGE);

  return {
    count: shown.length,
    more: feed.length > SENT_PER_PAGE,
    rows: shown.map((one) => (
      <tr key={one.id}>
        <td className="muted">{at(one.created_at)}</td>
        <td className="muted">{at(one.sent_at)}</td>
        <td className="state-cell">
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
          {/* Beside the badge rather than in a column of its own: most rows
              have no button, and an empty column on every one of them to
              serve the few that do is a column the eye has to skip. The
              badge is the thing being answered — it says this listing was
              never delivered — so the answer belongs against it.

              Only where it would work. A disabled button on a sent row, or
              on WhatsApp where 0074 says the press is a charge that the next
              drain undoes, is a thing to hover and find out about; no button
              is the same answer, given quietly. */}
          {one.status === "skipped" && one.channel === "telegram" && (
            <AsyncForm action="/api/admin/user" className="inline-form">
              <input type="hidden" name="action" value="send_listing" />
              <input type="hidden" name="user_id" value={userId} />
              <input type="hidden" name="notification_id" value={one.id} />
              <button type="submit" className="ghost" title="Back into the queue; it goes out on the next drain">
                Send it anyway
              </button>
            </AsyncForm>
          )}
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
    )),
  };
}

export async function AccountSent({ userId }: { userId: number }) {
  const first = await sentPage(userId, 1);
  if (first.count === 0) return <p className="hint">Nothing yet.</p>;

  return (
    <Paged
      load={moreSent.bind(null, userId)}
      more={first.more}
      per={SENT_PER_PAGE}
      unit="alerts"
      grid
      head={
        <tr>
          <th>Queued</th><th>Sent</th><th>State</th>
          <th className="num">Rent</th><th className="num">Beds</th>
          <th>Where</th><th>Listing</th>
        </tr>
      }
    >
      {first.rows}
    </Paged>
  );
}

const HISTORY_PER_PAGE = 15;

export async function historyPage(
  userId: number,
  page: number,
): Promise<MorePage & { count: number }> {
  const history = await historyOf(
    userId,
    HISTORY_PER_PAGE + 1,
    (page - 1) * HISTORY_PER_PAGE,
  ).catch(() => []);
  const shown = history.slice(0, HISTORY_PER_PAGE);

  return {
    count: shown.length,
    more: history.length > HISTORY_PER_PAGE,
    rows: shown.map((one) => (
      <tr key={one.id}>
        <td className="muted">{at(one.created_at)}</td>
        <td><strong>{one.action}</strong></td>
        <td className="wrap muted counters">{JSON.stringify(one.detail)}</td>
      </tr>
    )),
  };
}

export async function AccountHistory({ userId }: { userId: number }) {
  const first = await historyPage(userId, 1);
  if (first.count === 0) {
    return (
      <p className="hint">Nothing. Every action from this page is recorded here.</p>
    );
  }

  return (
    <Paged
      load={moreHistory.bind(null, userId)}
      more={first.more}
      per={HISTORY_PER_PAGE}
      unit="actions"
      grid
      head={<tr><th>When</th><th>What</th><th>Detail</th></tr>}
    >
      {first.rows}
    </Paged>
  );
}
