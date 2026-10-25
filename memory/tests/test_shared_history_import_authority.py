"""Consumer tests: a verified shared label survives TEHM canonical import."""
import copy
import hashlib
import json

import pytest

from tehm import evidence, neutral_export as NX
from tehm.canonical.transition import ObservationDelta, classify_outcome
from tehm.canonical.verifier import VerifierSnapshot


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fixture(tmp_path, outcome="admitted_positive", *, after=None, rollback=False):
    raw = {"check": "orfs_stage", "strategy": "util_trial", "from_stage": "floorplan",
           "fix_session_id": "trial", "iter": 1, "before": 2267, "after": after,
           "verdict": "cleared", "rolled_back": rollback,
           "config_before": {"CORE_UTILIZATION": "84"},
           "config_delta": '{"CORE_UTILIZATION":"45"}',
           "situation": {"check": "orfs_stage", "platform": "sky130hd",
                         "violation_class": "route", "error_code": "GRT-0116"}}
    lab = {"version": "shared-history-v2-labels", "admitted": outcome == "admitted_positive",
           "outcome_class": outcome, "signature": "route:GRT-0116",
           "binding": {"pre": "KNOWN", "post": "KNOWN"},
           "step_effect": {"stage_abort": "CLEARED"},
           "new_failures": {"stage": "KNOWN-present" if outcome == "cleared_new_failure" else "KNOWN-none"},
           "rolled_back": {"status": "UNKNOWN" if rollback is None else "KNOWN", "value": rollback},
           "edits": {"CORE_UTILIZATION": "45"}}
    if outcome == "harm":
        lab["step_effect"] = {"stage_abort": "REGRESSED"}
    if outcome == "no_change":
        lab["step_effect"] = {"stage_abort": "NO_CHANGE"}
    if outcome == "improved":
        lab["step_effect"] = {"stage_abort": "IMPROVED"}
    row_file = tmp_path / "raw.json"
    row_file.write_text(json.dumps(raw))
    unit = {"unit_id": "verified-unit", "identity": {"design": "learning_design", "platform": "sky130hd", "session": "trial"},
            "capture_context": {"prev_cumulative": None, "step_index": 0, "is_terminal": True,
                                "session_verdicts": ["cleared"]}, "raw_row": raw,
            "source_eligible": True, "provenance": {"sources": [{"path": str(row_file), "line": 1}]},
            "shared_label": lab}
    report = tmp_path / "verify.json"
    report.write_text(json.dumps({"gate": "PASS", "critical": 0, "records": 1}))
    auth = tmp_path / "authority.json"
    auth.write_text(json.dumps({"schema": "tehm-shared-import-authority-v1",
        "units": {unit["unit_id"]: digest(unit)},
        "files": {str(row_file): sha(row_file), str(report): sha(report)},
        "verifier_receipts": [str(report)]}))
    unit["shared_evidence_proof"] = {"authority": str(auth), "authority_sha256": sha(auth)}
    return unit


def cls(record):
    out = classify_outcome(ObservationDelta.from_dict(record.observation_delta),
                           VerifierSnapshot.from_dict(record.verification))
    return evidence.classify(out, record.verification, record.observation_delta)


def test_admitted_success_with_null_raw_count_survives(tmp_path):
    u = fixture(tmp_path)
    before = copy.deepcopy(u)
    rec = NX.unit_record(u)
    assert rec.verification["oracle_complete"] is True
    assert cls(rec) == "positive"
    assert rec.action["payload"]["config_edits"] == {"CORE_UTILIZATION": "45"}
    assert rec.observation_delta["first_divergence"]["after"] is None
    assert u == before                         # never rewrite the raw measurement
    assert rec.verification["input_binding"]["shared_history"]["unit_id"] == u["unit_id"]


@pytest.mark.parametrize("outcome,expected", [
    ("cleared_new_failure", "no_evidence"), ("improved", "no_evidence"),
    ("tool_no_op", "no_evidence"), (None, "no_evidence"),
    ("harm", "harmful"), ("no_change", "no_clear")])
def test_nonpositive_labels_do_not_turn_into_legacy_positives(tmp_path, outcome, expected):
    u = fixture(tmp_path, outcome, after=0)
    rec = NX.unit_record(u)
    assert cls(rec) == expected
    assert rec.verification["input_binding"]["shared_history"]["outcome_class"] == outcome


def test_rollback_unknown_never_becomes_verified_parameter_support(tmp_path):
    rec = NX.unit_record(fixture(tmp_path, "cleared_unconfirmed", after=0, rollback=None))
    assert cls(rec) == "rollback_unknown"


def test_rolled_back_clear_stays_nonpositive(tmp_path):
    rec = NX.unit_record(fixture(tmp_path, None, after=0, rollback=True))
    assert cls(rec) != "positive"


def test_shared_label_without_verified_authority_is_refused(tmp_path):
    u = fixture(tmp_path)
    u.pop("shared_evidence_proof")
    with pytest.raises(ValueError, match="authority|proof"):
        NX.unit_record(u)


def test_tampering_a_label_is_refused(tmp_path):
    u = fixture(tmp_path, "cleared_new_failure", after=0)
    u["shared_label"]["admitted"] = True
    with pytest.raises(ValueError):
        NX.unit_record(u)


def test_changed_evidence_file_is_refused(tmp_path):
    u = fixture(tmp_path)
    (tmp_path / "raw.json").write_text("changed")
    with pytest.raises(ValueError, match="changed|hash"):
        NX.unit_record(u)


def test_changed_verifier_receipt_is_refused(tmp_path):
    u = fixture(tmp_path)
    (tmp_path / "verify.json").write_text('{"gate":"FAIL","critical":1}')
    with pytest.raises(ValueError):
        NX.unit_record(u)


def test_legacy_record_without_shared_label_is_unchanged(tmp_path):
    u = fixture(tmp_path)
    del u["shared_label"], u["shared_evidence_proof"]
    rec = NX.unit_record(u)
    assert rec.verification["oracle_complete"] is False
    assert rec.verification.get("input_binding") is None


@pytest.mark.parametrize("field,value", [("oracle_complete", False), ("verdict", "UNKNOWN")])
def test_incomplete_persisted_shared_positive_is_refused(tmp_path, field, value):
    rec = NX.unit_record(fixture(tmp_path))
    rec.verification[field] = value
    with pytest.raises(ValueError):
        cls(rec)


def test_unknown_shared_schema_cannot_fall_back_to_legacy_positive(tmp_path):
    rec = NX.unit_record(fixture(tmp_path))
    rec.verification["input_binding"]["shared_history"]["schema"] = "unsupported"
    with pytest.raises(ValueError):
        cls(rec)
