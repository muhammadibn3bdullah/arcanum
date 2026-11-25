import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arcanum.config.settings import Settings
from arcanum.permissions.authorization_gateway import AuthorizationGateway
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_manager import DynamicPermissionManager, PermissionStore
from arcanum.permissions.permission_models import PermissionRequest
from arcanum.tools import (
    CaidoGraphQLClient,
    CaidoToolAdapter,
    CaidoUnavailable,
    build_mock_registry,
    install_caido_adapter,
)


class FakeCaidoGraphQL(BaseHTTPRequestHandler):
    requests_seen = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        query = payload.get("query", "")
        self.requests_seen.append({
            "query": query,
            "variables": payload.get("variables", {}),
            "auth": self.headers.get("Authorization"),
        })
        if "__typename" in query:
            data = {"__typename": "Query"}
        elif "requestsByOffset" in query:
            data = {"requestsByOffset": {"nodes": [
                {
                    "id": "req1", "host": "lab.example.test", "method": "GET",
                    "path": "/api/v1/users", "response": {"statusCode": 200},
                },
                {
                    "id": "req2", "host": "lab.example.test", "method": "GET",
                    "path": "/api/v1/health", "response": {"statusCode": 200},
                },
            ]}}
        else:
            data = {}
        body = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_caido_url(monkeypatch):
    FakeCaidoGraphQL.requests_seen = []
    monkeypatch.setenv("CAIDO_TOKEN", "test-token")
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeCaidoGraphQL)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/graphql"
    server.shutdown()
    server.server_close()


def test_client_status_and_host_query(fake_caido_url):
    client = CaidoGraphQLClient(fake_caido_url, timeout=3)
    assert client.status()["data"]["__typename"] == "Query"
    nodes = client.requests_by_host("lab.example.test", limit=10)
    assert len(nodes) == 2
    assert FakeCaidoGraphQL.requests_seen[-1]["variables"]["filter"]["code"] == (
        'req.host.cont:"lab.example.test"'
    )
    assert FakeCaidoGraphQL.requests_seen[-1]["auth"] == "Bearer test-token"


def test_adapter_reads_captured_traffic(fake_caido_url):
    adapter = CaidoToolAdapter(fake_caido_url)
    result = adapter.read({"host": "lab.example.test", "limit": 10})
    payload = result["untrusted_data"]
    assert payload["source"] == "caido_graphql"
    assert payload["count"] == 2
    assert payload["requests"][0]["path"] == "/api/v1/users"


def test_real_caido_handler_runs_through_gateway(tmp_path, fake_caido_url):
    dpm = DynamicPermissionManager(
        store=PermissionStore(tmp_path / "grants.db"),
        audit=PermissionAuditLog(tmp_path / "audit.db"),
        scope_verifier=lambda target, operation="": True,
    )
    gateway = AuthorizationGateway(dpm)
    registry = build_mock_registry(gateway)
    install_caido_adapter(registry, CaidoToolAdapter(fake_caido_url))
    assert any(not d.mock_only for d in registry.list_tools() if d.name.startswith("caido_"))

    request = PermissionRequest(
        request_id="req_caido", agent_id="recon_agent", task_id="task_1",
        requested_capability="caido_read", target="lab.example.test",
        operation="read", justification="passive fingerprint",
        task_type="passive_recon",
    )
    grant = dpm.request_permission(request, task_authorized=True).grant
    assert grant is not None

    ok, result, reason = gateway.execute(
        "caido_read", "recon_agent", "task_1", "lab.example.test",
        "read", grant.grant_id, params={"host": "lab.example.test"},
    )
    assert ok is True, reason
    assert result["untrusted_data"]["source"] == "caido_graphql"
    assert result["untrusted_data"]["count"] == 2


def test_unreachable_caido_raises_clear_error():
    adapter = CaidoToolAdapter("http://127.0.0.1:1/graphql")
    with pytest.raises(CaidoUnavailable, match="Cannot reach Caido GraphQL"):
        adapter.read({"host": "lab.example.test"})


def test_host_is_required(monkeypatch):
    adapter = CaidoToolAdapter("http://127.0.0.1:1/graphql")
    with pytest.raises(CaidoUnavailable, match="host is required"):
        adapter.read({})


def test_settings_caido_url_from_env(monkeypatch):
    monkeypatch.setenv("ARCANUM_CAIDO_URL", "http://127.0.0.1:9999/graphql")
    assert Settings().caido_url == "http://127.0.0.1:9999/graphql"


def test_token_discovered_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("CAIDO_TOKEN", raising=False)
    monkeypatch.delenv("CAIDO_ACCESS_TOKEN", raising=False)
    token_file = tmp_path / "token.txt"
    token_file.write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("CAIDO_TOKEN_FILE", str(token_file))
    from arcanum.tools import CaidoGraphQLClient
    assert CaidoGraphQLClient("http://127.0.0.1:1/graphql").token == "file-token"
