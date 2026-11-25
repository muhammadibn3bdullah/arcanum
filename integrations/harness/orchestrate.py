#!/usr/bin/env python3
"""Phase 3 (local): orchestrator that runs hunts as local worker processes.

No VPS. A "worker" is a Maestro agent invocation with a time budget, scoped by
the target record in the harness DB. Transcripts are saved under reports/runs/.

Usage:
  python3 orchestrate.py hunt <target-name-or-id> [--budget 10] [--agent js-hunter]
        [--mode local-js|caido] [--dir lab/sample-app] [--allow-active] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # project root
DB = Path(os.environ.get("HARNESS_DB", str(ROOT / "harness" / "hackbots.db")))
CLI = os.environ.get("MAESTRO_CLI", str(Path.home() / ".local/bin/maestro-cli"))
RUNS_DIR = ROOT / "reports" / "runs"


def db_conn():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def find_target(name: str):
    with db_conn() as con:
        rows = con.execute(
            "SELECT * FROM targets WHERE name = ? OR id = ?", (name, name)
        ).fetchall()
    return dict(rows[0]) if rows else None


def build_prompt(t: dict, run_id: str, mode: str, directory: str, allow_active: bool) -> str:
    active = (
        "Active probing IS authorized for this run; keep probes low-volume."
        if allow_active
        else "Active probing is NOT authorized; passive techniques only."
    )
    if mode == "local-js":
        task = (
            f"Run the js_analysis skill on the local directory `{directory}` "
            "(local files only, no network). Then push the resulting endpoint list "
            f"with the harness-ingest MCP tool ingest_file(path=..., source='{run_id}', "
            f"run_id='{run_id}', target_id={t['id']})."
        )
    else:
        task = (
            "Run the api-fingerprint-caido skill against Caido traffic for the target. "
            "If Caido has no captured traffic, report blocked — do not improvise. "
            f"Push each finding with harness-ingest ingest_finding(run_id='{run_id}', "
            f"target_id={t['id']})."
        )
    return (
        f"AUTHORIZED LOCAL HUNT {run_id}\n"
        f"TARGET: {t['name']} (id {t['id']}) base_url={t['base_url']}\n"
        f"IN SCOPE: {t['in_scope']}\n"
        f"OUT OF SCOPE: {t['out_of_scope'] or '(none recorded)'}\n"
        f"AUTH NOTES: {t['auth_notes'] or '(none)'}\n"
        f"{active}\n"
        "Before touching any host, confirm it with harness-ingest check_scope; "
        "if DENIED or UNKNOWN, stop and report blocked.\n"
        f"{task}\n"
        "When finished, summarize findings count and highest-value leads."
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Local hunt orchestrator (Phase 3)")
    p.add_argument("command", choices=["hunt"])
    p.add_argument("target")
    p.add_argument("--budget", type=int, default=10, help="minutes")
    p.add_argument("--agent", default="js-hunter")
    p.add_argument("--mode", choices=["local-js", "caido"], default="local-js")
    p.add_argument("--dir", default="lab/sample-app")
    p.add_argument("--allow-active", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    t = find_target(a.target)
    if not t:
        print(f"ERROR: target '{a.target}' not recorded in harness. Add it in the dashboard first.")
        return 2
    if not (t["in_scope"] or "").strip():
        print("ERROR: target has no recorded in-scope list. Refusing to hunt.")
        return 2

    run_id = f"run_{int(time.time())}"
    prompt = build_prompt(t, run_id, a.mode, a.dir, a.allow_active)

    if a.dry_run:
        print(f"[dry-run] run_id={run_id} agent={a.agent} budget={a.budget}m mode={a.mode}")
        print(prompt)
        return 0

    with db_conn() as con:
        con.execute(
            "INSERT INTO runs (id, target, model, budget, status) VALUES (?, ?, ?, ?, 'running')",
            (run_id, t["name"], a.agent, a.budget),
        )
        con.commit()

    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    transcript = RUNS_DIR / f"{run_id}.md"
    print(f"Starting {run_id} (agent={a.agent}, budget={a.budget}m, mode={a.mode})")

    status = "done"
    try:
        proc = subprocess.run(
            [CLI, "send", a.agent, prompt],
            capture_output=True, text=True, timeout=a.budget * 60,
        )
        out = proc.stdout.strip()
        try:
            payload = json.loads(out)
            response = payload.get("response") or payload.get("error") or out
            if not payload.get("success"):
                status = "failed"
        except Exception:
            response = out or proc.stderr
        if "blocked" in str(response).lower():
            status = "blocked"
    except subprocess.TimeoutExpired:
        response = f"Worker exceeded budget of {a.budget} minutes; terminated."
        status = "failed"
    except Exception as exc:
        response = f"Worker error: {exc}"
        status = "failed"

    transcript.write_text(
        f"# {run_id}\n\n- target: {t['name']}\n- agent: {a.agent}\n- mode: {a.mode}\n"
        f"- status: {status}\n\n## Prompt\n\n{prompt}\n\n## Response\n\n{response}\n",
        encoding="utf-8",
    )
    with db_conn() as con:
        con.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        con.commit()

    print(f"{run_id} -> {status}")
    print(f"transcript: {transcript}")
    print(str(response)[:600])
    return 0 if status == "done" else 1


if __name__ == "__main__":
    sys.exit(main())
