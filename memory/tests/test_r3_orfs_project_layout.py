"""Layout fixtures are pre-execution checks, not physical evidence."""
import hashlib
import json

import pytest

from scripts.audit_r3_orfs_project_layout import OrfsProjectLayoutError, audit_project_layout


def _project(tmp_path):
    project = tmp_path / "project"
    (project / "rtl").mkdir(parents=True)
    (project / "constraints").mkdir()
    rtl = project / "rtl" / "fixture.v"
    sdc = project / "constraints" / "constraint.sdc"
    rtl.write_text("module fixture(input clk, output q); assign q=clk; endmodule\n")
    sdc.write_text("create_clock -period 10 [get_ports clk]\n")
    config = project / "constraints" / "config.mk"
    config.write_text(f"export VERILOG_FILES = {rtl}\nexport SDC_FILE = {sdc}\n")
    payload = {"campaign_id": "layout-fixture", "evaluation_only": True,
               "canonical_memory_mutation": "none", "production_runtime_imported": False,
               "cases": [{"case_id": "layout-case", "project_dir": str(project),
                          "source_inputs": [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                                            for p in (rtl, sdc)]}]}
    prereg = tmp_path / "preregistration.json"
    prereg.write_text(json.dumps(payload))
    return prereg, project, payload


def test_self_contained_frozen_sources_pass_without_eda(tmp_path):
    prereg, project, _ = _project(tmp_path)
    report = audit_project_layout(prereg, output=tmp_path / "audit.json")
    assert report["passed"] and report["eda_executed"] is False
    assert report["cases"]["layout-case"]["conventional_sdc_verified"]
    assert report["signoff_or_equivalence_claimed"] is False


def test_external_sdc_exists_but_is_not_a_complete_isolated_project(tmp_path):
    prereg, project, _ = _project(tmp_path)
    sdc = project / "constraints" / "constraint.sdc"
    external = tmp_path / "external.sdc"
    sdc.rename(external)
    config = project / "constraints" / "config.mk"
    config.write_text(config.read_text().replace(str(sdc), str(external)))
    with pytest.raises(OrfsProjectLayoutError, match="lacks local"):
        audit_project_layout(prereg, output=tmp_path / "audit.json")


@pytest.mark.parametrize("key", ["SDC_FILE", "VERILOG_FILES"])
def test_external_sources_rejected_even_with_a_local_sdc(tmp_path, key):
    prereg, project, payload = _project(tmp_path)
    original = project / ("constraints/constraint.sdc" if key == "SDC_FILE" else "rtl/fixture.v")
    external = tmp_path / original.name
    external.write_bytes(original.read_bytes())
    config = project / "constraints" / "config.mk"
    config.write_text(config.read_text().replace(str(original), str(external)))
    with pytest.raises(OrfsProjectLayoutError, match="SDC_FILE differs|external"):
        audit_project_layout(prereg, output=tmp_path / "audit.json")


def test_symlink_cannot_hide_an_escaping_source(tmp_path):
    prereg, project, _ = _project(tmp_path)
    original = project / "rtl" / "fixture.v"
    external = tmp_path / "external.v"
    original.rename(external)
    original.symlink_to(external)
    with pytest.raises(OrfsProjectLayoutError, match="external"):
        audit_project_layout(prereg, output=tmp_path / "audit.json")


def test_source_content_drift_rejected_before_execution(tmp_path):
    prereg, project, _ = _project(tmp_path)
    (project / "rtl" / "fixture.v").write_text("changed")
    with pytest.raises(OrfsProjectLayoutError, match="source pins"):
        audit_project_layout(prereg, output=tmp_path / "audit.json")


def test_duplicate_cases_cannot_count_as_more_coverage(tmp_path):
    prereg, _, payload = _project(tmp_path)
    payload["cases"].append(payload["cases"][0])
    prereg.write_text(json.dumps(payload))
    with pytest.raises(OrfsProjectLayoutError, match="duplicate"):
        audit_project_layout(prereg, output=tmp_path / "audit.json")


def test_previous_layout_report_is_never_overwritten(tmp_path):
    prereg, _, _ = _project(tmp_path)
    output = tmp_path / "audit.json"
    output.write_text("original")
    with pytest.raises(OrfsProjectLayoutError, match="must be new"):
        audit_project_layout(prereg, output=output)
    assert output.read_text() == "original"
