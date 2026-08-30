"""JSON-schema contract for legacy and strict prospective manifests."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


SCHEMA_PATH = (Path(__file__).resolve().parents[1] /
               "tehm/schemas/parametric_prospective_manifest_v1.json")


def _base() -> dict:
    return {
        "version": "parametric-prospective-manifest-v1",
        "status": "PLANNED",
        "source_freeze": {"bundle_digest": "bundle", "manifest_digest": "manifest"},
        "firewall": {"training_lineages": [], "calibration_lineages": [],
                      "heldout_lineages": [], "ab_lineages": []},
        "cases": [{"case_id": "future:a:observation"}],
        "pre_registered_metrics": {"hard_ood_ceiling": 3.0,
                                    "min_interval_coverage": 0.8,
                                    "max_harmful_rate": 0.0,
                                    "min_obligation_coverage": 1.0},
    }


def test_schema_accepts_legacy_manifest_without_contract_fields():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.Draft202012Validator(schema).validate(_base())


def test_schema_requires_typed_contract_only_for_strict_manifest():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text())
    strict = {**_base(), "require_utility_contract": True}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(schema).validate(strict)

    strict.update({
        "contract_id": "DENSITY_RELIEF_NONREGRESSION_32",
        "utility_contract_digest": "0" * 64,
        "action_signature": {
            "domain": "flow.CONFIG_DELTA", "family": "DENSITY_RELIEF",
            "config_edits": {"CORE_UTILIZATION": "32"},
            "operation_point": "base<32->32",
        },
        "manifest_digest": "1" * 64,
        "target_groups": {"observation:target": 1},
        "validation": {"no_canonical_memory_mutation": True},
    })
    jsonschema.Draft202012Validator(schema).validate(strict)
