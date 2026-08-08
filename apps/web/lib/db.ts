// Postgres access.
//
// One pool per process, kept on `globalThis` because a serverless function is
// re-entered on a warm invocation and a fresh pool per request exhausts the
// server's connection slots within minutes. `max: 1` for the same reason: many
// short-lived instances each holding one connection is what the pooled port
// (6543) is designed for.

import { Pool } from "pg";

declare global {
  // eslint-disable-next-line no-var
  var __pool: Pool | undefined;
}

function pool(): Pool {
  if (!global.__pool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) throw new Error("DATABASE_URL is not set");
    global.__pool = new Pool({
      connectionString,
      max: 1,
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
  const result = await pool().query(sql, params);
  return result.rows as T[];
}

/**
 * Run several statements as one unit.
 *
 * `/stop` needs this and not for tidiness: deleting the filter while leaving the
 * queue behind would send messages to someone who has just unsubscribed, which
 * is a PECR breach rather than a cosmetic bug.
 */
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
