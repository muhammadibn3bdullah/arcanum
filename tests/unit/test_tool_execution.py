import json

import pytest

from arcanum.permissions.authorization_gateway import AuthorizationGateway
from arcanum.permissions.permission_audit import PermissionAuditLog
from arcanum.permissions.permission_manager import DynamicPermissionManager, PermissionStore
from arcanum.permissions.permission_models import (
    DEFAULT_PROFILES,
    PermissionDecision,
    PermissionRequest,
)
from arcanum.tools import (
    GatewayMCPServer,
    GatewayToolExecutor,
    MockBurpProxy,
    MockCaidoTrafficStore,
    build_mock_registry,
)
from arcanum.core.models import ToolOperation


def make_stack(tmp_path, scope_ok=True):
    dpm = DynamicPermissionManager(
        store=PermissionStore(tmp_path / "grants.db"),
        audit=PermissionAuditLog(tmp_path / "audit.db"),
        scope_verifier=lambda target, operation="": scope_ok,
    )
    gateway = AuthorizationGateway(dpm)
    caido = MockCaidoTrafficStore()
    burp = MockBurpProxy()
    registry = build_mock_registry(gateway, caido, burp)
    executor = GatewayToolExecutor(dpm, registry)
    mcp = GatewayMCPServer(registry)
    return dpm, gateway, registry, executor, mcp, caido, burp


def make_operation(capability="http_read", risk="LOW", task_type="general", **extra):
    return ToolOperation(
        request_id="op_1",
        agent_id="recon_agent",
        task_id="task_1",
        task_type=task_type,
        capability=capability,
        target="lab_app",
        operation="read",
        justification="test",
        risk=risk,
        **extra,
    )


def test_registry_covers_all_profile_capabilities(tmp_path):
    dpm, gateway, registry, *_ = make_stack(tmp_path)
    expected = {c for p in DEFAULT_PROFILES.values() for c in p.max_capabilities}
    names = {t.name for t in registry.list_tools()}
    assert expected <= names
    assert all(registry.get(n).mock_only for n in names)


def test_mcp_schema_requires_grant_id_and_rejects_extra_fields(tmp_path):
    dpm, gateway, registry, executor, mcp, *_ = make_stack(tmp_path)
    schemas = {s["name"]: s for s in mcp.handle_request({"id": 1, "method": "tools/list"})["result"]["tools"]}
    schema = schemas["caido_read"]["inputSchema"]
    assert "grant_id" in schema["required"]
    assert schema["additionalProperties"] is False


def test_mcp_has_no_grant_issuing_method(tmp_path):
    dpm, gateway, registry, executor, mcp, *_ = make_stack(tmp_path)
    response = mcp.handle_request({"id": 2, "method": "request_permission", "params": {}})
    assert "error" in response
    assert "Method not found" in response["error"]["message"]


def test_mcp_rejects_authorization_arguments(tmp_path):
    dpm, gateway, registry, executor, mcp, *_ = make_stack(tmp_path)
    response = mcp.handle_request({
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "http_read",
            "arguments": {
                "grant_id": "grant_fake", "agent_id": "recon_agent",
                "task_id": "task_1", "target": "lab_app", "operation": "read",
                "risk": "CRITICAL",
            },
        },
    })
    assert "error" in response
    assert "Disallowed tool arguments" in response["error"]["message"]


def test_mcp_requires_grant_id(tmp_path):
    dpm, gateway, registry, executor, mcp, *_ = make_stack(tmp_path)
    response = mcp.handle_request({
        "id": 4,
        "method": "tools/call",
        "params": {"name": "http_read", "arguments": {}},
    })
    assert "grant_id is required" in response["error"]["message"]


def test_caido_mock_executes_with_dpm_grant(tmp_path):
    dpm, gateway, registry, executor, mcp, caido, burp = make_stack(tmp_path)
    operation = make_operation(capability="caido_read", task_type="passive_recon")
    result = executor.request_and_execute(
        operation, task_authorized=True, params={"host": "lab.example.test"}
    )
    assert result.decision == "ALLOW"
    assert result.allowed is True
    assert result.result["untrusted_data"]["source"] == "mock_caido_store"
    assert result.result["untrusted_data"]["count"] == 2


def test_agent_supplied_grant_id_is_ignored(tmp_path):
    dpm, gateway, registry, executor, mcp, *_ = make_stack(tmp_path)
    operation = make_operation(grant_id="grant_forged_by_agent")
    result = executor.request_and_execute(operation, task_authorized=True)
    assert result.decision == "ALLOW"
    assert result.grant_id != "grant_forged_by_agent"
    assert result.grant_id.startswith("grant_")
    assert result.allowed is True


def test_mcp_call_with_valid_grant(tmp_path):
    dpm, gateway, registry, executor, mcp, *_ = make_stack(tmp_path)
    # Issue a fresh, unused grant directly from the DPM.
    request = PermissionRequest(
        request_id="req_mcp", agent_id="recon_agent", task_id="task_1",
        requested_capability="caido_read", target="lab_app", operation="read",
        justification="test", task_type="passive_recon",
    )
    grant = dpm.request_permission(request, task_authorized=True).grant
    response = mcp.handle_request({
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "caido_read",
            "arguments": {
                "grant_id": grant.grant_id,
                "agent_id": "recon_agent",
                "task_id": "task_1",
                "target": "lab_app",
                "operation": "read",
                "params": {"host": "lab.example.test"},
            },
        },
    })
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["allowed"] is True
    assert payload["untrusted_result"]["untrusted_data"]["source"] == "mock_caido_store"


def test_active_burp_tool_requires_human_approval(tmp_path):
    dpm, gateway, registry, executor, mcp, *_ = make_stack(tmp_path)
    operation = make_operation(
        capability="burp_intercept", risk="MEDIUM", task_type="active_web_test"
    )
    operation.agent_id = "web_security_agent"
    result = executor.request_and_execute(operation, task_authorized=True)
    assert result.decision == PermissionDecision.REQUIRE_HUMAN_APPROVAL.value
    assert result.allowed is False
    assert result.result is None
    assert result.requires_human_approval is True


def test_mcp_call_with_consumed_grant_is_denied(tmp_path):
    dpm, gateway, registry, executor, mcp, *_ = make_stack(tmp_path)
    operation = make_operation()
    first = executor.request_and_execute(operation, task_authorized=True)
    response = mcp.handle_request({
        "id": 6,
        "method": "tools/call",
        "params": {
            "name": "http_read",
            "arguments": {
                "grant_id": first.grant_id,
                "agent_id": "recon_agent",
                "task_id": "task_1",
                "target": "lab_app",
                "operation": "read",
            },
        },
    })
    assert response["result"]["isError"] is True
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["allowed"] is False
