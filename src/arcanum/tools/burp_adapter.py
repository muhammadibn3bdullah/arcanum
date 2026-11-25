"""Real Burp adapter over PortSwigger's MCP Server extension.

The adapter uses only the Python standard library. It first tries MCP
Streamable HTTP (``/mcp``); if the extension only exposes legacy SSE (``/``),
it transparently falls back to the SSE transport. It maps Arcanum's
``burp_intercept`` / ``burp_repeater`` capabilities to the tools actually
exposed by the running extension. It never grants permissions itself —
handlers installed here are still executed only through the
AuthorizationGateway.
"""

from __future__ import annotations

import http.client
import json
import os
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .tool_registry import ToolDefinition, ToolRegistry

DEFAULT_BURP_MCP_URL = os.environ.get(
    "ARCANUM_BURP_MCP_URL", "http://127.0.0.1:9876/mcp"
)


class BurpUnavailable(RuntimeError):
    """Raised when the Burp MCP endpoint cannot be reached or lacks a tool."""


class BurpMCPClient:
    """MCP client with Streamable-HTTP and legacy-SSE fallback."""

    def __init__(self, url: str = DEFAULT_BURP_MCP_URL, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout
        self.session_id: str | None = None
        self._id = 0
        self._sse_endpoint: str | None = None
        self._sse_conn: http.client.HTTPConnection | None = None
        self._sse_response: http.client.HTTPResponse | None = None
        self._sse_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._sse_thread: threading.Thread | None = None

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    # ---------- Streamable HTTP (JSON response) ----------

    def _post_json(self, payload: dict[str, Any], expect_response: bool) -> dict[str, Any] | None:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        request = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                sid = response.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                content_type = response.headers.get("Content-Type", "")
                body = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # This Burp extension version exposes legacy SSE only.
                self._init_sse()
                return self._post_sse(payload, expect_response)
            raise BurpUnavailable(f"Burp MCP returned HTTP {exc.code} at {self.url}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BurpUnavailable(f"Cannot reach Burp MCP at {self.url}: {exc}") from exc

        if not expect_response:
            return None
        if "text/event-stream" in content_type:
            for line in body.splitlines():
                if line.startswith("data:"):
                    message = line[5:].strip()
                    if message:
                        return json.loads(message)
            raise BurpUnavailable("Burp MCP returned an empty event stream")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise BurpUnavailable(f"Burp MCP returned non-JSON response: {body[:200]}") from exc

    # ---------- Legacy SSE ----------

    def _init_sse(self) -> None:
        if self._sse_endpoint:
            return
        parsed = urllib.parse.urlparse(self.url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        try:
            conn = http.client.HTTPConnection(parsed.hostname, parsed.port or 80,
                                              timeout=self.timeout)
            conn.request("GET", "/", headers={"Accept": "text/event-stream"})
            response = conn.getresponse()
            if response.status != 200:
                raise BurpUnavailable(f"Burp MCP SSE returned HTTP {response.status}")
        except (OSError, TimeoutError) as exc:
            raise BurpUnavailable(f"Cannot reach Burp MCP SSE at {base}: {exc}") from exc

        endpoint: str | None = None
        while True:
            line = response.readline().decode("utf-8", errors="replace").strip()
            if not line:
                continue
            if line.startswith("data:") and endpoint is None:
                endpoint = line[5:].strip()
                break

        if not endpoint:
            raise BurpUnavailable("Burp MCP SSE did not publish a message endpoint")

        self._sse_endpoint = urllib.parse.urljoin(base + "/", endpoint)
        self._sse_conn = conn
        self._sse_response = response
        self._sse_thread = threading.Thread(target=self._read_sse, daemon=True)
        self._sse_thread.start()

    def _read_sse(self) -> None:
        response = self._sse_response
        if response is None:
            return
        try:
            while True:
                line = response.readline().decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                message = line[5:].strip()
                if not message:
                    continue
                try:
                    parsed = json.loads(message)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    self._sse_queue.put(parsed)
        except Exception:
            return

    def _post_sse(self, payload: dict[str, Any], expect_response: bool) -> dict[str, Any] | None:
        if not self._sse_endpoint:
            raise BurpUnavailable("Burp MCP SSE endpoint is not initialized")
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self._sse_endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout):
                pass
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BurpUnavailable(f"Burp MCP SSE POST failed: {exc}") from exc

        if not expect_response:
            return None

        wanted = payload.get("id")
        while True:
            try:
                message = self._sse_queue.get(timeout=self.timeout)
            except queue.Empty as exc:
                raise BurpUnavailable("Timed out waiting for Burp MCP SSE response") from exc
            if message.get("id") == wanted:
                return message

    def _post(self, payload: dict[str, Any], expect_response: bool = True) -> dict[str, Any] | None:
        return self._post_json(payload, expect_response)

    # ---------- MCP methods ----------

    def initialize(self) -> dict[str, Any]:
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "arcanum-gateway", "version": "0.1.0"},
            },
        })
        assert result is not None
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"},
                   expect_response=False)
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/list",
            "params": {},
        })
        assert result is not None
        if "error" in result:
            raise BurpUnavailable(f"tools/list failed: {result['error']}")
        return result.get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self._post({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        assert result is not None
        if "error" in result:
            raise BurpUnavailable(f"tools/call {name} failed: {result['error']}")
        return result.get("result", {})


class BurpToolAdapter:
    """Maps Arcanum proxy capabilities onto the live Burp MCP tool set."""

    def __init__(self, url: str = DEFAULT_BURP_MCP_URL):
        self.client = BurpMCPClient(url)
        self._tools: list[dict[str, Any]] | None = None
        self._repeater_tool: str | None = None
        self._intercept_tool: str | None = None

    @staticmethod
    def _pick_tool(tools: list[dict[str, Any]], keywords: tuple[str, ...]) -> str | None:
        names = [t.get("name", "") for t in tools]
        for keyword in keywords:
            for name in names:
                if keyword in name.lower():
                    return name
        return None

    def ensure_ready(self) -> None:
        if self._tools is not None:
            return
        self.client.initialize()
        self._tools = self.client.list_tools()
        self._repeater_tool = self._pick_tool(self._tools, ("repeater", "send_request"))
        self._intercept_tool = self._pick_tool(self._tools, ("intercept", "proxy_history"))

    def available_tools(self) -> list[str]:
        self.ensure_ready()
        return [t.get("name", "") for t in (self._tools or [])]

    def _call(self, tool: str | None, purpose: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        if tool is None:
            raise BurpUnavailable(
                f"No Burp MCP tool found for {purpose}. "
                f"Available tools: {self.available_tools()}"
            )
        result = self.client.call_tool(tool, arguments)
        return {
            "untrusted_data": {
                "source": "burp_mcp",
                "tool": tool,
                "result": result,
            }
        }

    def repeater(self, params: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        return self._call(self._repeater_tool, "burp_repeater", {
            "request": params.get("request", ""),
        })

    def intercept(self, params: dict[str, Any]) -> dict[str, Any]:
        self.ensure_ready()
        return self._call(self._intercept_tool, "burp_intercept", {
            "request": params.get("request", ""),
            "action": params.get("action", "hold"),
        })


def install_burp_adapter(
    registry: ToolRegistry,
    adapter: BurpToolAdapter | None = None,
) -> BurpToolAdapter:
    """Replace the mock Burp handlers with real MCP-backed handlers."""
    adapter = adapter or BurpToolAdapter()
    handlers = {
        "burp_repeater": adapter.repeater,
        "burp_intercept": adapter.intercept,
    }
    for capability, handler in handlers.items():
        definition: ToolDefinition | None = registry.get(capability)
        if definition is None:
            continue
        definition.mock_only = False
        registry.register(definition, handler)
    return adapter
