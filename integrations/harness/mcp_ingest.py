"""MCP server: let OpenCode/Maestro agents push findings into the harness.

The ingest token is read directly from the harness SQLite DB, so no secret is
stored in OpenCode config. Runs locally, no cloud.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import sqlite3
import time
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("harness-ingest")

HARNESS_URL = os.environ.get("HARNESS_URL", "http://127.0.0.1:13337").rstrip("/")
DB_PATH = Path(
    os.environ.get(
        "HARNESS_DB",
        str(Path.home() / "Projects/hackbots-starter/harness/hackbots.db"),
    )
)


def _token() -> str:
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT value FROM settings WHERE key = 'ingest_token'"
        ).fetchone()
        return row[0] if row else ""
    finally:
        con.close()


def _rows(sql: str, args: tuple = ()) -> list[dict]:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _find_target(target: str) -> dict | None:
    rows = _rows("SELECT * FROM targets WHERE id = ? OR name = ?", (target, target))
    if not rows and target.isdigit():
        rows = _rows("SELECT * FROM targets WHERE id = ?", (int(target),))
    return rows[0] if rows else None


def _host(value: str) -> str:
    return value.strip().lower().split("://")[-1].split("/")[0].split(":")[0]


def _matches_scope(patterns: str, host: str) -> bool:
    parts = re.split(r"[,;\n\r ]+", patterns or "")
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if fnmatch.fnmatch(host, p) or fnmatch.fnmatch(host, p.lstrip("*.")):
            return True
        if p.startswith("*.") and host.endswith(p[1:]):
            return True
        if p in host or host.endswith("." + p):
            return True
    return False


def _post(payload: dict) -> str:
    token = _token()
    if not token:
        return "ERROR: no ingest token found in harness DB"
    req = urllib.request.Request(
        HARNESS_URL + "/api/ingest",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.dumps(json.load(res))
    except Exception as exc:
        return f"ERROR: ingest failed: {exc}"


@mcp.tool()
def ingest_finding(
    title: str,
    severity: str = "info",
    kind: str = "endpoint",
    detail: str = "",
    source: str = "",
    target_id: int = 0,
    run_id: str = "",
) -> str:
    """Push one finding into the Hackbots harness.

    Args:
        title: Short finding title.
        severity: info | low | medium | high | critical.
        kind: endpoint | exposure | fingerprint | vulnerability | etc.
        detail: Evidence / full text.
        source: Agent or skill that produced it (e.g. "js-hunter").
        target_id: Optional harness target ID (0 = none).
        run_id: Optional harness run ID.
    """
    return _post(
        {
            "title": title,
            "severity": severity,
            "kind": kind,
            "detail": detail,
            "source": source,
            "target_id": target_id or None,
            "run_id": run_id or None,
        }
    )


@mcp.tool()
def ingest_file(
    path: str,
    severity: str = "low",
    kind: str = "endpoint",
    target_id: int = 0,
    source: str = "js-hunter",
    limit: int = 0,
) -> str:
    """Push every non-empty line of a text file into the harness as findings.

    Args:
        path: Text file with one finding/endpoint per line.
        severity: Severity for all lines.
        kind: Kind for all lines.
        target_id: Optional harness target ID (0 = none).
        source: Agent name recorded on each finding.
        limit: Maximum lines to ingest (0 = all).
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return f"ERROR: file not found: {p}"
    lines = [l.strip() for l in p.read_text(encoding="utf-8").splitlines()
             if l.strip() and not l.startswith("#")]
    if limit:
        lines = lines[:limit]
    if not lines:
        return "No findings to ingest"
    results = []
    for line in lines:
        results.append(
            _post(
                {
                    "title": line[:160],
                    "severity": severity,
                    "kind": kind,
                    "detail": line,
                    "source": source,
                    "target_id": target_id or None,
                }
            )
        )
    ok = sum(1 for r in results if r.startswith("{"))
    return f"Ingested {ok}/{len(lines)} findings from {p}"


@mcp.tool()
def list_targets() -> str:
    """List all targets recorded in the harness (id, name, platform, base URL)."""
    rows = _rows("SELECT id, name, platform, base_url FROM targets ORDER BY id DESC")
    return json.dumps(rows) if rows else "No targets recorded yet"


@mcp.tool()
def get_target_scope(target: str) -> str:
    """Read the authorized scope for a target from the harness before starting work.

    Args:
        target: Target name or numeric ID (see list_targets).
    """
    t = _find_target(target)
    if not t:
        return f"ERROR: no target named '{target}'. Use list_targets to see available targets."
    return (
        f"SCOPE FOR {t['name']} (id {t['id']})\n"
        f"  platform: {t['platform']}\n"
        f"  base_url: {t['base_url']}\n"
        f"  IN SCOPE: {t['in_scope'] or '(not recorded)'}\n"
        f"  OUT OF SCOPE: {t['out_of_scope'] or '(not recorded)'}\n"
        f"  AUTH NOTES: {t['auth_notes'] or '(none)'}\n"
        f"  NOTES: {t['notes'] or '(none)'}\n"
        "Only work on IN SCOPE hosts. Exclude OUT OF SCOPE hosts. If scope is "
        "missing or ambiguous, stop and report blocked — do not guess."
    )


@mcp.tool()
def check_scope(target: str, host: str) -> str:
    """Check a single host/URL against the harness target's recorded scope.

    Advisory only: this tells you what the dashboard records; it does not
    technically enforce anything. Deny wins when a host matches both lists.

    Args:
        target: Target name or numeric ID.
        host: Host or URL to check (e.g. 'api.example.com').
    """
    t = _find_target(target)
    if not t:
        return f"ERROR: no target named '{target}'"
    h = _host(host)
    if not h:
        return "ERROR: could not parse a host from that value"
    if _matches_scope(t["out_of_scope"], h):
        result = "DENIED"
    elif _matches_scope(t["in_scope"], h):
        result = "ALLOWED"
    else:
        result = "UNKNOWN (not covered by recorded scope)"
    return json.dumps({"target": t["name"], "host": h, "result": result})


@mcp.tool()
def start_run(target: str, model: str = "", budget: int = 0) -> str:
    """Create a harness run record before a hunt starts.

    Args:
        target: Target name (free text).
        model: Agent model name.
        budget: Time budget in minutes.
    """
    run_id = f"run_{int(time.time())}"
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO runs (id, target, model, budget, status) VALUES (?, ?, ?, ?, 'running')",
            (run_id, target, model, budget),
        )
        con.commit()
    finally:
        con.close()
    return f"Started run {run_id} for {target}"


@mcp.tool()
def finish_run(run_id: str, status: str = "done") -> str:
    """Mark a harness run as finished.

    Args:
        run_id: Run ID returned by start_run.
        status: done | failed | blocked.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
        con.commit()
    finally:
        con.close()
    return f"Run {run_id} marked {status}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
