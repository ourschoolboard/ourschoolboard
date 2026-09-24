#!/usr/bin/env node
/* Numbered, ordered, checksummed database migrations.
 *
 *   node scripts/migrate.js status     what is applied, what is pending
 *   node scripts/migrate.js up         apply everything pending
 *   node scripts/migrate.js verify     applied files still match their checksums
 *
 * Files live in migrations/NNNN_name.sql and run in numeric order. They are
 * written with {{p}} where a table name goes, so staging gets staging_ tables
 * from the identical file — the same substitution the app uses at query time.
 * The ledger is per-prefix, so staging and production track their versions
 * independently even though they share one database.
 *
 * Three properties worth the extra code:
 *
 *   Checksums. A migration that has already run is frozen. Editing one is the
 *   classic way environments silently diverge: prod ran the old text, your
 *   laptop ran the new text, and nothing ever tells you. `verify` fails loudly
 *   instead, and `up` refuses to proceed.
 *
 *   One transaction per migration. A failure rolls back that migration whole,
 *   so a half-applied file can never be recorded as applied. (Postgres does DDL
 *   transactionally, unlike MySQL. The exception is CREATE INDEX CONCURRENTLY,
 *   which cannot run in a transaction — put one of those in its own file marked
 *   with the -- no-transaction pragma below.)
 *
 *   An advisory lock. Two deploys landing together would otherwise both see the
 *   same pending list and both try to apply it.
 */
import "../backend/load-env.js";

import { readFileSync, readdirSync, existsSync } from "node:fs";
import path from "node:path";
import crypto from "node:crypto";
import { fileURLToPath } from "node:url";

import { APP_ENV, TABLE_PREFIX, applyPrefix, t } from "../backend/env.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const MIGRATIONS_DIR = path.resolve(here, "..", "migrations");
const LEDGER = t("schema_migrations");

// Distinct lock per prefix: staging and production migrate independently.
const LOCK_KEY = Number(
  BigInt("0x" + crypto.createHash("sha1").update(`osb:${TABLE_PREFIX}`).digest("hex").slice(0, 12))
  % 2147483647n
);

function discover() {
  if (!existsSync(MIGRATIONS_DIR)) return [];
  const files = readdirSync(MIGRATIONS_DIR).filter((f) => f.endsWith(".sql")).sort();
  const seen = new Map();
  return files.map((file) => {
    const m = /^(\d{4})_([a-z0-9_]+)\.sql$/.exec(file);
    if (!m) {
      throw new Error(`migration "${file}" must be named NNNN_lower_snake_case.sql`);
    }
    const version = Number(m[1]);
    if (seen.has(version)) {
      // Two people numbering 0007 on separate branches is the one merge conflict
      // you want to be loud, not silently ordered by filename.
      throw new Error(`duplicate migration number ${m[1]}: ${seen.get(version)} and ${file}`);
    }
    seen.set(version, file);
    const sql = readFileSync(path.join(MIGRATIONS_DIR, file), "utf8");
    return {
      version,
      file,
      name: m[2],
      sql,
      // Checksum the file as written, before prefix substitution: the same
      // migration must have the same identity in staging and production.
      checksum: crypto.createHash("sha256").update(sql).digest("hex"),
      noTransaction: /^--\s*no-transaction\b/m.test(sql)
    };
  });
}

async function connect() {
  if (!process.env.DATABASE_URL) {
    console.error("DATABASE_URL is not set — nothing to migrate.");
    process.exit(2);
  }
  const { default: pg } = await import("pg");
  const url = new URL(process.env.DATABASE_URL);
  const ssl = url.searchParams.get("sslmode") === "require" ? { rejectUnauthorized: false } : undefined;
  url.searchParams.delete("sslmode");
  const pool = new pg.Pool({ connectionString: url.toString(), ssl });
  return pool;
}

async function ensureLedger(client) {
  await client.query(`
    create table if not exists ${LEDGER} (
      version     integer primary key,
      name        text        not null,
      checksum    text        not null,
      applied_at  timestamptz not null default now(),
      duration_ms integer
    )`);
}

async function appliedMap(client) {
  const { rows } = await client.query(`select version, name, checksum, applied_at from ${LEDGER}`);
  return new Map(rows.map((r) => [r.version, r]));
}

/** Applied migrations whose file no longer matches what was run. */
function drifted(all, applied) {
  const out = [];
  for (const m of all) {
    const rec = applied.get(m.version);
    if (rec && rec.checksum !== m.checksum) out.push({ m, rec });
  }
  // A migration recorded in the database with no file at all is just as wrong.
  const files = new Set(all.map((m) => m.version));
  for (const [version, rec] of applied) {
    if (!files.has(version)) out.push({ m: null, rec, missing: true });
  }
  return out;
}

function banner() {
  console.log(`environment : ${APP_ENV}`);
  console.log(`table prefix: ${TABLE_PREFIX || "(none)"}`);
  console.log(`ledger      : ${LEDGER}`);
}

async function cmdStatus(pool) {
  const client = await pool.connect();
  try {
    await ensureLedger(client);
    const all = discover();
    const applied = await appliedMap(client);
    banner();
    console.log("");
    if (!all.length) return console.log("no migrations found");
    for (const m of all) {
      const rec = applied.get(m.version);
      const mark = !rec ? "PENDING" : rec.checksum === m.checksum ? "applied" : "CHANGED";
      const when = rec ? new Date(rec.applied_at).toISOString().slice(0, 19).replace("T", " ") : "";
      console.log(`  ${String(m.version).padStart(4, "0")}  ${mark.padEnd(8)} ${m.name.padEnd(34)} ${when}`);
    }
    const pending = all.filter((m) => !applied.has(m.version));
    console.log("");
    console.log(`${all.length} migration(s), ${pending.length} pending`);
    const bad = drifted(all, applied);
    if (bad.length) {
      console.log("");
      for (const d of bad) {
        console.log(d.missing
          ? `  DRIFT: ${String(d.rec.version).padStart(4, "0")} ${d.rec.name} is recorded as applied but its file is gone`
          : `  DRIFT: ${String(d.m.version).padStart(4, "0")} ${d.m.name} has been edited since it was applied`);
      }
      process.exitCode = 1;
    }
  } finally {
    client.release();
  }
}

async function cmdVerify(pool) {
  const client = await pool.connect();
  try {
    await ensureLedger(client);
    const all = discover();
    const applied = await appliedMap(client);
    const bad = drifted(all, applied);
    const pending = all.filter((m) => !applied.has(m.version));
    banner();
    if (bad.length) {
      for (const d of bad) {
        console.error(d.missing
          ? `DRIFT: ${d.rec.name} recorded as applied but its file is gone`
          : `DRIFT: ${d.m.file} was edited after it was applied`);
      }
      process.exit(1);
    }
    console.log(`ok — ${applied.size} applied, ${pending.length} pending, no drift`);
    if (pending.length) process.exitCode = 3; // distinct: healthy but behind
  } finally {
    client.release();
  }
}

async function cmdUp(pool) {
  const client = await pool.connect();
  try {
    banner();
    // Serialise: two deploys landing together would both see the same pending
    // list. The lock is released when the session ends, even on a crash.
    await client.query("select pg_advisory_lock($1)", [LOCK_KEY]);

    await ensureLedger(client);
    const all = discover();
    const applied = await appliedMap(client);

    const bad = drifted(all, applied);
    if (bad.length) {
      for (const d of bad) {
        console.error(d.missing
          ? `DRIFT: ${d.rec.name} recorded as applied but its file is gone`
          : `DRIFT: ${d.m.file} was edited after it was applied`);
      }
      console.error("refusing to migrate — write a new migration instead of editing an applied one");
      process.exit(1);
    }

    const pending = all.filter((m) => !applied.has(m.version)).sort((a, b) => a.version - b.version);
    if (!pending.length) {
      console.log("\nnothing to apply");
      return;
    }
    console.log("");
    for (const m of pending) {
      const sql = applyPrefix(m.sql);
      const started = Date.now();
      process.stdout.write(`  applying ${String(m.version).padStart(4, "0")} ${m.name} ... `);
      try {
        if (!m.noTransaction) await client.query("begin");
        await client.query(sql);
        // Record inside the same transaction: a migration that ran but was not
        // recorded would be re-applied on the next deploy.
        await client.query(
          `insert into ${LEDGER} (version, name, checksum, duration_ms) values ($1,$2,$3,$4)`,
          [m.version, m.name, m.checksum, Date.now() - started]
        );
        if (!m.noTransaction) await client.query("commit");
        console.log(`ok (${Date.now() - started}ms)`);
      } catch (error) {
        if (!m.noTransaction) await client.query("rollback").catch(() => {});
        console.log("FAILED");
        console.error(`\n  ${m.file}: ${error.message}`);
        if (m.noTransaction) {
          console.error("  (this migration is marked -- no-transaction, so it may be half-applied)");
        }
        process.exit(1);
      }
    }
    console.log(`\napplied ${pending.length} migration(s)`);
  } finally {
    await client.query("select pg_advisory_unlock($1)", [LOCK_KEY]).catch(() => {});
    client.release();
  }
}

const command = process.argv[2] || "status";
const pool = await connect();
try {
  if (command === "up") await cmdUp(pool);
  else if (command === "status") await cmdStatus(pool);
  else if (command === "verify") await cmdVerify(pool);
  else {
    console.error(`unknown command "${command}" (expected: up, status, verify)`);
    process.exit(2);
  }
} finally {
  await pool.end();
}
