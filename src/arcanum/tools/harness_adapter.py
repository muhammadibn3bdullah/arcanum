"""Gateway-wrapped adapter for the Hackbots harness Shared Findings Store.

Agents never call the raw ``harness-ingest`` MCP tools. Instead the DPM grants
``finding_write`` / ``evidence_write``, the AuthorizationGateway verifies the
grant, and the handlers below write through the same authenticated harness
ingest endpoint that the MCP server uses.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..core.proof_gate import FindingProof, ProofGate
from .tool_registry import ToolDefinition, ToolRegistry

DEFAULT_HARNESS_URL = os.environ.get("HARNESS_URL", "http://127.0.0.1:13337").rstrip("/")
DEFAULT_HARNESS_DB = Path(
    os.environ.get(
        "HARNESS_DB",
        str(Path.home() / "Projects/hackbots-starter/harness/hackbots.db"),
    )
)

VALID_SEVERITIES = {"info", "low", "medium", "high", "critical"}


class HarnessUnavailable(RuntimeError):
    """Raised when the harness ingest endpoint cannot be used."""


class HarnessIngestClient:
    def __init__(self, url: str = DEFAULT_HARNESS_URL, db_path: Path = DEFAULT_HARNESS_DB):
        self.url = url.rstrip("/")
        self.db_path = Path(db_path)
        self.timeout = 15.0

    def _token(self) -> str:
        if not self.db_path.exists():
            raise HarnessUnavailable(f"Harness DB not found: {self.db_path}")
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute(
                "SELECT value FROM settings WHERE key = 'ingest_token'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise HarnessUnavailable(f"Cannot read harness token: {exc}") from exc
        finally:
            con.close()
        if not row or not row[0]:
            raise HarnessUnavailable("No ingest token found in harness DB")
        return str(row[0])

    def ingest(self, payload: dict[str, Any]) -> dict[str, Any]:
        severity = str(payload.get("severity", "info")).lower()
        if severity not in VALID_SEVERITIES:
            raise HarnessUnavailable(f"Invalid severity '{severity}'")
        body = {
            "title": str(payload.get("title", ""))[:160],
            "severity": severity,
            "kind": str(payload.get("kind", "endpoint")),
            "detail": str(payload.get("detail", "")),
            "source": str(payload.get("source", "arcanum-gateway")),
            "target_id": payload.get("target_id") or None,
            "run_id": payload.get("run_id") or None,
        }
        if not body["title"]:
            raise HarnessUnavailable("A finding title is required")

        request = urllib.request.Request(
            self.url + "/api/ingest",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer " + self._token(),
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HarnessUnavailable(f"Cannot reach harness ingest at {self.url}: {exc}") from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise HarnessUnavailable(f"Harness returned non-JSON response: {raw[:200]}") from exc

    def update_finding_status(self, finding_id: str, status: str) -> bool:
        if not self.db_path.exists():
            raise HarnessUnavailable(f"Harness DB not found: {self.db_path}")
        con = sqlite3.connect(self.db_path)
        try:
            cur = con.execute(
                "UPDATE findings SET status = ? WHERE id = ?",
                (status, int(finding_id)),
            )
            con.commit()
            return cur.rowcount > 0
        except (ValueError, sqlite3.Error) as exc:
            raise HarnessUnavailable(f"Cannot update finding status: {exc}") from exc
        finally:
            con.close()


class HarnessToolAdapter:
    def __init__(self, client: HarnessIngestClient | None = None):
        self.client = client or HarnessIngestClient()

    def finding_write(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self.client.ingest({
            "title": params.get("title", ""),
            "severity": params.get("severity", "info"),
            "kind": params.get("kind", "endpoint"),
            "detail": params.get("detail", ""),
            "source": params.get("source", "arcanum-gateway"),
            "target_id": params.get("target_id"),
            "run_id": params.get("run_id"),
        })
        return {"untrusted_data": {"source": "harness_ingest", "ingest": result}}

    def finding_status_write(self, params: dict[str, Any]) -> dict[str, Any]:
        proof = FindingProof(
            finding_id=str(params.get("finding_id", "")),
            request_text=str(params.get("request_text", "")),
            response_text=str(params.get("response_text", "")),
            cross_user_evidence=str(params.get("cross_user_evidence", "")),
            validated_by=str(params.get("validated_by", "")),
        )
        confirmed = ProofGate.can_confirm(proof)
        if confirmed:
            self.client.update_finding_status(proof.finding_id, "confirmed")
        return {
            "untrusted_data": {
                "source": "proof_gate",
                "finding_id": proof.finding_id,
                "confirmed": confirmed,
                "reason": "proof complete" if confirmed else "proof incomplete",
            }
        }

    def report_write(self, params: dict[str, Any]) -> dict[str, Any]:
        findings = params.get("findings", []) or []
        confirmed_ids = {str(x) for x in (params.get("confirmed_ids", []) or [])}
        included = ProofGate.confirmed_only(findings, confirmed_ids)
        lines = ["# Arcanum report draft", ""]
        for finding in included:
            lines.append(
                f"- [{finding.get('severity', 'info')}] "
                f"{finding.get('title', finding.get('id', 'unknown'))}"
            )
        draft = "\n".join(lines)
        return {
            "untrusted_data": {
                "source": "report_writer",
                "included_count": len(included),
                "draft": draft,
            }
        }

    def evidence_write(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self.client.ingest({
            "title": params.get("title", "EVIDENCE"),
            "severity": params.get("severity", "info"),
            "kind": "evidence",
            "detail": params.get("detail", ""),
            "source": params.get("source", "arcanum-gateway"),
            "target_id": params.get("target_id"),
            "run_id": params.get("run_id"),
        })
        return {"untrusted_data": {"source": "harness_ingest", "ingest": result}}


def install_harness_adapter(
    registry: ToolRegistry,
    adapter: HarnessToolAdapter | None = None,
) -> HarnessToolAdapter:
    """Replace mock finding/evidence handlers with real harness writes."""
    adapter = adapter or HarnessToolAdapter()
    handlers = {
        "finding_write": adapter.finding_write,
        "evidence_write": adapter.evidence_write,
        "finding_status_write": adapter.finding_status_write,
        "report_write": adapter.report_write,
    }
    for capability, handler in handlers.items():
        definition: ToolDefinition | None = registry.get(capability)
        if definition is None:
            continue
        definition.mock_only = False
        registry.register(definition, handler)
    return adapter
