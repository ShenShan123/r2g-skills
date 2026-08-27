from pathlib import Path

import pytest

from scripts.run_procedural_rule_growth import _profile, _validate_work_root


def test_rule_growth_rejects_non_tmp_work_root():
    with pytest.raises(ValueError, match="under /tmp"):
        _validate_work_root(Path("/data1/zhangdy/rule-growth"))


def test_rule_growth_profile_keeps_v3_support_fields():
    row = _profile({
        "rule_id": "rule_x",
        "domain": "rtl",
        "validity_status": "VALIDATED",
        "validity_profile": {
            "gates": [
                {"name": "V2", "ok": True, "detail": {"num_sources": 4}},
                {"name": "V1", "ok": True, "detail": {}},
                {"name": "V3", "ok": True, "detail": {
                    "cross_lineage": True, "unique_attempts": 4,
                    "unique_lineages": 3, "unique_families": 1,
                }},
                {"name": "V4", "ok": True, "detail": {}},
            ]
        },
    })
    assert row["v2_v4_valid"] is True
    assert row["cross_lineage"] is True
    assert row["unique_lineages"] == 3
