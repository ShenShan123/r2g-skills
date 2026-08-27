from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_calibration_expansion import (_strict_oracle_one, _strict_oracle_projects,
                                       _subset_manifest)  # noqa: E402


def test_subset_manifest_selects_scratch_lineages_without_mutating_source():
    manifest = {
        "items": [
            {"case_id": "v39", "lineage_id": "future-prospective-v39:sky130hs:x"},
            {"case_id": "v40", "lineage_id": "future-prospective-v40:sky130hs:x"},
        ],
        "version": "calibration-expansion-v1",
    }
    selected = _subset_manifest(manifest, {"v40"})
    assert [item["case_id"] for item in selected["items"]] == ["v40"]
    assert selected["selected_suffixes"] == ["v40"]
    assert len(manifest["items"]) == 2


def test_strict_oracle_runs_signoff_and_timing_and_reuses_bound_receipt(tmp_path,
                                                                        monkeypatch):
    project = tmp_path / "project"
    run = project / "backend" / "RUN_demo"
    run.mkdir(parents=True)
    (run / "run-meta.json").write_text(json.dumps({"run_tag": "RUN_demo"}))
    manifest = {"items": [{"platform": "sky130hs",
                            "before_project": str(project),
                            "after_project": str(project)}]}
    assert _strict_oracle_projects(manifest) == [(project, "sky130hs")]
    calls = []

    class Proc:
        returncode = 0

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        reports = project / "reports"
        if cmd[1].endswith("run_strict_signoff.sh"):
            (reports / "strict_signoff.json").write_text(json.dumps({
                "run_tag": "RUN_demo", "status": "pass"}))
        else:
            (reports / "timing_check.json").write_text(json.dumps({
                "status": "clean", "tier": "clean"}))
        return Proc()

    monkeypatch.setattr("run_calibration_expansion.subprocess.run", fake_run)
    first = _strict_oracle_one(project, "sky130hs", timeout=10)
    assert first["strict_rc"] == 0
    assert first["timing_rc"] == 0
    assert first["strict_status"] == "pass"
    assert first["timing_status"] == "clean"
    assert len(calls) == 2

    second = _strict_oracle_one(project, "sky130hs", timeout=10)
    assert second["reused"] is True
    assert second["strict_rc"] is None
    assert second["timing_rc"] is None
    assert len(calls) == 2
