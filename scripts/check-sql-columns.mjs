// Does the SQL in the app refer to columns that exist?
//
//   node scripts/check-sql-columns.mjs
//
// ── why this exists ──────────────────────────────────────────────────────────
//
// Every query in this repository is a string. TypeScript checks that it is a
// string; Postgres checks that it is correct, and it does so at the moment a real
// person loads a page. Between those two is the whole class of mistake this catches:
// a column that was remembered rather than read.
//
// It found `source_key` on `job_runs` (it is on `job_stages` and `job_events`, not
// on runs) and `created_at` on `job_events` (that table calls it `ts`). Both were
// written from memory of a schema I had read a hundred lines earlier.
//
// ── what it does and does not know ───────────────────────────────────────────
//
// It reads the migrations for the real column names, finds the tables each query
// touches, and checks every identifier that appears where a column would. It is a
// text tool, not a SQL parser, so it errs toward reporting: an unknown word is
// listed rather than assumed fine. A false positive is a line to read; a false
// negative is a page that breaks in production.

import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const MIGRATIONS = "db/migrations";
const SOURCES = ["apps/web/lib", "apps/web/app"];

// ── the schema, from the migrations ────────────────────────────────────────

const schema = new Map(); // table -> Set(columns)
// Column names whose declared type is a date or a timestamp, anywhere in the
// schema. Kept flat rather than per table: the check below only needs to know that
// a word names a time, not which table it came from.
const TEMPORAL = new Set();
const TEMPORAL_TYPE = /\b(TIMESTAMPTZ|TIMESTAMP|DATE)\b/i;

function addColumn(table, column, type = "") {
  if (!schema.has(table)) schema.set(table, new Set());
  schema.get(table).add(column.toLowerCase());
  if (TEMPORAL_TYPE.test(type)) TEMPORAL.add(column.toLowerCase());
}

/**
 * A column that has been taken away.
 *
 * Without this the schema map is a record of everything that has ever existed, which
 * is the one shape that makes this tool worse than nothing: it stayed silent about
 * `plans.max_alerts_per_day` — dropped in 0007, referenced again in 0020 — because as
 * far as it knew the column was still there.
 *
 * Not removed from TEMPORAL: the set is only used to ask "is this word the name of a
 * time", and a name that used to be one still is.
 */
function dropColumn(table, column) {
  schema.get(table)?.delete(column.toLowerCase());
}

// Every migration's own DML, checked against the schema as it stands when that
// migration runs. Collected here and reported after the whole schema is built, so a
// single pass gives both the final shape and each step's mistakes.
const migrationProblems = [];

for (const file of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
  const sql = readFileSync(join(MIGRATIONS, file), "utf8");

  // CREATE TABLE [IF NOT EXISTS] name ( ... );
  for (const match of sql.matchAll(
    /CREATE TABLE (?:IF NOT EXISTS )?(\w+)\s*\(([\s\S]*?)\n\);/gi,
  )) {
    const [, table, body] = match;
    for (const line of body.split("\n")) {
      const trimmed = line.trim();
      // A column definition starts with a bare name; a constraint starts with a
      // keyword. Comments and closing parens are neither.
      const column = /^(\w+)\s+(?!KEY\b)[A-Za-z]/.exec(trimmed);
      if (!column) continue;
      const first = column[1].toUpperCase();
      if (["CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "EXCLUDE"].includes(first)) {
        continue;
      }
      addColumn(table, column[1], trimmed);
    }
  }

  // One ALTER TABLE commonly adds several columns, separated by commas, and taking
  // only the first is how `plan_until` came to look like it did not exist. So the
  // statement is taken whole and every ADD COLUMN inside it is read.
  for (const statement of sql.matchAll(/ALTER TABLE (?:ONLY )?(\w+)([\s\S]*?);/gi)) {
    const [, table, body] = statement;
    for (const added of body.matchAll(/ADD COLUMN (?:IF NOT EXISTS )?(\w+)/gi)) {
      // The type follows the name on the same ADD COLUMN clause.
      addColumn(table, added[1], added[0] + body.slice(added.index + added[0].length, added.index + added[0].length + 40));
    }
  }
  for (const statement of sql.matchAll(/ALTER TABLE (?:ONLY )?(\w+)([\s\S]*?);/gi)) {
    const [, table, body] = statement;
    for (const gone of body.matchAll(/DROP COLUMN (?:IF EXISTS )?(\w+)/gi)) {
      dropColumn(table, gone[1]);
    }
  }

  for (const gone of sql.matchAll(/DROP TABLE (?:IF EXISTS )?(\w+)/gi)) {
    schema.delete(gone[1]);
  }

  // ── the migration's own writes ────────────────────────────────────────────
  //
  // Checked against the schema at THIS migration's point in the sequence, which is
  // the whole reason this lives inside the loop. `plans.max_alerts_per_day` existed
  // in 0006 and was gone by 0007; 0020 referenced it again and this tool said
  // nothing, because it had no notion of a column being taken away and no notion of
  // reading the migrations at all.
  //
  // Only the two forms that name columns outright — an INSERT's column list and an
  // UPDATE's assignments — because those are the two that can be verified without
  // parsing SQL. A bare `INSERT … VALUES` with no column list is not checked; it is
  // also not written anywhere here.
  const bare = sql.replace(/--[^\n]*/g, " ").replace(/'[^']*'/g, "''");

  for (const insert of bare.matchAll(/INSERT\s+INTO\s+(\w+)\s*\(([^)]*)\)/gi)) {
    const [, table, list] = insert;
    const known = schema.get(table);
    if (!known) continue;
    for (const column of list.split(",").map((c) => c.trim().toLowerCase()).filter(Boolean)) {
      if (!/^\w+$/.test(column) || known.has(column)) continue;
      migrationProblems.push(`  \u2717 ${MIGRATIONS}/${file} — ${table} has no column "${column}"`);
    }
  }

  for (const update of bare.matchAll(/UPDATE\s+(\w+)\s+SET\s+([\s\S]*?)(?:\bWHERE\b|;)/gi)) {
    const [, table, sets] = update;
    const known = schema.get(table);
    if (!known) continue;
    for (const assign of sets.split(",")) {
      const name = /^\s*(\w+)\s*=/.exec(assign)?.[1]?.toLowerCase();
      if (!name || known.has(name)) continue;
      migrationProblems.push(`  \u2717 ${MIGRATIONS}/${file} — ${table} has no column "${name}"`);
    }
  }

}

// Columns Postgres provides that no migration declares.
const IMPLICIT = new Set(["ctid", "xmin", "tableoid", "oid"]);

// `excluded` is the row Postgres offers inside ON CONFLICT DO UPDATE. It is not a
// table in the schema and its columns are the target table's, so it is resolved as
// an alias of whatever is being inserted into.
const PSEUDO_TABLES = new Set(["excluded"]);

// ── the queries ────────────────────────────────────────────────────────────

/** Every template literal that looks like SQL, with the file and line it is on. */
function queriesIn(path) {
  const text = readFileSync(path, "utf8");
  const found = [];
  for (const match of text.matchAll(/`([^`]*?(?:SELECT|INSERT|UPDATE|DELETE)[^`]*?)`/gi)) {
    // The row type the caller declared for this query, if it named one. It is what
    // makes the timestamp check precise rather than noisy: a temporal column with no
    // cast is only wrong when the caller has promised itself a string.
    const preceding = text.slice(Math.max(0, match.index - 400), match.index);
    const rowType = [...preceding.matchAll(/query<\s*\{?\s*([A-Za-z][\w]*)/g)].pop()?.[1];
    found.push({
      sql: match[1],
      line: text.slice(0, match.index).split("\n").length,
      rowType,
      text,
    });
  }
  return found;
}

function filesUnder(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...filesUnder(path));
    else if (/\.tsx?$/.test(entry.name)) out.push(path);
  }
  return out;
}

// Words that appear where a column would but are not one.
const NOT_COLUMNS = new Set(
  `select from where group by order having limit offset union all as on and or not in is
   null true false case when then else end insert into values conflict do update set
   returning delete with left right inner outer join full cross using distinct count sum
   min max avg coalesce greatest least now current_date current_timestamp interval
   make_interval date text int integer bigint smallint boolean jsonb json numeric real
   timestamptz timestamp asc desc nulls first last exists any array unnest generate_series
   to_char left right lower upper trim length floor ceil round abs cast between like ilike
   for share nowait skip locked days mins secs hours months years mo yy dd row rows only
   over partition filter lateral natural concat position substring extract epoch
   float8 float4 int2 int4 int8 bool bytea uuid inet interval money
   nullif greatest least mod div sign power sqrt trunc ceiling
   percentile_disc mode rank dense_rank row_number lag lead
   nothing constraint default add column alter table create index primary key
   foreign references cascade restrict check unique if not to_jsonb jsonb_build_object
   string_agg array_agg date_trunc age justify_hours width_bucket percentile_cont
   within ordinality current_setting format regexp_replace split_part
   excluded`
    .split(/\s+/)
    .filter(Boolean),
);

let problems = migrationProblems.length;
for (const line of migrationProblems) console.log(line);

for (const dir of SOURCES) {
  for (const path of filesUnder(dir)) {
    for (const { sql, line, rowType, text } of queriesIn(path)) {
      // Strip comments and string literals: a word inside 'quotes' is a value.
      const whole = sql
        .replace(/--[^\n]*/g, " ")
        .replace(/'[^']*'/g, " ' ' ")
        .replace(/\$\d+/g, " ");

      // Each arm of a UNION is its own scope, and checking them together is how a
      // missing column hides: with five arms in one query, a column belonging to the
      // fourth arm's table looks like it justifies the first arm's reference to it.
      // Postgres resolves each arm against its own FROM, and so does this.
      //
      // A WITH clause is shared, so its names are gathered from the whole query and
      // handed to every arm.
      const shared = new Set(
        [...whole.matchAll(/(?:WITH|,)\s+(\w+)\s+AS\s*\(/gi)].map((m) => m[1].toLowerCase()),
      );

      const arms = whole.split(/\bUNION\s+ALL\b|\bUNION\b|\bINTERSECT\b|\bEXCEPT\b/gi);
      for (const [armIndex, bare] of arms.entries()) {

      // Tables in play, with their aliases.
      const tables = new Map(); // alias-or-name -> table
      for (const match of bare.matchAll(/\b(?:FROM|JOIN|INTO|UPDATE)\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?/gi)) {
        const [, table, alias] = match;
        if (!schema.has(table)) continue;
        tables.set(table, table);
        if (alias && !NOT_COLUMNS.has(alias.toLowerCase())) tables.set(alias, table);
      }
      // The insert target, so `excluded.x` resolves to that table's columns.
      const target = /\bINSERT\s+INTO\s+(\w+)/i.exec(bare)?.[1];
      if (target && schema.has(target)) {
        for (const pseudo of PSEUDO_TABLES) tables.set(pseudo, target);
      }
      // Names a WITH clause invents. They are tables for the rest of the query but
      // no migration declares them, so they are noted and then not reported.
      const virtual = shared;
      if (tables.size === 0) continue;

      const allowed = new Set(IMPLICIT);
      for (const table of new Set(tables.values())) {
        for (const column of schema.get(table) ?? []) allowed.add(column);
      }

      // Qualified references: the alias names the table, so this is exact.
      for (const match of bare.matchAll(/\b(\w+)\.(\w+)\b/g)) {
        const [, prefix, column] = match;
        const table = tables.get(prefix);
        if (!table) continue;
        if (!schema.get(table)?.has(column.toLowerCase()) && !IMPLICIT.has(column.toLowerCase())) {
          console.log(`  ✗ ${path}:${line} — ${table} has no column "${column}"`);
          problems += 1;
        }
      }

      // ── a timestamp returned as a Date ────────────────────────────────────
      //
      // The driver hands a TIMESTAMPTZ to JavaScript as a Date, so a field the
      // caller has typed `string` is a Date at runtime and the first `.slice` on it
      // throws. TypeScript cannot see it: the row type is an assertion about a value
      // it never inspects.
      //
      // Driven from the declared TYPE rather than from the words in the SQL, and that
      // direction is the whole reliability of this check. Scanning the SQL for
      // temporal column names finds every mention of one — a sort key inside
      // `array_agg(… ORDER BY ts)`, a predicate inside `count(*) FILTER (WHERE ts >
      // …)` — none of which is returned to anybody. Those false positives are how a
      // check becomes noise and then becomes ignored.
      //
      // Starting from the type asks the only question that matters: this caller says
      // it will get a string called `plan_until`; does the query cast it?
      // What the query returns: everything between SELECT and its FROM.
      const selectList = /\bSELECT\b([\s\S]*?)\bFROM\b/i.exec(bare)?.[1];

      // Only the first arm. In a UNION, Postgres takes the output column names from
      // the first SELECT and ignores the rest — so the later arms select the same
      // values positionally and never mention the field names at all. Checking them
      // asks whether a name that cannot appear appears.
      if (rowType && selectList && armIndex === 0) {
        const body = new RegExp(`type\\s+${rowType}\\s*=\\s*\\{([\\s\\S]*?)\\n\\}`).exec(text)?.[1];
        for (const field of body?.matchAll(/^\s*(\w+)\??\s*:\s*([^;\n]+)/gm) ?? []) {
          const [, name, declared] = field;
          const word = name.toLowerCase();
          if (!TEMPORAL.has(word) || !/\bstring\b/.test(declared)) continue;
          // At least one occurrence carrying a cast. A column can appear twice — once
          // cast in the select list, once bare in a predicate — and the cast one is
          // the one that comes back.
          const cast = new RegExp(`\\b${name}\\b[^,]{0,24}::\\s*(text|date|varchar)`, "i");
          const aliased = new RegExp(`\\bAS\\s+${name}\\b`, "i");
          if (cast.test(selectList) || aliased.test(selectList)) continue;
          console.log(
            `  ! ${path}:${line} — ${rowType}.${name} is declared "${declared.trim()}" ` +
              `but the query returns a timestamp; it arrives as a Date. Add ::text`,
          );
          problems += 1;
        }
      }

      // Bare references, checked against the union of every table in the query.
      // Cannot say which table it was meant for, and does not need to: if no table
      // in the query has it, it is wrong wherever it was aimed.
      const qualified = new Set([...bare.matchAll(/\b\w+\.(\w+)\b/g)].map((m) => m[1].toLowerCase()));
      // An output alias is a name this query invents. It is not a column and the
      // caller reads it back by that name, so it belongs in the allowed set.
      for (const alias of bare.matchAll(/\bAS\s+(\w+)/gi)) allowed.add(alias[1].toLowerCase());
      for (const match of bare.matchAll(/\b([a-z][a-z0-9_]{2,})\b/gi)) {
        const word = match[1].toLowerCase();
        if (NOT_COLUMNS.has(word) || allowed.has(word) || qualified.has(word)) continue;
        if (tables.has(match[1]) || schema.has(word) || virtual.has(word)) continue;
        console.log(
          `  ? ${path}:${line} — "${word}" is not a column of ` +
            `${[...new Set(tables.values())].join(", ")}`,
        );
        problems += 1;
      }
      }
    }
  }
}

console.log(
  problems === 0
    ? `\nEvery column referenced exists. ${schema.size} tables read from ${MIGRATIONS}.`
    : `\n${problems} to look at. A "?" may be a function this tool does not know; ` +
        `a "✗" is wrong.`,
);
process.exit(problems > 0 ? 1 : 0);
