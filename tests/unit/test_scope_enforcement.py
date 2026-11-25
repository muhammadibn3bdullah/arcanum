from pathlib import Path

import pytest

from arcanum.config.settings import Settings
from arcanum.memory.database import Database
from arcanum.security.scope_manager import ScopeManager
from arcanum.core.models import ScopeRef


def make(tmp_path, mode="operator_declared", allowlist=None, operators=None):
    s = Settings(
        base_dir=tmp_path,
        db_path=tmp_path / "db.sqlite",
        targets_dir=tmp_path / "Targets",
        dynamic_target_mode=mode,
        allowed_operators=operators or ["op"],
        target_allowlist=allowlist or [],
    )
    db = Database(s.db_path)
    return s, db, ScopeManager(db, s)


def test_operator_declared_registers_any_domain(tmp_path):
    s, db, sm = make(tmp_path)
    refs = sm.register_targets("Assess example.com", "op")
    assert len(refs) == 1
    assert refs[0].domain == "example.com"
    assert refs[0].mode == "operator_declared"
    assert sm.get(refs[0].scope_ref) is not None


def test_non_allowlisted_operator_denied(tmp_path):
    s, db, sm = make(tmp_path)
    with pytest.raises(PermissionError):
        sm.register_targets("Assess example.com", "attacker")
    # audit recorded the denial
    rows = db.conn.execute("SELECT event_type, target FROM audit_log").fetchall()
    assert any(r[0] == "scope_declaration_denied" for r in rows)


def test_allowlist_mode_rejects_unlisted_domain(tmp_path):
    s, db, sm = make(tmp_path, mode="TARGET_ALLOWLIST_ONLY", allowlist=["allowed.test"])
    with pytest.raises(PermissionError):
        sm.register_targets("Assess not-allowed.test", "op")
    refs = sm.register_targets("Assess allowed.test", "op")
    assert len(refs) == 1


def test_extract_domains_dedupes_and_normalizes(tmp_path):
    s, db, sm = make(tmp_path)
    domains = sm.extract_domains("Visit https://www.Example.com/path and example.com again")
    assert domains == ["example.com"]


def test_validate_scope(tmp_path):
    s, db, sm = make(tmp_path)
    refs = sm.register_targets("Assess example.com", "op")
    assert sm.validate([r.scope_ref for r in refs]) is True
    assert sm.validate(["nonexistent"]) is False


def test_audit_records_declaration(tmp_path):
    s, db, sm = make(tmp_path)
    refs = sm.register_targets("Assess example.com", "op")
    rows = db.conn.execute("SELECT event_type, target FROM audit_log").fetchall()
    assert any(r[0] == "scope_declared" and r[1] == "example.com" for r in rows)
    assert db.get_scope(refs[0].scope_ref) is not None