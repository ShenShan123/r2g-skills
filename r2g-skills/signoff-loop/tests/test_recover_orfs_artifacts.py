import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flow" / "recover_orfs_artifacts.py"
SPEC = importlib.util.spec_from_file_location("recover_orfs_artifacts", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MOD)


def _case(tmp_path: Path, *, finish_status: int = 0):
    project = tmp_path / "aes_safe_u20"
    constraints = project / "constraints"
    constraints.mkdir(parents=True)
    constraints.joinpath("config.mk").write_text(
        "export DESIGN_NICKNAME = aes\n"
        "export DESIGN_NAME = aes_cipher_top\n"
        "export PLATFORM = sky130hd\n")
    run = project / "backend" / "RUN_A"
    run.mkdir(parents=True)
    run.joinpath("run-meta.json").write_text(json.dumps({"make_status": 0}))
    rows = [{"stage": stage, "status": finish_status if stage == "finish" else 0}
            for stage in MOD.REQUIRED_STAGES]
    run.joinpath("stage_log.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    flow = tmp_path / "flow"
    for kind in ("results", "logs", "objects", "reports"):
        root = flow / kind / "sky130hd" / "aes" / project.name
        root.mkdir(parents=True)
        root.joinpath(f"{kind}.txt").write_text(kind)
    results = flow / "results" / "sky130hd" / "aes" / project.name
    for name in MOD.FINAL_FILES:
        results.joinpath(name).write_bytes(("real-" + name).encode())
    return project, flow, run


def test_recovers_nickname_workspace_with_provenance(tmp_path):
    project, flow, run = _case(tmp_path)
    result = MOD.recover(project, flow)
    assert result["design_name"] == "aes_cipher_top"
    assert result["design_nickname"] == "aes"
    assert (run / "results" / "6_final.gds").read_bytes() == b"real-6_final.gds"
    assert (run / "final" / "6_final.gds").is_file()
    assert (run / "objects" / "objects.txt").is_file()
    meta = json.loads((run / "run-meta.json").read_text())
    assert meta["orfs_results"].endswith("/results/sky130hd/aes/aes_safe_u20")
    assert meta["artifact_recovery"] == "artifact-recovery.json"


def test_refuses_incomplete_stage_journal(tmp_path):
    project, flow, _run = _case(tmp_path, finish_status=1)
    try:
        MOD.recover(project, flow)
    except RuntimeError as exc:
        assert "finish" in str(exc)
    else:
        raise AssertionError("failed stage must block recovery")
