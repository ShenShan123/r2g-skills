import json
from types import SimpleNamespace

import pytest

from scripts.run_r3_orfs_p15_calibration import (
    OrfsP15CalibrationError, _digest, _prospective_calibration_partition, _strata, run,
)


def test_orfs_calibration_strata_are_backend_explicit():
    strata = _strata({
        "project_dir": "/tmp/sky130hs_uart_base_0",
        "platform": "sky130hs",
    })
    assert strata["mechanism_family"] == "ORFS_DENSITY_RELIEF"
    assert strata["design"] == "uart_base"
    assert strata["platform"] == "sky130hs"
    assert strata["flow_regime"] == "orfs_route_real"
    assert strata["model_identity"] == "typed-paired-oracle-v1"
    assert strata["state_shift_dimension"] == "none"


def _partition(tmp_path):
    cases = [{"case_id": cid, "lineage_id": "lineage:" + cid,
        "dataset_split": "calibration", "role": "calibration", "learner_eligible": False}
        for cid in ("a", "b")]
    manifest = {"campaign_id": "calibration", "lane": "CALIBRATION", "learner_eligible": False,
                "cases": cases}
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    path = receipts / "campaign_manifest.json"
    path.write_text(json.dumps(manifest))
    cohort = SimpleNamespace(campaign_id="calibration", campaign_manifest_digest=_digest(manifest),
        case_receipts={cid: object() for cid in ("a", "b")},
        lineage_ids={cid: "lineage:" + cid for cid in ("a", "b")})
    return manifest, {"cases": cases}, cohort, path


def test_execution_bound_calibration_membership_is_accepted(tmp_path):
    _, cases, cohort, _ = _partition(tmp_path)
    report = _prospective_calibration_partition(tmp_path, cases, cohort)
    assert report["membership_execution_bound"] is True
    assert report["learner_eligible"] is False


@pytest.mark.parametrize("split", ["training", "validation", "held_out", None])
def test_non_calibration_membership_cannot_be_relabeled(tmp_path, split):
    manifest, cases, cohort, path = _partition(tmp_path)
    manifest["cases"][0].update(dataset_split=split, role=split, learner_eligible=split == "training")
    path.write_text(json.dumps(manifest))
    cohort.campaign_manifest_digest = _digest(manifest)
    with pytest.raises(OrfsP15CalibrationError, match="cannot be relabeled"):
        _prospective_calibration_partition(tmp_path, cases, cohort)


def test_post_execution_role_edit_cannot_change_frozen_manifest(tmp_path):
    manifest, cases, cohort, path = _partition(tmp_path)
    manifest["cases"][0]["lineage_id"] = "changed-after-execution"
    path.write_text(json.dumps(manifest))
    with pytest.raises(OrfsP15CalibrationError, match="execution-bound"):
        _prospective_calibration_partition(tmp_path, cases, cohort)


def test_evolution_challenge_lane_is_not_calibration(tmp_path):
    manifest, cases, cohort, path = _partition(tmp_path)
    manifest["lane"] = "EVOLUTION_CHALLENGE"
    path.write_text(json.dumps(manifest))
    cohort.campaign_manifest_digest = _digest(manifest)
    with pytest.raises(OrfsP15CalibrationError, match="execution-bound"):
        _prospective_calibration_partition(tmp_path, cases, cohort)


def test_case_index_cannot_drop_a_frozen_member(tmp_path):
    _, cases, cohort, _ = _partition(tmp_path)
    cases["cases"] = cases["cases"][:1]
    with pytest.raises(OrfsP15CalibrationError, match="case membership"):
        _prospective_calibration_partition(tmp_path, cases, cohort)


def test_declared_lineage_must_match_the_actual_cohort(tmp_path):
    _, cases, cohort, _ = _partition(tmp_path)
    cohort.lineage_ids["a"] = "other-lineage"
    with pytest.raises(OrfsP15CalibrationError, match="cannot be relabeled"):
        _prospective_calibration_partition(tmp_path, cases, cohort)


def test_force_never_deletes_source_or_previous_evidence(tmp_path):
    prior = tmp_path / "prior"
    prior.mkdir()
    marker = prior / "retained.json"
    marker.write_text("retained")
    with pytest.raises(OrfsP15CalibrationError, match="immutable"):
        run(challenge_artifacts=prior, artifacts=prior, force=True)
    assert marker.read_text() == "retained"


def test_bad_partition_does_not_leave_fake_calibration_output(tmp_path, monkeypatch):
    import scripts.run_r3_orfs_p15_calibration as module
    challenge_root = tmp_path / "challenge"
    challenge_root.mkdir()
    manifest, cases, cohort, path = _partition(challenge_root)
    manifest["lane"] = "EVOLUTION_CHALLENGE"
    path.write_text(json.dumps(manifest))
    cohort.campaign_manifest_digest = _digest(manifest)
    monkeypatch.setattr(module.shadow, "_load_challenge", lambda *args: (cases, cohort, {}, {}, {}, {}, {}, None))
    output = tmp_path / "calibration-output"
    with pytest.raises(OrfsP15CalibrationError, match="execution-bound"):
        run(challenge_artifacts=challenge_root, artifacts=output)
    assert not output.exists()


def _known_pair(case_id, forced):
    from tehm.evaluation.candidate_executor import execute_paired_candidates
    from test_no_skill_calibration import _oracle_candidate, _route
    route = _route("CONSIDER")
    def oracle(candidate, case, budget):
        outcome = forced if candidate is not None else "PASS"
        return {"compile_result": "PASS", "functional_result": outcome,
                "signoff_result": outcome, "outcome": outcome}
    pair = execute_paired_candidates({"case_id": case_id,
        "toolchain_digest": "sha256:test-tools", "oracle_digest": "sha256:test-oracle"},
        {"NO_MEMORY": None, "ALWAYS_MEMORY": _oracle_candidate(),
         "APPLICABILITY_GATED": _oracle_candidate(), "CAUSAL_NO_SKILL": _oracle_candidate()},
        oracle=oracle, budget=3, lineage_id="lineage:" + case_id,
        routing_receipt_id=route.routing_receipt_id, routing_decision="CONSIDER")
    return pair, route


@pytest.mark.parametrize("all_neutral", [False, True])
def test_unclassifiable_cases_are_retained_but_not_binary_samples(tmp_path, monkeypatch, all_neutral):
    import scripts.run_r3_orfs_p15_calibration as module
    root = tmp_path / "source"
    root.mkdir()
    _, cases, cohort, _ = _partition(root)
    pairs, routes = {}, {}
    for case in cases["cases"]:
        case["project_dir"] = str(root / ("sky130hs_" + case["case_id"] + "_0"))
        case["platform"] = "sky130hs"
        pairs[case["case_id"]], routes[case["case_id"]] = _known_pair(
            case["case_id"], "FAIL" if case["case_id"] == "a" and not all_neutral else "PASS")
    manifest = {"campaign_id": "calibration", "lane": "CALIBRATION",
                "learner_eligible": False, "cases": cases["cases"]}
    (root / "receipts/campaign_manifest.json").write_text(json.dumps(manifest))
    cohort.campaign_manifest_digest = _digest(manifest)
    cohort.case_receipts = pairs
    cohort.lineage_count = 2
    for name in ("cohort.json", "cases.json", "p13_reason_receipt.json"):
        (root / "receipts" / name).write_text("{}")
    monkeypatch.setattr(module.shadow, "_load_challenge", lambda *args:
        (cases, cohort, routes, {}, {}, {}, {}, SimpleNamespace(receipt_digest="sha256:reason")))
    output = tmp_path / "output"
    if all_neutral:
        with pytest.raises(OrfsP15CalibrationError, match="calibration NOT_ESTABLISHED"):
            run(challenge_artifacts=root, artifacts=output, minimum_sample_count=2)
        assert not (output / "receipts/calibration_report.json").exists()
        assert not (output / "summary.json").exists()
        derivations = json.loads((output / "receipts/oracle_label_derivations.json").read_text())
        assert set(derivations["excluded_cases"]) == {"a", "b"}
        return
    summary = run(challenge_artifacts=root, artifacts=output, minimum_sample_count=2)
    assert summary["executed_case_count"] == 2
    assert summary["sample_count"] == 1
    assert set(summary["excluded_cases"]) == {"b"}
    assert summary["calibration_receipt"]["confidence_coverage"] == 0
    assert summary["calibration_receipt"]["eligible"] is False
    assert summary["production_promotion_eligible"] is False
    saved = json.loads((output / "receipts/calibration_manifest.json").read_text())
    assert set(saved["oracle_labels"]) == set(saved["routing_decisions"]) == {"a"}
    derivations = json.loads((output / "receipts/oracle_label_derivations.json").read_text())
    assert set(derivations["derivations"]) == {"a", "b"}
    assert derivations["derivations"]["b"]["expected_decision"] is None
    assert derivations["derivations"]["a"]["router_confidence_policy"] == "ABSENT_NOT_IMPUTED"
