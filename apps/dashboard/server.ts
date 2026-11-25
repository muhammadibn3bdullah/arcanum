import { Database } from "bun:sqlite";
import { db, getSetting, setSetting, atOrAbove } from "./db";

// Arcanum owns the canonical shared domain rooms and message store. The
// Dashboard reads them directly; it never creates a competing conversation DB.
const ARCANUM_DB =
  process.env.ARCANUM_DB ?? `${process.env.ARCANUM_HOME ?? `${process.env.HOME ?? ""}/.arcanum`}/arcanum.db`;
const AGENT_IDS = [
  "hackbot-lead", "recon-agent", "hunt-agent", "validate-agent", "report-agent",
];
function arcanumDb(): Database | null {
  try {
    return new Database(ARCANUM_DB, { readonly: true });
  } catch {
    return null;
  }
}

const HOST = process.env.HOST ?? "127.0.0.1";
const PORT = Number(process.env.PORT ?? 13337);
// Optional dashboard token. When set, every /api/* route except /api/health
// and /api/ingest requires `Authorization: Bearer <token>` or ?token=<token>.
const DASHBOARD_TOKEN = process.env.DASHBOARD_TOKEN ?? "";

// Bundle the React UI once at startup (Bun handles TSX; no external build tool).
const build = await Bun.build({
  entrypoints: [import.meta.dir + "/public/app.tsx"],
  outdir: import.meta.dir + "/public/dist",
  target: "browser",
  minify: false,
});
if (!build.success) {
  console.error("UI build failed:", build.logs);
} else {
  console.log("UI bundled -> public/dist/app.js");
}

type Finding = {
  id: number;
  target_id: number | null;
  title: string;
  severity: string;
  kind: string;
  status: string;
  detail: string;
  source: string;
  run_id: string | null;
  created_at: string;
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

async function notifyDiscord(finding: Finding): Promise<boolean> {
  const url = getSetting("webhook_url");
  if (!url) return false;
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        content:
          `**${finding.severity.toUpperCase()} — ${finding.title}**\n` +
          `Kind: ${finding.kind} · Status: ${finding.status} · ID: ${finding.id}` +
          (finding.detail ? `\n${finding.detail.slice(0, 900)}` : ""),
      }),
    });
    return res.ok;
  } catch (err) {
    console.error("Discord webhook failed:", err);
    return false;
  }
}

const server = Bun.serve({
  hostname: HOST,
  port: PORT,
  async fetch(req) {
    const url = new URL(req.url);
    const { pathname } = url;

    // ---------- optional dashboard auth ----------
    if (
      DASHBOARD_TOKEN &&
      pathname.startsWith("/api/") &&
      pathname !== "/api/health" &&
      pathname !== "/api/ingest"
    ) {
      const provided =
        req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ??
        url.searchParams.get("token") ??
        "";
      if (provided !== DASHBOARD_TOKEN) return json({ error: "unauthorized" }, 401);
    }

    // ---------- static UI ----------
    if (pathname === "/" || pathname === "/index.html") {
      return new Response(Bun.file(import.meta.dir + "/public/index.html"));
    }
    if (pathname === "/app.js") {
      return new Response(Bun.file(import.meta.dir + "/public/dist/app.js"), {
        headers: { "content-type": "text/javascript; charset=utf-8" },
      });
    }

    // ---------- bootstrap ----------
    if (pathname === "/api/health") return json({ ok: true });

    if (pathname === "/api/bootstrap" && req.method === "GET") {
      return json({
        targets: db.query("SELECT * FROM targets ORDER BY id DESC").all(),
        findings: db.query("SELECT * FROM findings ORDER BY id DESC").all(),
        runs: db.query("SELECT * FROM runs ORDER BY started_at DESC").all(),
        settings: Object.fromEntries(
          db.query("SELECT key, value FROM settings").all().map((r: any) => [r.key, r.value]),
        ),
      });
    }

    // ---------- canonical shared domain rooms ----------
    if (pathname === "/api/rooms" && req.method === "GET") {
      const ar = arcanumDb();
      if (!ar) return json({ rooms: [], agents: AGENT_IDS, error: "arcanum db unavailable" });
      try {
        const rooms = ar.query(
          `SELECT workflow_id, target_id, canonical_target, room_id, room_name,
                  room_status, last_activity_at
           FROM domain_rooms ORDER BY last_activity_at DESC`,
        ).all();
        const views = ar.query("SELECT agent_id, workflow_id, target_id, room_id FROM room_agent_views").all();
        const counts = ar.query(
          "SELECT room_id, COUNT(*) AS messages FROM canonical_messages GROUP BY room_id",
        ).all();
        return json({
          rooms: rooms.map((r: any) => ({
            ...r,
            agent_views: views.filter((v: any) => v.room_id === r.room_id).map((v: any) => v.agent_id),
            message_count: Number(counts.find((c: any) => c.room_id === r.room_id)?.messages ?? 0),
          })),
          agents: AGENT_IDS,
        });
      } finally {
        ar.close();
      }
    }

    if (pathname === "/api/rooms/messages" && req.method === "GET") {
      const ar = arcanumDb();
      if (!ar) return json({ error: "arcanum db unavailable" }, 503);
      try {
        const roomId = url.searchParams.get("room_id") ?? "";
        const agentId = url.searchParams.get("agent_id") ?? "";
        const afterId = url.searchParams.get("after_id") ?? "";
        const room: any = ar.query("SELECT * FROM domain_rooms WHERE room_id=?").get(roomId);
        if (!room) return json({ error: "room not found" }, 404);
        // An agent view is a navigation reference only: it must resolve to the
        // same canonical room, never to an independent message history.
        if (agentId) {
          const view: any = ar.query(
            "SELECT room_id FROM room_agent_views WHERE agent_id=? AND workflow_id=? AND target_id=?",
          ).get(agentId, room.workflow_id, room.target_id);
          if (!view || view.room_id !== roomId) {
            return json({ error: "agent view does not resolve to this room" }, 403);
          }
        }
        const rows = ar.query(
          `SELECT data FROM canonical_messages WHERE room_id=? AND message_id>?
           ORDER BY rowid LIMIT 500`,
        ).all(roomId, afterId);
        return json({
          room_id: roomId,
          workflow_id: room.workflow_id,
          target_id: room.target_id,
          agent_id: agentId,
          messages: rows.map((r: any) => JSON.parse(r.data)),
        });
      } finally {
        ar.close();
      }
    }

    // ---------- targets ----------
    if (pathname === "/api/targets" && req.method === "POST") {
      const b = await req.json();
      const info = db.query(
        `INSERT INTO targets (name, platform, base_url, in_scope, out_of_scope, auth_notes, notes)
         VALUES (?, ?, ?, ?, ?, ?, ?)`,
      ).run(
        b.name ?? "", b.platform ?? "web", b.base_url ?? "",
        b.in_scope ?? "", b.out_of_scope ?? "", b.auth_notes ?? "", b.notes ?? "",
      );
      return json({ ok: true, id: Number(info.lastInsertRowid) }, 201);
    }

    const targetMatch = pathname.match(/^\/api\/targets\/(\d+)$/);
    if (targetMatch && req.method === "DELETE") {
      db.query("DELETE FROM targets WHERE id = ?").run(Number(targetMatch[1]));
      return json({ ok: true });
    }

    // ---------- findings ----------
    if (pathname === "/api/findings" && req.method === "GET") {
      const q = url.searchParams;
      const clauses: string[] = [];
      const args: any[] = [];
      if (q.get("search")) { clauses.push("(title LIKE ? OR detail LIKE ?)"); args.push(`%${q.get("search")}%`, `%${q.get("search")}%`); }
      if (q.get("severity")) { clauses.push("severity = ?"); args.push(q.get("severity")); }
      if (q.get("status")) { clauses.push("status = ?"); args.push(q.get("status")); }
      if (q.get("kind")) { clauses.push("kind = ?"); args.push(q.get("kind")); }
      if (q.get("target_id")) { clauses.push("target_id = ?"); args.push(Number(q.get("target_id"))); }
      const where = clauses.length ? `WHERE ${clauses.join(" AND ")}` : "";
      return json(db.query(`SELECT * FROM findings ${where} ORDER BY id DESC`).all(...args));
    }

    const findingMatch = pathname.match(/^\/api\/findings\/(\d+)$/);
    if (findingMatch && req.method === "PATCH") {
      const b = await req.json();
      const allowed = ["title", "severity", "kind", "status", "detail", "target_id"];
      const sets: string[] = [];
      const args: any[] = [];
      for (const key of allowed) {
        if (key in b) { sets.push(`${key} = ?`); args.push(b[key]); }
      }
      if (sets.length) {
        args.push(Number(findingMatch[1]));
        db.query(`UPDATE findings SET ${sets.join(", ")} WHERE id = ?`).run(...args);
      }
      return json({ ok: true });
    }

    const resolveMatch = pathname.match(/^\/api\/findings\/(\d+)\/resolve$/);
    if (resolveMatch && req.method === "POST") {
      const row = db.query("SELECT * FROM findings WHERE id = ?").get(Number(resolveMatch[1])) as Finding | undefined;
      if (!row) return json({ error: "not found" }, 404);
      const sent = await notifyDiscord(row);
      db.query("UPDATE findings SET status = 'resolved' WHERE id = ?").run(row.id);
      return json({ ok: true, webhookSent: sent });
    }

    // ---------- settings ----------
    if (pathname === "/api/settings" && req.method === "PATCH") {
      const b = await req.json();
      for (const key of ["webhook_url", "severity_threshold", "eval_endpoint", "ingest_token"]) {
        if (key in b) setSetting(key, String(b[key]));
      }
      return json({ ok: true });
    }

    // ---------- runs ----------
    if (pathname === "/api/runs" && req.method === "POST") {
      const b = await req.json();
      const id = b.id ?? `run_${Date.now().toString(36)}`;
      db.query(
        "INSERT OR REPLACE INTO runs (id, target, model, budget, status) VALUES (?, ?, ?, ?, ?)",
      ).run(id, b.target ?? "", b.model ?? "", Number(b.budget ?? 0), b.status ?? "queued");
      return json({ ok: true, id }, 201);
    }
    const runMatch = pathname.match(/^\/api\/runs\/(.+)$/);
    if (runMatch && req.method === "PATCH") {
      const b = await req.json();
      db.query("UPDATE runs SET status = ? WHERE id = ?").run(b.status ?? "done", runMatch[1]);
      return json({ ok: true });
    }

    // ---------- ingest (workers report here) ----------
    if (pathname === "/api/ingest" && req.method === "POST") {
      const provided = req.headers.get("authorization")?.replace(/^Bearer\s+/i, "") ?? "";
      if (provided !== getSetting("ingest_token")) return json({ error: "unauthorized" }, 401);

      const b = await req.json();
      const severity = String(b.severity ?? "info").toLowerCase();
      const track = getSetting("severity_threshold") || "high";
      const info = db.query(
        `INSERT INTO findings (target_id, title, severity, kind, status, detail, source, run_id)
         VALUES (?, ?, ?, ?, 'open', ?, ?, ?)`,
      ).run(
        b.target_id ? Number(b.target_id) : null,
        String(b.title ?? "Untitled finding"),
        severity,
        String(b.kind ?? "endpoint"),
        String(b.detail ?? ""),
        String(b.source ?? ""),
        b.run_id ? String(b.run_id) : null,
      );
      const id = Number(info.lastInsertRowid);
      const row = db.query("SELECT * FROM findings WHERE id = ?").get(id) as Finding;
      const shouldNotify = atOrAbove(severity, track);
      const webhookSent = shouldNotify ? await notifyDiscord(row) : false;
      return json({ ok: true, id, severity, threshold: track, webhookSent });
    }

    return json({ error: "not found" }, 404);
  },
});

console.log(`Hackbots harness listening on http://${HOST}:${PORT}`);
export default server;
