import { Database } from "bun:sqlite";
import { randomBytes } from "node:crypto";
import path from "node:path";

const DATA_DIR = process.env.DATA_DIR ?? import.meta.dir;
const DB_PATH = path.join(DATA_DIR, "hackbots.db");

export const db = new Database(DB_PATH);
db.exec("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;");

db.exec(`
  CREATE TABLE IF NOT EXISTS targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'web',
    base_url TEXT NOT NULL DEFAULT '',
    in_scope TEXT NOT NULL DEFAULT '',
    out_of_scope TEXT NOT NULL DEFAULT '',
    auth_notes TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id INTEGER,
    title TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    kind TEXT NOT NULL DEFAULT 'endpoint',
    status TEXT NOT NULL DEFAULT 'open',
    detail TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    run_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (target_id) REFERENCES targets(id) ON DELETE CASCADE
  );

  CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    target TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    budget INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'queued',
    started_at TEXT NOT NULL DEFAULT (datetime('now'))
  );

  CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
  );
`);

const defaults: Record<string, string> = {
  webhook_url: process.env.DISCORD_WEBHOOK_URL ?? "",
  severity_threshold: "high",
  ingest_token: process.env.INGEST_TOKEN ?? randomBytes(24).toString("hex"),
  eval_endpoint: "",
};

for (const [key, value] of Object.entries(defaults)) {
  db.query("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)").run(key, value);
}

// An explicit env webhook always wins over a stored (possibly empty) value.
if (process.env.DISCORD_WEBHOOK_URL) {
  setSetting("webhook_url", process.env.DISCORD_WEBHOOK_URL);
}

export function getSetting(key: string): string {
  const row = db.query("SELECT value FROM settings WHERE key = ?").get(key) as
    | { value: string }
    | undefined;
  return row?.value ?? "";
}

export function setSetting(key: string, value: string): void {
  db.query(
    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
  ).run(key, value);
}

export const SEVERITY_RANK: Record<string, number> = {
  info: 0,
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

export function atOrAbove(severity: string, threshold: string): boolean {
  return (SEVERITY_RANK[severity] ?? 0) >= (SEVERITY_RANK[threshold] ?? 99);
}

export { DB_PATH };
