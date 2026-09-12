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
