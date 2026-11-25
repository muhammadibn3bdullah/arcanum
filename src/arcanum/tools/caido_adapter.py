"""Real Caido adapter over the local GraphQL API.

This replaces the mock Caido handlers with a client that reads already-captured
traffic from Caido at http://127.0.0.1:8080/graphql by default. It is pass-only:
it never injects traffic and never grants permissions. Handlers still execute
through the AuthorizationGateway.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .tool_registry import ToolDefinition, ToolRegistry

DEFAULT_CAIDO_URL = os.environ.get("CAIDO_URL", "http://127.0.0.1:8080/graphql")

TRAFFIC_QUERY = """
query FingerprintTraffic($limit: Int, $offset: Int, $filter: HTTPQLInput) {
  requestsByOffset(limit: $limit, offset: $offset, filter: $filter) {
    nodes {
      id
      host
      method
      path
      query
      port
      isTls
      createdAt
      raw
      response {
        id
        statusCode
        length
        roundtripTime
        raw
      }
    }
  }
}
""".strip()


class CaidoUnavailable(RuntimeError):
    """Raised when Caido GraphQL cannot be reached or returns errors."""


class CaidoGraphQLClient:
    def __init__(self, endpoint: str = DEFAULT_CAIDO_URL, timeout: float = 15.0):
        self.endpoint = endpoint
        self.timeout = timeout
        self.token = (
            os.environ.get("CAIDO_TOKEN")
            or os.environ.get("CAIDO_ACCESS_TOKEN")
            or self._token_from_file()
        )

    @staticmethod
    def _token_from_file() -> str | None:
        """Find the current Caido token in engagement folders (headless-friendly)."""
        explicit = os.environ.get("CAIDO_TOKEN_FILE")
        candidates: list[Path] = []
        if explicit:
            candidates.append(Path(explicit).expanduser())
        else:
            candidates.extend(
                Path.home().glob("Targets/*/.maestro/playbooks/Working/caido_token.txt")
            )
        usable = [p for p in candidates if p.is_file()]
        if not usable:
            return None
        newest = max(usable, key=lambda p: p.stat().st_mtime)
        try:
            return newest.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def query(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(
            self.endpoint, data=payload, headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CaidoUnavailable(f"Cannot reach Caido GraphQL at {self.endpoint}: {exc}") from exc

        try:
            result = json.loads(body)
        except json.JSONDecodeError as exc:
            raise CaidoUnavailable(f"Caido returned non-JSON response: {body[:200]}") from exc
        if "errors" in result:
            raise CaidoUnavailable(f"Caido GraphQL error: {result['errors']}")
        return result

    def status(self) -> dict[str, Any]:
        return self.query("query { __typename }")

    def requests_by_host(
        self, host: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        httpql = f'req.host.cont:"{host}"'
        result = self.query(TRAFFIC_QUERY, {
            "limit": limit,
            "offset": offset,
            "filter": {"code": httpql},
        })
        data = result.get("data", {}).get("requestsByOffset", {})
        return data.get("nodes", []) or []


class CaidoToolAdapter:
    def __init__(self, endpoint: str = DEFAULT_CAIDO_URL):
        self.client = CaidoGraphQLClient(endpoint)

    def _read(self, params: dict[str, Any], purpose: str) -> dict[str, Any]:
        host = str(params.get("host", "")).strip()
        if not host:
            raise CaidoUnavailable("A host is required to query captured Caido traffic")
        limit = int(params.get("limit", 50))
        offset = int(params.get("offset", 0))
        nodes = self.client.requests_by_host(host, limit=limit, offset=offset)
        summary = [
            {
                "id": n.get("id"),
                "host": n.get("host"),
                "method": n.get("method"),
                "path": n.get("path"),
                "status_code": (n.get("response") or {}).get("statusCode"),
            }
            for n in nodes
        ]
        return {
            "untrusted_data": {
                "source": "caido_graphql",
                "purpose": purpose,
                "host": host,
                "count": len(summary),
                "requests": summary,
            }
        }

    def read(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._read(params, "caido_read")

    def traffic_query(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._read(params, "caido_traffic_query")


def install_caido_adapter(
    registry: ToolRegistry,
    adapter: CaidoToolAdapter | None = None,
) -> CaidoToolAdapter:
    """Replace mock Caido handlers with real GraphQL-backed handlers."""
    adapter = adapter or CaidoToolAdapter()
    handlers = {
        "caido_read": adapter.read,
        "caido_traffic_query": adapter.traffic_query,
    }
    for capability, handler in handlers.items():
        definition: ToolDefinition | None = registry.get(capability)
        if definition is None:
            continue
        definition.mock_only = False
        registry.register(definition, handler)
    return adapter
