"""Evidence contradictions, missing scope and stale artifacts remain distinct."""
import copy
import hashlib
import json

import pytest

from r2g_agent import validation, artifact_snapshots
from r2g_agent.input_snapshots import capture, reference
from r2g_agent.jobs import JobStore


def write(project, name, value):
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if isinstance(value, (dict, list)) else value)
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    p = tmp_path / "project"
    write(p, "rtl/top.v", "module top; endmodule")
    write(p, "constraints/constraint.sdc", "create_clock -period 1 clk")
    gds = write(p, "backend/RUN_test/results/6_final.gds", "synthetic layout")
    raw = write(p, "drc/6_drc.lyrdb", '<report-database><description>synthetic</description><generator>drc: script</generator>'
                '<top-cell>top</top-cell><categories><category><name>WELL.1</name></category>'
                '<category><name>ANTENNA</name></category></categories><cells><cell><name>top</name></cell></cells>'
                '<items/></report-database>')
    log = write(p, "drc/6_drc.log", "Writing report database: 6_drc.lyrdb\nTotal elapsed: 1.0s\n")
    deck = write(p, "drc/audit_rule.lydrc", 'well.output("WELL.1", "rule")\nantenna.output("ANTENNA", "rule")')
    write(p, "drc/6_drc_count.rpt", "0")
    write(p, "drc/drc_result.json", {"status": "clean", "violations": 0, "drc_mode": "full", "exit_code": 0,
          "run_tag": "RUN_test", "gds_sha256": sha(gds), "deck_sha256": sha(deck), "deck_sha256_post": sha(deck),
          "audit_rule_sha256": sha(deck), "raw_report_sha256": sha(raw), "log_sha256": sha(log), "excluded_rules": []})
    write(p, "reports/drc.json", {"status": "clean", "total_violations": 0, "raw_marker_count": 0,
                                  "drc_mode": "full", "exit_code": 0, "run_tag": "RUN_test", "gds_sha256": sha(gds),
                                  "deck_sha256": sha(deck)})
    spef = write(p, "rcx/6_final.spef", '*SPEF "IEEE 1481-1998"\n*D_NET *1 0.1\n*END\n*D_NET *2 0.2\n*END\n')
    receipt = {"status": "complete", "exit_code": 0, "spef_sha256": sha(spef)}
    write(p, "rcx/rcx_result.json", receipt)
    write(p, "reports/rcx.json", {"status": "complete", "net_count": 2, "execution": receipt,
                                  "spef_file": str(spef), "log_info": {"nets_extracted": 7}})
    binding = reference(capture(p))
    write(p, "reports/input_bindings.json", {"signoff": {"before": binding, "after": binding,
                                                         "exit_code": 0, "unchanged": True}})
    write(p, "lvs/raw.fixture", "PASS")
    def raw_audit(project):
        # Engine parsers have separate tests. This fixture checks that review
        # reads their fresh result rather than the producer's summary.
        verdict = (project / "lvs/raw.fixture").read_text()
        return {"schema_version": 1, "verdict": verdict,
                "layers": [{"id": name, "verdict": verdict, "reasons": []} for name in validation.LAYER_IDS],
                "verification_level": "transistor_hierarchical", "policy_sha256": "synthetic-auditor-policy",
                "metrics": {"layout": {"devices": 11}, "schematic": {"devices": 11}},
                "evidence_files": [{"path": "lvs/raw.fixture", "sha256": sha(project / "lvs/raw.fixture")}]}
    monkeypatch.setattr(validation, "_audit_lvs", raw_audit)
    return p


def test_required_scope_does_not_invent_simulation_or_require_fmax(evidence):
    write(evidence, "reports/signoff_manifest.json", {"strict_clean": False, "strict_missing": ["fmax_search missing"]})
    out = validation.review(evidence)
    assert out["verdict"] == "PASS"
    assert out["checks"]["simulation"]["verdict"] == "UNKNOWN"
    assert out["prior_policy"]["strict_clean"] is False and not out["prior_policy"]["required_by_this_review"]
    assert out["required_checks"] == ["drc", "lvs", "rcx"]
    assert out["checks"]["rcx"]["metrics"]["spef_nets"] == 2
    assert out["input_snapshot"] == capture(evidence)


@pytest.mark.parametrize("field", ["before", "unchanged", "exit_code", "after"])
def test_unconfirmed_stage_does_not_export_verified_input_snapshot(evidence, field):
    bindings = json.loads((evidence / "reports/input_bindings.json").read_text())
    bindings["signoff"].pop(field)
    write(evidence, "reports/input_bindings.json", bindings)
    checked = validation.review(evidence)
    assert checked["verdict"] != "PASS" and checked["input_snapshot"] is None


def test_stage_reference_must_bind_component_manifests(evidence):
    bindings = json.loads((evidence / "reports/input_bindings.json").read_text())
    bindings["signoff"]["after"]["rtl_sha256"] = "different"
    write(evidence, "reports/input_bindings.json", bindings)
    checked = validation.review(evidence)
    assert checked["verdict"] == "STALE" and checked["input_snapshot"] is None


def test_review_stages_cannot_silently_bind_different_sources(evidence):
    observed = validation._Evidence(evidence)
    assert validation._stage_binding(observed, "signoff") == ("PASS", None)
    (evidence / "rtl/top.v").write_text("module changed; endmodule")
    other = reference(capture(evidence))
    write(evidence, "reports/input_bindings.json", {"verify": {
        "before": other, "after": other, "exit_code": 0, "unchanged": True}})
    assert validation._stage_binding(observed, "verify") == ("STALE", "review_input_receipt_changed")


@pytest.mark.parametrize("count,verdict", [(None, "UNKNOWN"), (1, "FAIL"), (-1, "UNKNOWN"), (False, "UNKNOWN"), ("0", "UNKNOWN")])
def test_drc_clean_cannot_override_invalid_or_nonzero_count(evidence, count, verdict):
    data = json.loads((evidence / "reports/drc.json").read_text())
    data["total_violations"] = count
    write(evidence, "reports/drc.json", data)
    out = validation.review(evidence)
    assert out["checks"]["drc"]["verdict"] == out["verdict"] == verdict


def test_summary_zero_conflicting_with_raw_markers_fails(evidence):
    data = json.loads((evidence / "reports/drc.json").read_text())
    data["raw_marker_count"] = 2
    write(evidence, "reports/drc.json", data)
    assert validation.review(evidence)["checks"]["drc"]["verdict"] == "FAIL"


def test_lvs_is_reaudited_and_claims_cannot_supply_observations(evidence):
    write(evidence, "reports/lvs.json", {"status": "clean", "three_layer": {"verdict": "PASS"}})
    assert validation.review(evidence)["checks"]["lvs"]["verdict"] == "PASS"
    write(evidence, "lvs/raw.fixture", "FAIL")
    claims = [{"claim_id": "forged", "kind": "lvs", "claimed": "PASS", "observed": "PASS", "verdict": "PASS",
               "evidence": [{"path": "invented", "sha256": "invented"}]}]
    out = validation.review(evidence, claims=claims)
    assert out["verdict"] == "FAIL"
    row = out["claims"][-1]
    assert row["observed"] == "FAIL" and row["verdict"] == "FAIL"
    assert row["evidence"][0]["path"] == "lvs/raw.fixture"


def test_nine_vs_eleven_is_failed_claim_not_changed_measurement(evidence):
    out = validation.review(evidence, claims=[{"claim_id": "device_count", "kind": "lvs", "metric": "layout.devices", "claimed": 9}])
    assert out["claims"][-1]["observed"] == 11 and out["claims"][-1]["verdict"] == "FAIL"
    assert out["verdict"] == "PASS"
    assert validation.compare_claim({"claimed": 9}, {"value": 11, "verdict": "UNKNOWN"})["verdict"] == "FAIL"


def timing_evidence(project):
    ppa = write(project, "reports/ppa.json", {"summary": {"timing": {
        "setup_wns": 1, "setup_tns": 0, "hold_wns": 1, "hold_tns": 0}}})
    write(project, "reports/timing_check.json", {"tier": "clean", "wns": 1, "tns": 0, "hold_wns": 1,
              "hold_tns": 0, "run_tag": "RUN_test", "provenance": {"ppa_sha256": sha(ppa)}})
    return write(project, "backend/RUN_test/logs/6_report.json", {
        "finish__timing__setup__ws": 1, "finish__timing__setup__tns": 0,
        "finish__timing__hold__ws": 1, "finish__timing__hold__tns": 0})


def test_sta_is_not_simulation(evidence):
    timing_evidence(evidence)
    out = validation.review(evidence, claims=[{"claim_id": "simulation_done", "kind": "simulation", "claimed": "PASS"}])
    assert out["checks"]["sta"]["verdict"] == "PASS" and out["claims"][-1]["verdict"] == "UNKNOWN"
    row = validation.compare_claim({"claimed": "PASS", "verification_level": "functional_simulation"},
        {"value": "PASS", "verdict": "PASS", "method": "static_timing_analysis", "verification_level": "static_timing"})
    assert row["verdict"] == "FAIL"


@pytest.mark.parametrize("mutation,expected", [("absent", "UNKNOWN"), ("missing_slack", "UNKNOWN"),
                                              ("native_conflict", "FAIL"), ("ppa_conflict", "FAIL")])
def test_sta_needs_native_evidence_consistent_with_both_summaries(evidence, mutation, expected):
    path = timing_evidence(evidence)
    if mutation == "absent":
        path.unlink()
    elif mutation == "ppa_conflict":
        ppa = write(evidence, "reports/ppa.json", {"summary": {"timing": {
            "setup_wns": 9, "setup_tns": 0, "hold_wns": 1, "hold_tns": 0}}})
        report = json.loads((evidence / "reports/timing_check.json").read_text())
        report["provenance"]["ppa_sha256"] = sha(ppa)
        write(evidence, "reports/timing_check.json", report)
    else:
        raw = json.loads(path.read_text())
        if mutation == "missing_slack":
            raw.pop("finish__timing__setup__ws")
        else:
            raw["finish__timing__setup__ws"] = 0.5
        path.write_text(json.dumps(raw))
    out = validation.review(evidence)
    assert out["checks"]["sta"]["verdict"] == expected
    assert out["checks"]["simulation"]["verdict"] == "UNKNOWN"


def test_structure_only_cannot_support_transistor_claim():
    row = validation.compare_claim({"claimed": "PASS", "verification_level": "transistor_hierarchical"},
        {"value": "PASS", "verdict": "PASS", "method": "structural_compare", "verification_level": "structure_only"})
    assert row["verdict"] == "FAIL"


def test_antenna_nine_vs_eleven_and_absent_timing_claims(evidence):
    drc = json.loads((evidence / "reports/drc.json").read_text())
    drc.update(status="violations", total_violations=11, raw_marker_count=11,
               categories={"antenna.gate_ratio": {"count": 11}})
    write(evidence, "reports/drc.json", drc)
    raw = evidence / "drc/6_drc.lyrdb"
    raw.write_text(raw.read_text().replace('<items/>', '<items>' +
                   '<item><category>ANTENNA</category><cell>top</cell><values/></item>' * 11 + '</items>'))
    write(evidence, "drc/6_drc_count.rpt", "11")
    receipt = json.loads((evidence / "drc/drc_result.json").read_text())
    receipt.update(violations=11, status="violations", raw_report_sha256=sha(raw))
    write(evidence, "drc/drc_result.json", receipt)
    out = validation.review(evidence, claims=[
        {"claim_id": "antenna_count", "kind": "drc", "metric": "antenna_violations", "claimed": 9},
        {"claim_id": "timing_pass", "kind": "sta", "claimed": "PASS"}])
    assert out["claims"][-2]["observed"] == 11 and out["claims"][-2]["verdict"] == "FAIL"
    assert out["claims"][-1]["verdict"] == "UNKNOWN"
    assert next(row for row in out["claims"] if row["claim_id"] == "simulation.ok")["claimed"] is None


@pytest.mark.parametrize("content", ["not json", {"status": {}, "total_violations": None},
                                      {"status": "clean", "total_violations": 0, "exit_code": False, "drc_mode": "full"}])
def test_malformed_reports_are_unknown_without_crashing(evidence, content):
    write(evidence, "reports/drc.json", content)
    assert validation.review(evidence)["checks"]["drc"]["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("raw", ["<report-database/>", "<root><items/></root>", "<report-database>",
                                  '<!DOCTYPE x [<!ENTITY x "x">]><report-database/>'])
def test_empty_or_unsupported_native_drc_cannot_pass_with_zero_summary(evidence, raw):
    write(evidence, "drc/6_drc.lyrdb", raw)
    out = validation.review(evidence)
    assert out["checks"]["drc"]["verdict"] == "UNKNOWN"
    assert all(layer["verdict"] == "UNKNOWN" for layer in out["three_layer"].values())


def test_native_drc_items_conflict_with_clean_zero_summary(evidence):
    path = evidence / "drc/6_drc.lyrdb"
    path.write_text(path.read_text().replace('<items/>',
        '<items><item><category>WELL.1</category><cell>top</cell><values/></item></items>'))
    out = validation.review(evidence)
    assert out["checks"]["drc"]["verdict"] == "FAIL"
    assert out["checks"]["drc"]["metrics"]["native_items"] == 1
    assert all(layer["verdict"] == "FAIL" for layer in out["three_layer"].values())


@pytest.mark.parametrize("field,value,expected", [("raw_report_sha256", "wrong", "STALE"),
    ("log_sha256", "wrong", "STALE"), ("audit_rule_sha256", "wrong", "STALE"),
    ("excluded_rules", ["FEOL"], "UNKNOWN"), ("exit_code", 124, "UNKNOWN")])
def test_native_drc_requires_bound_artifacts_and_full_scope(evidence, field, value, expected):
    receipt = json.loads((evidence / "drc/drc_result.json").read_text())
    receipt[field] = value
    write(evidence, "drc/drc_result.json", receipt)
    assert validation.review(evidence)["checks"]["drc"]["verdict"] == expected


def test_native_drc_log_must_confirm_completion(evidence):
    write(evidence, "drc/6_drc.log", "Starting DRC; output not completed")
    receipt = json.loads((evidence / "drc/drc_result.json").read_text())
    receipt["log_sha256"] = sha(evidence / "drc/6_drc.log")
    write(evidence, "drc/drc_result.json", receipt)
    assert validation.review(evidence)["checks"]["drc"]["verdict"] == "UNKNOWN"


def test_native_drc_rule_category_omitted_from_report_is_unknown(evidence):
    deck = write(evidence, "drc/audit_rule.lydrc", 'layer.output("UNRUN_RULE", "rule")')
    receipt = json.loads((evidence / "drc/drc_result.json").read_text())
    for field in ("deck_sha256", "deck_sha256_post", "audit_rule_sha256"):
        receipt[field] = sha(deck)
    write(evidence, "drc/drc_result.json", receipt)
    report = json.loads((evidence / "reports/drc.json").read_text())
    report["deck_sha256"] = sha(deck)
    write(evidence, "reports/drc.json", report)
    assert validation.review(evidence)["checks"]["drc"]["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("target", ["rcx/6_final.spef", "backend/RUN_test/results/6_final.gds", "rtl/top.v"])
def test_changed_bound_artifacts_are_stale(evidence, target):
    with (evidence / target).open("a") as stream:
        stream.write("\nchanged bytes\n")
    assert validation.review(evidence)["verdict"] == "STALE"


def test_rcx_same_scope_report_count_conflict_fails(evidence):
    data = json.loads((evidence / "reports/rcx.json").read_text())
    data["net_count"] = 3
    write(evidence, "reports/rcx.json", data)
    assert validation.review(evidence)["checks"]["rcx"]["verdict"] == "FAIL"


def test_missing_gds_unknown_never_reads_report_external_path(evidence, tmp_path):
    (evidence / "backend/RUN_test/results/6_final.gds").unlink()
    data = json.loads((evidence / "reports/drc.json").read_text())
    data["gds_path"] = str(tmp_path / "private")
    write(evidence, "reports/drc.json", data)
    out = validation.review(evidence)
    assert out["checks"]["drc"]["verdict"] == "UNKNOWN"
    assert not any("private" in item["path"] for item in out["evidence_files"])


def test_symlink_report_is_unknown(evidence, tmp_path):
    path = evidence / "reports/drc.json"
    outside = tmp_path / "outside"
    path.rename(outside)
    path.symlink_to(outside)
    assert validation.review(evidence)["checks"]["drc"]["verdict"] == "UNKNOWN"


def test_dataset_empty_nonboolean_or_duplicate_checks_do_not_pass(evidence):
    for results in ([], [{"check": "bad", "ok": "false"}], [{"check": "same", "ok": True}] * 2):
        write(evidence, "reports/verify.json", {"results": results})
        assert validation.review(evidence)["checks"]["dataset"]["verdict"] == "UNKNOWN"


@pytest.mark.parametrize("image", [False, True])
def test_dataset_checks_need_execution_binding_and_exact_summary_counts(evidence, image):
    name = "images/verify.json" if image else "reports/verify.json"
    results = [{"check": "source.binding", "ok": True}, {"check": "tensor.shape", "ok": True}]
    payload = {"results": results, "passed": 2, "total": 2}
    write(evidence, name, payload)
    if image:
        write(evidence, "reports/image_dataset.json", {"status": "ok", "verify": {"passed": 2, "total": 2}})
    assert validation.review(evidence)["checks"]["dataset"]["verdict"] == "UNKNOWN"
    bindings = json.loads((evidence / "reports/input_bindings.json").read_text())
    bindings["graph" if image else "verify"] = {**bindings["signoff"], "execution_id": "fixture-execution"}
    write(evidence, "reports/input_bindings.json", bindings)
    assert validation.review(evidence)["checks"]["dataset"]["verdict"] == "UNKNOWN"
    kind = "image" if image else "graph"
    for path in (("images/features.npz", "images/labels.npz", "images/image_meta.json") if image else ("dataset/graph.pt",)):
        write(evidence, path, "fixture")
    before = artifact_snapshots.begin(evidence, kind)
    if image:
        core = artifact_snapshots.capture(evidence, kind)["files"]
        payload["output_binding"] = {"schema_version": 1, "scope": "core_files_unchanged_during_verifier",
                                     "before": core, "after": core}
    write(evidence, name, payload)
    receipt = artifact_snapshots.finish(evidence, kind, before, "fixture-execution", 0)
    write(evidence, "reports/output_bindings.json", {kind: receipt})
    checked = validation.review(evidence)["checks"]["dataset"]
    assert checked["verdict"] == "PASS" and checked["metrics"] == {"passed": 2, "total": 2}
    assert checked["method"] == ("image_dataset_verifier" if image else "graph_dataset_verifier")
    assert validation.review(evidence)["output_snapshot"] == receipt["after"]
    write(evidence, name, {"results": results, "passed": 9, "total": 11})
    # A changed report is stale even when its numbers are also contradictory.
    assert validation.review(evidence)["checks"]["dataset"]["verdict"] == "STALE"


def test_image_summary_alone_cannot_award_dataset_pass(evidence):
    write(evidence, "reports/image_dataset.json", {"status": "ok", "verify": {"passed": 17, "total": 17}})
    assert validation.review(evidence)["checks"]["dataset"]["verdict"] == "UNKNOWN"


def test_summary_idempotent_and_legacy_projection_never_rewrites_history(evidence):
    out = validation.review(evidence)
    summary = validation.validation_summary({"validation": out})
    assert validation.validation_summary({"validation": summary}) == summary
    assert "claims" not in summary and "audit" not in summary["checks"]["lvs"]
    store = JobStore()
    jid = store.create_job("legacy", {}, [{"name": "top"}])
    store.set_design(jid, "top", status="passed", detail={"signoff": {"lvs": "clean", "lvs_mismatches": 7}})
    before = copy.deepcopy(store.design(jid, "top"))
    projected = store.context_snapshot(jid)["designs"][0]
    assert projected["validation"]["status"] == "legacy_unreviewed"
    assert projected["validation"]["verdict"] == "UNKNOWN"
    assert projected["signoff"]["lvs_mismatches"] == 7
    assert store.design(jid, "top") == before
    current = store.create_job("reviewed", {}, [{"name": "top"}])
    store.set_design(current, "top", status="passed", detail={"validation": summary})
    assert store.context_snapshot(current)["designs"][0]["validation"] == summary


def test_changed_report_on_reread_keeps_first_hash_and_invalidates_pass(evidence, monkeypatch):
    report = evidence / "reports/drc.json"
    first_hash = sha(report)
    original = validation._statements
    def replace_after_checks(reader):
        changed = json.loads(report.read_text())
        changed["total_violations"] = 11
        write(evidence, "reports/drc.json", changed)
        return original(reader)
    monkeypatch.setattr(validation, "_statements", replace_after_checks)
    out = validation.review(evidence, claims=[{"claim_id": "drc_claim", "kind": "drc", "claimed": "PASS"}])
    assert out["verdict"] == out["checks"]["drc"]["verdict"] == "STALE"
    assert "reports/drc.json" in out["changed_evidence"]
    stored = next(ref for ref in out["evidence_files"] if ref["path"] == "reports/drc.json")
    assert stored["sha256"] == first_hash != sha(report)
    assert out["claims"][-1]["verdict"] == "STALE"
