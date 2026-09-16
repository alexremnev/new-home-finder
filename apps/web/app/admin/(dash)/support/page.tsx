import Link from "next/link";

import { ticketCounts, tickets } from "@/lib/admin-queries";

export const dynamic = "force-dynamic";

function ago(stamp: string | null): string {
  if (!stamp) return "—";
  const then = new Date(stamp.replace(" ", "T"));
  if (Number.isNaN(then.getTime())) return stamp;
  const hours = (Date.now() - then.getTime()) / 3_600_000;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min ago`;
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

// The bot promises a reply within 24 hours. Anything older than that is not
// merely waiting, it is late, and the list says so rather than leaving the
// arithmetic to whoever is reading.
function late(stamp: string | null): boolean {
  if (!stamp) return false;
  const then = new Date(stamp.replace(" ", "T"));
  return !Number.isNaN(then.getTime()) && Date.now() - then.getTime() > 86_400_000;
}

export default async function SupportPage() {
  const [rows, counts] = await Promise.all([tickets(), ticketCounts()]);
  const open = rows.filter((row) => row.status === "open");
  const handled = rows.filter((row) => row.status === "handled");

  return (
    <>
      <h1>Support</h1>

      {open.length === 0 ? (
        <p className="lede">Nothing waiting. {counts.handled} handled so far.</p>
      ) : (
        <p className="lede">
          {open.length} waiting, {open.filter((row) => late(row.submitted_at)).length}{" "}
          past the 24 hours the bot promised.
        </p>
      )}

      {open.map((row) => (
        <article
          key={row.id}
          className={late(row.submitted_at) ? "ticket ticket-late" : "ticket"}
        >
          <header>
            <span className="ticket-id">#{row.id}</span>
            <span className={late(row.submitted_at) ? "critical" : "good"}>
              {ago(row.submitted_at)}
            </span>
            <span className="ticket-who">
              {row.user_id === null ? (
                <span title={`${row.channel} ${row.address}`}>no account</span>
              ) : (
                <Link href={`/admin/subscribers/${row.user_id}`}>
                  user {row.user_id}
                  {row.plan ? ` · ${row.plan}` : ""}
                </Link>
              )}
            </span>
          </header>

          <p className="ticket-body">{row.body}</p>

          <footer>
            <span className="ticket-email">
              {row.email ? (
                <a href={`mailto:${row.email}`}>{row.email}</a>
              ) : (
                `answer in ${row.channel}`
              )}
            </span>

            <form method="post" action="/api/admin/support">
              <input type="hidden" name="ticket_id" value={row.id} />
              <input type="hidden" name="action" value="handle" />
              <input
                type="text"
                name="note"
                placeholder="what was done (optional)"
                maxLength={200}
              />
              <button type="submit">Mark handled</button>
            </form>
          </footer>
        </article>
      ))}

      {handled.length > 0 && (
        <>
          <h2>Handled</h2>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Reported</th>
                <th>Complaint</th>
                <th>What was done</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {handled.map((row) => (
                <tr key={row.id}>
                  <td>{row.id}</td>
                  <td>{ago(row.submitted_at)}</td>
                  <td className="ticket-short">{row.body}</td>
                  <td>{row.handled_note || "—"}</td>
                  <td>
                    <form method="post" action="/api/admin/support">
                      <input type="hidden" name="ticket_id" value={row.id} />
                      <input type="hidden" name="action" value="reopen" />
                      <button type="submit" className="ghost">
                        Reopen
                      </button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </>
  );
}
