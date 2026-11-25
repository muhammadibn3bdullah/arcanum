import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arcanum.permissions.authorization_gateway import AuthorizationGateway
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_manager import DynamicPermissionManager, PermissionStore
from arcanum.permissions.permission_models import PermissionRequest
from arcanum.tools import (
    HarnessIngestClient,
    HarnessToolAdapter,
    HarnessUnavailable,
    build_mock_registry,
    install_harness_adapter,
)


class FakeHarness(BaseHTTPRequestHandler):
    payloads = []
    auth_headers = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self.payloads.append(payload)
        self.auth_headers.append(self.headers.get("Authorization"))
        body = json.dumps({"id": 999, "status": "ok"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_harness(tmp_path):
    FakeHarness.payloads = []
    FakeHarness.auth_headers = []
    db = tmp_path / "harness.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO settings VALUES ('ingest_token', 'test-token')")
    con.commit()
    con.close()

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeHarness)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"
    yield url, db
    server.shutdown()
    server.server_close()


def test_client_posts_authenticated_finding(fake_harness):
    url, db = fake_harness
    client = HarnessIngestClient(url, db)
    result = client.ingest({
        "title": "Test finding",
        "severity": "medium",
        "kind": "endpoint",
        "detail": "evidence",
        "target_id": 6,
    })
    assert result["id"] == 999
    assert FakeHarness.auth_headers[-1] == "Bearer test-token"
    assert FakeHarness.payloads[-1]["title"] == "Test finding"
    assert FakeHarness.payloads[-1]["target_id"] == 6


def test_client_validates_payload(fake_harness):
    url, db = fake_harness
    client = HarnessIngestClient(url, db)
    with pytest.raises(HarnessUnavailable, match="Invalid severity"):
        client.ingest({"title": "x", "severity": "URGENT"})
    with pytest.raises(HarnessUnavailable, match="title is required"):
        client.ingest({})


def test_unreachable_harness_raises_clear_error(tmp_path):
    client = HarnessIngestClient("http://127.0.0.1:1", tmp_path / "none.db")
    with pytest.raises(HarnessUnavailable, match="Harness DB not found"):
        client.ingest({"title": "x"})


def test_harness_handler_runs_through_gateway(tmp_path, fake_harness):
    url, db = fake_harness
    dpm = DynamicPermissionManager(
        store=PermissionStore(tmp_path / "grants.db"),
        audit=PermissionAuditLog(tmp_path / "audit.db"),
        scope_verifier=lambda target, operation="": True,
    )
    gateway = AuthorizationGateway(dpm)
    registry = build_mock_registry(gateway)
    client = HarnessIngestClient(url, db)
    install_harness_adapter(registry, HarnessToolAdapter(client))
    assert any(not d.mock_only for d in registry.list_tools() if d.name in ("finding_write", "evidence_write"))

    request = PermissionRequest(
        request_id="req_finding", agent_id="web_security_agent", task_id="task_1",
        requested_capability="finding_write", target="lab_app", operation="write",
        justification="test",
    )
    grant = dpm.request_permission(request, task_authorized=True).grant
    ok, result, reason = gateway.execute(
        "finding_write", "web_security_agent", "task_1", "lab_app",
        "write", grant.grant_id,
        params={"title": "Gateway finding", "severity": "high", "kind": "vulnerability"},
    )
    assert ok is True, reason
    assert result["untrusted_data"]["source"] == "harness_ingest"
    assert FakeHarness.payloads[-1]["title"] == "Gateway finding"
