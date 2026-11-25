import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from arcanum.config.settings import Settings
from arcanum.permissions.authorization_gateway import AuthorizationGateway
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_manager import DynamicPermissionManager, PermissionStore
from arcanum.permissions.permission_models import PermissionRequest
from arcanum.tools import (
    BurpMCPClient,
    BurpToolAdapter,
    BurpUnavailable,
    build_mock_registry,
    install_burp_adapter,
)


class FakeBurpMCP(BaseHTTPRequestHandler):
    calls = []
    tools = [
        {"name": "send_request_to_repeater"},
        {"name": "intercept_request"},
    ]

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = payload.get("method")
        req_id = payload.get("id")

        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "fake-burp"}}
        elif method == "tools/list":
            result = {"tools": self.tools}
        elif method == "tools/call":
            name = payload["params"]["name"]
            self.calls.append(name)
            result = {"content": [{"type": "text", "text": f"called {name}"}]}
        elif method == "notifications/initialized":
            result = {}
        else:
            self.send_response(400)
            self.end_headers()
            return

        body = json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def fake_burp_url():
    FakeBurpMCP.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBurpMCP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/mcp"
    server.shutdown()
    server.server_close()


def test_burp_client_initialize_list_and_call(fake_burp_url):
    client = BurpMCPClient(fake_burp_url, timeout=3)
    client.initialize()
    tools = client.list_tools()
    assert any(t["name"] == "send_request_to_repeater" for t in tools)
    client.call_tool("send_request_to_repeater", {"request": "GET / HTTP/1.1"})
    assert FakeBurpMCP.calls == ["send_request_to_repeater"]


def test_adapter_maps_proxy_capabilities(fake_burp_url):
    adapter = BurpToolAdapter(fake_burp_url)
    assert any("repeater" in t for t in adapter.available_tools())
    result = adapter.repeater({"request": "GET / HTTP/1.1"})
    assert result["untrusted_data"]["source"] == "burp_mcp"
    assert result["untrusted_data"]["tool"] == "send_request_to_repeater"
    adapter.intercept({"request": "GET / HTTP/1.1", "action": "hold"})
    assert FakeBurpMCP.calls == ["send_request_to_repeater", "intercept_request"]


def test_real_burp_handler_runs_through_gateway(tmp_path, fake_burp_url):
    dpm = DynamicPermissionManager(
        store=PermissionStore(tmp_path / "grants.db"),
        audit=PermissionAuditLog(tmp_path / "audit.db"),
        scope_verifier=lambda target, operation="": True,
    )
    gateway = AuthorizationGateway(dpm)
    registry = build_mock_registry(gateway)
    adapter = install_burp_adapter(registry, BurpToolAdapter(fake_burp_url))
    assert any(not d.mock_only for d in registry.list_tools() if d.name.startswith("burp_"))

    request = PermissionRequest(
        request_id="req_burp", agent_id="web_security_agent", task_id="task_1",
        requested_capability="burp_repeater", target="lab_app",
        operation="repeat", justification="authorized lab",
        task_type="active_web_test",
    )
    grant = dpm.approve_request(request, task_authorized=True).grant
    assert grant is not None

    ok, result, reason = gateway.execute(
        "burp_repeater", "web_security_agent", "task_1", "lab_app",
        "repeat", grant.grant_id, params={"request": "GET / HTTP/1.1"},
    )
    assert ok is True, reason
    assert result["untrusted_data"]["source"] == "burp_mcp"
    assert result["untrusted_data"]["tool"] == "send_request_to_repeater"


def test_unreachable_burp_raises_clear_error():
    adapter = BurpToolAdapter("http://127.0.0.1:1/mcp")
    with pytest.raises(BurpUnavailable, match="Cannot reach Burp MCP"):
        adapter.available_tools()


def test_settings_burp_url_from_env(monkeypatch):
    monkeypatch.setenv("ARCANUM_BURP_MCP_URL", "http://127.0.0.1:9999/mcp")
    assert Settings().burp_mcp_url == "http://127.0.0.1:9999/mcp"


class FakeBurpSSE(BaseHTTPRequestHandler):
    streams = []
    lock = threading.Lock()

    def log_message(self, *args):
        pass

    def do_POST(self):
        if self.path.startswith("/mcp"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = payload.get("method")
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05"}
        elif method == "tools/list":
            result = {"tools": [{"name": "create_repeater_tab"}]}
        else:
            result = {}
        message = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": result})
        with self.lock:
            for stream in list(self.streams):
                try:
                    stream.write(f"event: message\ndata: {message}\n\n".encode())
                    stream.flush()
                except Exception:
                    pass
        self.send_response(202)
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(
            b"event: endpoint\ndata: ?sessionId=fake-session\n\n"
        )
        self.wfile.flush()
        with self.lock:
            self.streams.append(self.wfile)
        try:
            while True:
                time.sleep(0.1)
        except Exception:
            pass
        finally:
            with self.lock:
                if self.wfile in self.streams:
                    self.streams.remove(self.wfile)

def test_client_falls_back_to_legacy_sse():
    FakeBurpSSE.streams = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeBurpSSE)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/mcp"
    try:
        client = BurpMCPClient(url, timeout=5)
        client.initialize()
        tools = client.list_tools()
        assert client._sse_endpoint is not None
        assert tools[0]["name"] == "create_repeater_tab"
    finally:
        server.shutdown()
        server.server_close()
