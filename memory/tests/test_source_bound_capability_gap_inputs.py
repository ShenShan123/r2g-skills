"""Prospective gap input conformance; these fixtures are not expansion evidence."""
import json
from types import SimpleNamespace

import pytest

import scripts.prepare_r3_source_bound_capability_gap as module


def _project(tmp_path, role="training", design="source-design"):
    project = tmp_path / design
    (project / "rtl").mkdir(parents=True)
    (project / "tb").mkdir()
    (project / "rtl" / "source.v").write_text(
        "module source(input clk, input enable, output reg value);\n"
        "reg [1:0] state, next_state; localparam IDLE=0, WAIT=1, DONE=2;\n"
        "always @(posedge clk) begin state <= next_state; if(enable) value <= 1'b1; end\n"
        "always @(*) begin\n next_state = state;\n case(state)\n"
        " IDLE: if(enable) next_state = WAIT;\n WAIT: if(enable) next_state = DONE;\n"
        " DONE: next_state = IDLE;\n endcase\n end\n endmodule\n")
    for index, name in enumerate(("target.v", "regression.v")):
        (project / "tb" / name).write_text("module test; initial #" + str(index + 1) + " $finish; endmodule\n")
    manifest = {"design": design, "mechanism_family": "HANDSHAKE_COMPLETION",
        "verification": {"target_test": "tb/target.v", "frozen_regression": "tb/regression.v"}}
    if role == "training":
        manifest["fix"] = {"domain": "rtl.GUARD_STRENGTHEN"}
    (project / "manifest.json").write_text(json.dumps(manifest))
    row = {"case_id": design, "project": str(project), "lineage_id": design,
        "role": role, "dataset_split": role, "learner_eligible": role == "training",
        "query_plan": {"mechanism_family": "HANDSHAKE_COMPLETION",
            "compatibility_profile": "rtl.fsm.single_guard.v1", "target_scope": "rtl_target"}}
    return project, row, manifest


@pytest.mark.parametrize("role", ["training", "held_out", "validation"])
def test_case_preserves_actual_file_and_declared_nonlearner_role(tmp_path, role):
    project, row, _ = _project(tmp_path, role)
    case = module._case(row)
    assert case["learner_eligible"] is (role == "training")
    assert case["rtl_inputs"][0]["sha256"] == module._sha256(project / "rtl/source.v")
    assert case["query"]["query_plan"] == row["query_plan"]
    assert case["repair_proposal_is_not_evidence"] is True


@pytest.mark.parametrize("field,value", [
    ("role", "calibration"), ("dataset_split", "held_out"),
    ("learner_eligible", False), ("lineage_id", "renamed-project"),
])
def test_role_and_lineage_drift_are_rejected(tmp_path, field, value):
    _, row, _ = _project(tmp_path)
    row[field] = value
    with pytest.raises(module.CapabilityGapInputError, match="role/lineage"):
        module._case(row)


@pytest.mark.parametrize("role", ["held_out", "validation"])
def test_nonlearner_repair_answer_cannot_enter_input_compiler(tmp_path, role):
    project, row, manifest = _project(tmp_path, role)
    manifest["fix"] = {"add_condition": "secret_answer"}
    (project / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(module.CapabilityGapInputError, match="repair answer"):
        module._case(row)


@pytest.mark.parametrize("field", ["outcome", "oracle_label", "replacement", "no_skill_reason"])
def test_query_cannot_use_oracle_prediction_or_mutation_plan(tmp_path, field):
    _, row, _ = _project(tmp_path)
    row["query_plan"][field] = "forbidden"
    with pytest.raises(module.CapabilityGapInputError, match="static"):
        module._case(row)


def test_declared_mechanism_must_match_project_manifest(tmp_path):
    _, row, _ = _project(tmp_path)
    row["query_plan"]["mechanism_family"] = "DENSITY_RELIEF"
    with pytest.raises(module.CapabilityGapInputError, match="static"):
        module._case(row)


def test_target_and_regression_cannot_alias(tmp_path):
    project, row, manifest = _project(tmp_path)
    manifest["verification"]["frozen_regression"] = "tb/target.v"
    (project / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(module.CapabilityGapInputError, match="alias"):
        module._case(row)


def test_renaming_identical_testbench_bytes_does_not_create_regression_coverage(tmp_path):
    project, row, _ = _project(tmp_path)
    (project / "tb/regression.v").write_bytes((project / "tb/target.v").read_bytes())
    with pytest.raises(module.CapabilityGapInputError, match="alias"):
        module._case(row)


def test_empty_case_identity_is_rejected(tmp_path):
    _, row, _ = _project(tmp_path)
    row["case_id"] = ""
    with pytest.raises(module.CapabilityGapInputError, match="case identity"):
        module._case(row)


def test_declared_fsm_profile_cannot_hide_parser_unsupported_sensitivity(tmp_path):
    project, row, _ = _project(tmp_path)
    source = project / "rtl/source.v"
    source.write_text(source.read_text().replace("always @(*)", "always @*"))
    with pytest.raises(module.CapabilityGapInputError, match="parser-supported FSM"):
        module._case(row)


def test_testbench_cannot_escape_project_root(tmp_path):
    project, row, manifest = _project(tmp_path)
    (tmp_path / "outside.v").write_text("module outside; endmodule")
    manifest["verification"]["target_test"] = "../outside.v"
    (project / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(module.CapabilityGapInputError, match="contained"):
        module._case(row)


def _cases():
    roles = ("training", "training", "held_out", "held_out", "validation")
    return [{"case_id": str(i), "lineage_id": "lineage" + str(i), "role": role,
        "rtl_inputs": [{"sha256": "sha256:" + str(i)}]} for i, role in enumerate(roles)]


def test_declared_three_partitions_are_preserved():
    assert module._partitions(_cases()) == {
        "training": ["0", "1"], "held_out": ["2", "3"], "validation": ["4"]}


def test_lineage_rename_does_not_hide_duplicate_rtl():
    cases = _cases()
    cases[2]["rtl_inputs"] = cases[0]["rtl_inputs"]
    with pytest.raises(module.CapabilityGapInputError, match="overlap"):
        module._partitions(cases)


def test_insufficient_heldout_coverage_is_not_silently_dropped():
    with pytest.raises(module.CapabilityGapInputError, match="held_out"):
        module._partitions(_cases()[:3] + _cases()[4:])


def test_existing_freeze_is_not_replaced(tmp_path):
    target = tmp_path / "freeze.json"
    target.write_text("original")
    with pytest.raises(module.CapabilityGapInputError, match="must be new"):
        module.prepare_gap_inputs("missing", "missing", output=target)
    assert target.read_text() == "original"


def test_actual_router_is_called_in_ram_before_evidence(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    calls = []
    monkeypatch.setattr(module.db, "connect_read_only", lambda path: SimpleNamespace(
        backup=lambda ram: conn.backup(ram), close=lambda: None))
    actual = module.route_memory
    def route(ram, query, **kwargs):
        assert ram.execute("PRAGMA database_list").fetchone()[2] == ""
        calls.append(query.query_plan)
        return actual(ram, query, **kwargs)
    monkeypatch.setattr(module, "route_memory", route)
    query = {"mechanism_family": "HANDSHAKE_COMPLETION", "compatibility_profile": "rtl.fsm.single_guard.v1",
        "target_scope": "rtl_target"}
    routes, _ = module._actual_routes("unused", [{"case_id": "case", "role": "training",
        "query": module.MemoryQuery(query_plan=query).to_dict()}])
    assert calls == [query]
    assert routes["case"]["decision"] == "NO_SKILL"
    assert routes["case"]["no_skill_reason"] == "NO_MATCH"
    assert not routes["case"]["resolved_state_id"].startswith("gap-state:")


def test_actual_router_mismatch_is_not_handcrafted_as_no_match(tmp_tehm, monkeypatch):
    conn, _, _ = tmp_tehm
    monkeypatch.setattr(module.db, "connect_read_only", lambda path: SimpleNamespace(
        backup=lambda ram: conn.backup(ram), close=lambda: None))
    monkeypatch.setattr(module, "route_memory", lambda *args, **kwargs: SimpleNamespace(
        decision="CONSIDER", no_skill_reason=None))
    with pytest.raises(module.CapabilityGapInputError, match="actual current training route"):
        module._actual_routes("unused", [{"case_id": "case", "role": "training",
            "query": module.MemoryQuery(query_plan={}).to_dict()}])


@pytest.mark.parametrize("statement", [
    "UPDATE tehm_meta SET value='changed' WHERE key='schema_version'",
    "DROP TABLE tehm_meta",
])
def test_router_cannot_modify_source_rows_or_drop_schema_even_in_ram(tmp_tehm, monkeypatch, statement):
    conn, _, _ = tmp_tehm
    before = list(conn.iterdump())
    monkeypatch.setattr(module.db, "connect_read_only", lambda path: SimpleNamespace(
        backup=lambda ram: conn.backup(ram), close=lambda: None))
    def write(ram, *args, **kwargs):
        ram.execute(statement)
        raise AssertionError("forbidden write was accepted")
    monkeypatch.setattr(module, "route_memory", write)
    with pytest.raises(module.CapabilityGapInputError, match="forbidden"):
        module._actual_routes("unused", [{"case_id": "case", "role": "training",
            "query": module.MemoryQuery(query_plan={}).to_dict()}])
    assert list(conn.iterdump()) == before
