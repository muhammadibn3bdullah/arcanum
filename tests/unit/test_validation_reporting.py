import sqlite3

from arcanum.core.proof_gate import FindingProof, ProofGate
from arcanum.permissions.authorization_gateway import AuthorizationGateway
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_manager import DynamicPermissionManager, PermissionStore
from arcanum.permissions.permission_models import PermissionRequest
from arcanum.tools import (
    HarnessIngestClient,
    HarnessToolAdapter,
    build_mock_registry,
    install_harness_adapter,
)


def harness_db(tmp_path):
    path = tmp_path / "harness.db"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE findings (id INTEGER PRIMARY KEY, title TEXT, status TEXT)"
    )
    con.execute("INSERT INTO findings VALUES (1, 'IDOR demo', 'open')")
    con.execute("INSERT INTO findings VALUES (2, 'Unconfirmed', 'open')")
    con.commit()
    con.close()
    return path


def test_proof_gate_requires_all_three_parts():
    assert not ProofGate.can_confirm(FindingProof(
        finding_id="1", request_text="GET /u/1", response_text="200", cross_user_evidence=""
    ))
    assert ProofGate.can_confirm(FindingProof(
        finding_id="1", request_text="GET /u/1", response_text="200",
        cross_user_evidence="user B sees user A data",
    ))


def test_status_write_only_confirms_with_complete_proof(tmp_path):
    db = harness_db(tmp_path)
    adapter = HarnessToolAdapter(HarnessIngestClient("http://127.0.0.1:1", db))

    bad = adapter.finding_status_write({
        "finding_id": "1", "request_text": "GET /u/1", "response_text": "200",
        "cross_user_evidence": "",
    })
    assert bad["untrusted_data"]["confirmed"] is False

    good = adapter.finding_status_write({
        "finding_id": "1", "request_text": "GET /u/1", "response_text": "200",
        "cross_user_evidence": "user B sees user A data",
    })
    assert good["untrusted_data"]["confirmed"] is True

    con = sqlite3.connect(db)
    status = con.execute("SELECT status FROM findings WHERE id = 1").fetchone()[0]
    con.close()
    assert status == "confirmed"


def test_report_draft_excludes_unconfirmed_findings():
    adapter = HarnessToolAdapter()
    result = adapter.report_write({
        "findings": [
            {"id": 1, "severity": "high", "title": "Confirmed IDOR"},
            {"id": 2, "severity": "low", "title": "Unconfirmed"},
        ],
        "confirmed_ids": [1],
    })
    payload = result["untrusted_data"]
    assert payload["included_count"] == 1
    assert "Confirmed IDOR" in payload["draft"]
    assert "Unconfirmed" not in payload["draft"]


def test_status_write_still_requires_gateway_grant(tmp_path):
    dpm = DynamicPermissionManager(
        store=PermissionStore(tmp_path / "grants.db"),
        audit=PermissionAuditLog(tmp_path / "audit.db"),
        scope_verifier=lambda target, operation="": True,
    )
    gateway = AuthorizationGateway(dpm)
    registry = build_mock_registry(gateway)
    db = harness_db(tmp_path)
    install_harness_adapter(registry, HarnessToolAdapter(HarnessIngestClient("http://127.0.0.1:1", db)))

    req = PermissionRequest(
        request_id="req_validate", agent_id="vulnerability_validator", task_id="task_1",
        requested_capability="finding_status_write", target="lab_app",
        operation="write", justification="test",
    )
    grant = dpm.request_permission(req, task_authorized=True).grant
    assert grant is not None

    ok, result, reason = gateway.execute(
        "finding_status_write", "vulnerability_validator", "task_1", "lab_app",
        "write", grant.grant_id,
        params={
            "finding_id": "1", "request_text": "GET /u/1", "response_text": "200",
            "cross_user_evidence": "user B sees user A data",
        },
    )
    assert ok is True, reason
    assert result["untrusted_data"]["confirmed"] is True

    forged = gateway.execute(
        "finding_status_write", "vulnerability_validator", "task_1", "lab_app",
        "write", "grant_forged", params={"finding_id": "1"},
    )
    assert forged[0] is False
