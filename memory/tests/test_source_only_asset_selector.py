"""Selector/construction/execution conformance, not campaign authority evidence.

Router claims are mocked here deliberately. Real L3 and promotion admission
must be audited separately against the controlled campaign receipts.
"""
import copy
from dataclasses import replace
from pathlib import Path

import pytest

from contracts import MemoryQuery
from tehm.assets import register_asset, set_asset_status
from tehm.assets.guard_binding import PROFILE, DOMAIN
from tehm.assets.source_selection import verify_candidate_source_replay
from tehm.retrieval.asset_selector import select_knowledge_grounded_assets, AssetSelectorError
from tehm.retrieval.structured_candidate import build_structured_candidate, StructuredCandidateError
from tehm.evaluation.rtl_candidate_oracle import execute_rtl_candidate, RtlCandidateOracleError
from tehm.rtl.rtl_oracle import IcarusOracle

from test_guard_semantic_binding import SOURCE, _asset
from test_asset_selector import _claim, _routing, _patch_shadow_state


def _setup(conn, monkeypatch, *, linked=True):
    raw = _asset()
    raw["provenance"]["mechanism_knowledge_ids"] = ["mk_selector@1"] if linked else []
    receipt = register_asset(conn, **{key: raw[key] for key in (
        "asset_type", "name", "version", "definition", "input_contract",
        "output_contract", "verifier_contract", "compatibility", "provenance")})
    for status in ("shadow", "candidate"):
        set_asset_status(conn, asset_id=receipt.asset_id, target_scope=receipt.target_scope, status=status)
    claim = replace(_claim(), compatibility_profile=PROFILE, positive_applicability=({
        "mechanism_family": "HANDSHAKE_COMPLETION", "compatibility_profile": PROFILE},))
    _patch_shadow_state(monkeypatch, receipt.asset_id, claim)
    query = MemoryQuery(query_plan={"mechanism_family": "HANDSHAKE_COMPLETION",
                                   "compatibility_profile": PROFILE})
    return query, _routing(receipt.asset_id)


def _select(conn, query, routing, source=SOURCE):
    return select_knowledge_grounded_assets(conn, query, routing=routing,
        rtl_source_text=source, design_id="source-only-target")


def _candidate(conn, monkeypatch):
    query, routing = _setup(conn, monkeypatch)
    selection = _select(conn, query, routing)
    assert selection.receipt.decision == "SELECT", selection.receipt.abstain_reasons
    return build_structured_candidate(query, routing, selection, selection.metadata["runtime_binding"])


def test_strict_source_selection_builds_candidate_without_reading_files(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    query, routing = _setup(conn, monkeypatch)
    def reject(*args, **kwargs):
        raise AssertionError("selection must not read manifests, answers or testbenches")
    monkeypatch.setattr(Path, "read_text", reject)
    selection = _select(conn, query, routing)
    assert selection.receipt.binding["compatibility_mode"] is False
    candidate = build_structured_candidate(query, routing, selection, selection.metadata["runtime_binding"])
    assert candidate.concrete_action["domain"] == DOMAIN
    assert candidate.concrete_action["payload"]["add_condition"] == "grant"
    assert candidate.provenance["source_binding_required"] is True
    assert verify_candidate_source_replay(candidate, SOURCE)
    assert candidate.evaluation_only is True


def test_non_alpha_target_uses_source_roles_not_training_coordinates(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    query, routing = _setup(conn, monkeypatch)
    target = SOURCE.replace("[15:0] value", "[31:0] value").replace("value <= data", "value <= value + data")
    target = target.replace("WAIT", "RECEIVE").replace("grant", "ready")
    selection = _select(conn, query, routing, target)
    assert selection.receipt.decision == "SELECT", selection.receipt.abstain_reasons
    candidate = build_structured_candidate(query, routing, selection, selection.metadata["runtime_binding"])
    assert candidate.concrete_action["payload"]["source_state"] == "RECEIVE"
    assert candidate.concrete_action["payload"]["add_condition"] == "ready"
    assert verify_candidate_source_replay(candidate, target)
    assert not verify_candidate_source_replay(candidate, SOURCE)


def test_missing_source_context_abstains(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    query, routing = _setup(conn, monkeypatch)
    selection = select_knowledge_grounded_assets(conn, query, routing=routing)
    assert selection.receipt.decision == "ABSTAIN"
    assert any("source_binding_context_missing" in x for x in selection.receipt.abstain_reasons)


@pytest.mark.parametrize("source,design", [(SOURCE, None), (None, "design"), (SOURCE, ""), (123, "design")])
def test_partial_source_context_rejected(tmp_tehm, source, design):
    with pytest.raises(AssetSelectorError):
        select_knowledge_grounded_assets(tmp_tehm[0], None, rtl_source_text=source, design_id=design)


@pytest.mark.parametrize("old,new", [("send && grant", "send || grant"),
    ("if(send)", "if(send && grant)"), ("posedge clk", "negedge clk")])
def test_unproved_source_abstains(tmp_tehm, monkeypatch, old, new):
    conn, _, _ = tmp_tehm
    query, routing = _setup(conn, monkeypatch)
    selection = _select(conn, query, routing, SOURCE.replace(old, new))
    assert selection.receipt.decision == "ABSTAIN"


def test_source_proof_does_not_create_knowledge_link(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    query, routing = _setup(conn, monkeypatch, linked=False)
    assert _select(conn, query, routing).receipt.decision == "ABSTAIN"


@pytest.mark.parametrize("field", ["selected_binding", "binding_digest", "target_design", "structural_evidence"])
def test_candidate_rejects_modified_runtime_receipt(tmp_tehm, monkeypatch, field):
    conn, _, _ = tmp_tehm
    query, routing = _setup(conn, monkeypatch)
    selection = _select(conn, query, routing)
    binding = copy.deepcopy(selection.metadata["runtime_binding"])
    binding[field] = {"add_condition": "send"} if field == "selected_binding" else "tampered"
    with pytest.raises(StructuredCandidateError, match="source binding differs"):
        build_structured_candidate(query, routing, selection, binding)


@pytest.mark.parametrize("tamper", ["action", "source", "registry", "id", "strip", "strip_all"])
def test_execution_replay_cannot_be_downgraded(tmp_tehm, monkeypatch, tamper):
    candidate = copy.deepcopy(_candidate(tmp_tehm[0], monkeypatch))
    if tamper == "action":
        candidate.concrete_action["payload"]["add_condition"] = "send"
    elif tamper == "source":
        candidate.provenance["source_binding_replay"]["bound_asset"]["provenance"]["binding_evidence"]["source"] = "bad source"
    elif tamper == "registry":
        candidate.provenance["source_binding_replay"]["registered_asset"]["name"] = "forged"
    elif tamper == "id":
        candidate.provenance["source_binding_replay"]["registered_asset"]["asset_id"] = "forged"
    else:
        candidate.provenance.pop("source_binding_replay")
        if tamper == "strip_all":
            candidate.provenance.pop("source_binding_required")
    assert not verify_candidate_source_replay(candidate, SOURCE)


def test_icarus_rejects_changed_actual_source_before_simulation(tmp_tehm, monkeypatch, tmp_path):
    candidate = _candidate(tmp_tehm[0], monkeypatch)
    source = tmp_path / "source.v"
    source.write_text(SOURCE.replace("send && grant", "send || grant"))
    tb = tmp_path / "tb.v"
    tb.write_text("module tb; endmodule")
    class SpyOracle(IcarusOracle):
        def verify(self, *args, **kwargs):
            raise AssertionError("unproved binding must not reach simulation")
    with pytest.raises(RtlCandidateOracleError, match="before execution"):
        execute_rtl_candidate(candidate, {"rtl_source": str(source), "target_test": str(tb),
            "frozen_regression": str(tb), "toolchain_digest": "pinned", "oracle_digest": "pinned"},
            1, oracle=SpyOracle())


def test_backend_forwards_explicit_source_context_without_production_mode(tmp_tehm, monkeypatch):
    from tehm_backend import TehmMemoryBackend
    import tehm.retrieval.asset_selector as selector
    conn, store, tmp_path = tmp_tehm
    backend = TehmMemoryBackend(db_path=tmp_path / "unused.sqlite", artifact_root=tmp_path / "unused-artifacts")
    monkeypatch.setattr(backend, "_open", lambda: (conn, store))
    captured = {}
    def capture(connection, query, **kwargs):
        assert connection is conn
        captured.update(kwargs)
        return "advisory-only"
    monkeypatch.setattr(selector, "select_knowledge_grounded_assets", capture)
    assert backend.select_assets(None, rtl_source_text=SOURCE, design_id="explicit") == "advisory-only"
    assert captured["rtl_source_text"] == SOURCE
    assert captured["design_id"] == "explicit"
    assert captured["mode"] == "shadow"
    assert captured["compatibility_mode"] is False


@pytest.mark.parametrize("decision,budget", [("NO_SKILL", 0), ("CONSIDER", 0), ("APPLY", 0)])
def test_source_binding_never_overrides_zero_memory_authorization(tmp_tehm, monkeypatch, decision, budget):
    conn, _, _ = tmp_tehm
    query, routing = _setup(conn, monkeypatch)
    routing = replace(routing, decision=decision, memory_budget=budget,
                      selected_asset_ids=() if decision == "NO_SKILL" else routing.selected_asset_ids)
    assert _select(conn, query, routing).receipt.decision == "NO_SKILL"


def test_no_skill_cannot_allocate_memory_even_before_selection():
    with pytest.raises(ValueError, match="cannot allocate"):
        replace(_routing("fixture-asset"), decision="NO_SKILL", memory_budget=1)


def test_candidate_rejects_removed_template_in_selected_asset(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    query, routing = _setup(conn, monkeypatch)
    selection = _select(conn, query, routing)
    selection.assets[0]["definition"].pop("binding_template")
    with pytest.raises(StructuredCandidateError, match="template missing"):
        build_structured_candidate(query, routing, selection, selection.metadata["runtime_binding"])


def test_actual_source_bound_candidate_runs_icarus_without_mutating_source(tmp_tehm, monkeypatch, tmp_path):
    oracle = IcarusOracle()
    if not oracle.available:
        pytest.skip("Icarus not installed")
    candidate = _candidate(tmp_tehm[0], monkeypatch)
    source = tmp_path / "worker.v"
    source.write_text(SOURCE)
    target = tmp_path / "target.v"
    target.write_text("""module tb;
reg clk=0, start=0, send=0, grant=0;
reg [15:0] data=16'h1234;
wire [15:0] value; wire finished;
worker dut(clk,start,send,grant,data,value,finished);
always #5 clk=~clk;
initial begin
 dut.phase=0;
 #1; start=1;
 @(negedge clk); start=0; send=1;
 repeat(2) begin @(negedge clk); if(finished !== 0) $fatal(1,"early completion"); end
 grant=1;
 @(negedge clk);
 if(finished !== 1 || value !== data) $fatal(1,"acceptance completion missing");
 $display("PASS"); $finish;
end
initial begin #200; $fatal(1,"timeout"); end
endmodule
""")
    regression = tmp_path / "regression.v"
    regression.write_text("""module tb;
reg clk=0, start=0, send=0, grant=0;
reg [15:0] data=16'h5678;
wire [15:0] value; wire finished;
worker dut(clk,start,send,grant,data,value,finished);
always #5 clk=~clk;
initial begin
 dut.phase=0;
 #1; start=1;
 @(negedge clk); start=0;
 repeat(2) begin @(negedge clk); if(finished !== 0) $fatal(1,"idle guard regression"); end
 send=1; grant=1;
 @(negedge clk);
 if(finished !== 1 || value !== data) $fatal(1,"baseline acceptance regression");
 $display("PASS"); $finish;
end
initial begin #200; $fatal(1,"timeout"); end
endmodule
""")
    case = {"rtl_source": str(source), "target_test": str(target), "frozen_regression": str(regression)}
    baseline = execute_rtl_candidate(None, case, 1, oracle=oracle)
    assert baseline["functional_result"] == "FAIL"
    repaired = execute_rtl_candidate(candidate, case, 1, oracle=oracle)
    assert repaired["functional_result"] == "PASS"
    assert repaired["metadata"]["oracle_complete"] is True
    assert repaired["metadata"]["action_edit"]["rewritten"] == 1
    assert source.read_text() == SOURCE
