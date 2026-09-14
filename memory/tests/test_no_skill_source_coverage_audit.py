"""Public source audit conformance; request declarations are not authority."""
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from tehm.ids import stable_dumps
from tehm.state.schema import ensure_state_schema

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_no_skill_source_coverage.py"
spec = importlib.util.spec_from_file_location("coverage_auditor", SCRIPT)
auditor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auditor)


@pytest.fixture
def audit_inputs(tmp_tehm, tmp_path):
    conn, _, _ = tmp_tehm
    ensure_state_schema(conn)
    source = tmp_path / "source.sqlite"
    saved = sqlite3.connect(source); conn.backup(saved); saved.close()
    path = tmp_path / "request.json"
    payload = {"campaign_id": "unit-audit", "cases": [{"case_id": "unit-case",
        "split": "calibration", "learner_eligible": False, "query": {"query_plan": {
            "mechanism_family": "HANDSHAKE_COMPLETION", "compatibility_profile": None,
            "target_scope": "global"}}}]}
    path.write_text(json.dumps(payload))
    return source, path, tmp_path / "new-report.json", payload


def test_public_audit_retains_source_and_distinguishes_diagnostic_from_label(audit_inputs):
    source, path, output, _ = audit_inputs
    before = source.read_bytes()
    report = auditor.audit(source, path, output=output)
    assert source.read_bytes() == before
    assert report["scope"] == "COVERAGE_ONLY_NOT_PAIRED_LABEL"
    assert report["paired_execution_bound"] is False
    assert report["request_membership_independently_verified"] is False
    assert report["calibration_labels_emitted"] == report["new_independent_calibration_samples"] == 0
    assert report["cases"]["unit-case"]["replay"]["absence_established"] is True
    payload = json.loads(output.read_text()); digest = payload.pop("report_digest")
    assert digest == "sha256:" + hashlib.sha256(stable_dumps(payload).encode()).hexdigest()
    with pytest.raises(ValueError, match="new"):
        auditor.audit(source, path, output=output)


@pytest.mark.parametrize("kind", ["learner", "partition", "duplicate"])
def test_public_request_cannot_silently_repartition_or_repeat_cases(audit_inputs, kind):
    source, path, output, payload = audit_inputs
    if kind == "learner": payload["cases"][0]["learner_eligible"] = True
    elif kind == "partition": payload["cases"][0]["split"] = "training"
    else: payload["cases"] *= 2
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        auditor.audit(source, path, output=output)
    assert not output.exists()
