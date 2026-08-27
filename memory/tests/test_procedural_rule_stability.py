from pathlib import Path

import pytest

from scripts.run_procedural_rule_stability import (
    _rule_signature,
    _validate_work_root,
)


def test_rule_stability_rejects_non_tmp_work_root():
    with pytest.raises(ValueError, match="under /tmp"):
        _validate_work_root(Path("/data1/zhangdy/rule-stability"))


def test_rule_signature_excludes_volatile_support_fields():
    base = {
        "domain": "rtl",
        "action_domain": "rtl.GUARD_STRENGTHEN",
        "transformation_family": "GUARD_STRENGTHEN",
        "before_pattern": {"state": "$SRC"},
        "after_pattern": {"state": "$DST"},
        "hard_preconditions": {"role": "ready"},
        "context_predicates": {"check": "rtl"},
        "obligations": ["TARGET_PASS"],
        "predicate_schema_version": "v1",
        "role_schema_version": "v1",
        "crystallizer_version": "v1",
        "rule_id": "volatile-a",
        "validity_status": "VALIDATED",
        "created_at": "now-a",
    }
    changed = dict(base, rule_id="volatile-b", created_at="now-b",
                   validity_status="CANDIDATE")
    assert _rule_signature(base) == _rule_signature(changed)


def test_rule_signature_changes_when_executable_pattern_changes():
    base = {
        "domain": "rtl", "action_domain": "rtl.GUARD_STRENGTHEN",
        "transformation_family": "GUARD_STRENGTHEN",
        "before_pattern": {"state": "$SRC"},
        "after_pattern": {"state": "$DST"},
        "hard_preconditions": {}, "context_predicates": {},
        "obligations": ["TARGET_PASS"],
        "predicate_schema_version": "v1", "role_schema_version": "v1",
        "crystallizer_version": "v1",
    }
    changed = dict(base, after_pattern={"state": "$OTHER"})
    assert _rule_signature(base) != _rule_signature(changed)
