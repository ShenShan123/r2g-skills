"""Standard receipt wiring with synthetic oracle output, not EDA evidence."""
import importlib.util
from pathlib import Path

import pytest

from tehm.capability import build_candidate_lineage
from test_candidate_lineage import _bundle


@pytest.fixture
def execute(monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location("smoke_receipts", scripts / "run_flow_binding_smoke.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.execute_smoke_arm


@pytest.mark.parametrize("memory", [False, True])
def test_one_live_call_creates_matching_raw_and_standard_receipts(execute, memory):
    candidate, routing, selection, binding, _ = _bundle()
    calls = []

    def oracle(proposal, case, budget):
        calls.append(proposal)
        return {"outcome": "PASS", "compile_result": "PASS", "functional_result": "PASS",
                "signoff_result": "UNKNOWN", "metadata": {"scope": "route"}}

    raw, receipt = execute(candidate if memory else None,
        {"case_id": "receipt-unit", "toolchain_digest": "sha256:tool", "oracle_digest": "sha256:oracle"},
        oracle=oracle)
    assert len(calls) == 1
    assert receipt.outcome == raw["outcome"]
    assert receipt.signoff_result == "UNKNOWN"
    assert receipt.source == ("structured_memory" if memory else "no_memory")
    if memory:
        lineage = build_candidate_lineage(candidate=candidate, routing=routing,
            asset_selection=selection, runtime_binding=binding, execution=receipt)
        assert lineage.eligible
        assert lineage.execution_receipt_digest == receipt.execution_digest


def test_oracle_exception_cannot_become_successful_smoke(execute):
    candidate, *_ = _bundle()

    def broken(*args):
        raise RuntimeError("tool unavailable")

    raw, receipt = execute(candidate, {"case_id": "error"}, oracle=broken)
    assert raw == {}
    assert receipt.outcome == "UNKNOWN"
    assert receipt.metadata["oracle_error"] == "RuntimeError"
