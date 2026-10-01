import { Pool } from "pg";

declare global {

  var __pool: Pool | undefined;
}

// Connections per instance.
//
// This was 1, and one is too few for the page that reads this. The admin
// dashboard renders every panel in its own Suspense boundary so their queries
// run at the same time — around twenty on the System tab alone — and with a
// single connection they instead queue behind each other. Each one's
// `connectionTimeoutMillis` clock starts when it asks, not when it is served,
// so the panels at the end of the queue time out while the ones at the top
// render: `Last runs` and `Run log` are the last two panels on that page, and
// they were empty for this reason rather than for want of rows.
//
// Not large, though, because this runs serverless: every concurrent instance
// holds its own pool, so the total is this number times however many instances
// are warm. Eight is enough that nothing on the busiest page waits for a
// connection, and small enough that a dozen instances do not exhaust the
// database. Through a Supabase pooler it could be higher; there is no reason
// for it to be.
const CONNECTIONS = 8;

function pool(): Pool {
  if (!global.__pool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) throw new Error("DATABASE_URL is not set");
    global.__pool = new Pool({
      connectionString,
      max: CONNECTIONS,
      idleTimeoutMillis: 10_000,
      connectionTimeoutMillis: 8_000,
    });
  }
  return global.__pool;
}

export async function query<T extends Record<string, unknown>>(
  sql: string,
  params: unknown[] = [],
): Promise<T[]> {
  try {
    const result = await pool().query(sql, params);
    return result.rows as T[];
  } catch (error) {
    // Said out loud before it is rethrown, because almost every caller on the
    // dashboard ends in `.catch(() => [])` — a panel that cannot answer is
    // better than a page that will not load. The cost of that is a failure
    // which looks exactly like an empty table, and the queue timeout above
    // hid behind it for as long as it existed. One line here is what makes
    // the two distinguishable.
    console.error("query failed", {
      sql: sql.replace(/\s+/g, " ").slice(0, 120),
      error: error instanceof Error ? error.message : String(error),
    });
    throw error;
  }
}

export async function transaction<T>(
  work: (run: (sql: string, params?: unknown[]) => Promise<Record<string, unknown>[]>) => Promise<T>,
): Promise<T> {
  const client = await pool().connect();
  try {
    await client.query("BEGIN");
    const result = await work(async (sql, params = []) => {
      const r = await client.query(sql, params);
      return r.rows as Record<string, unknown>[];
    });
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}
