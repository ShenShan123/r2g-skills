"""Six-stage lineage + strict tier in signoff_gate.py (round-2 pilot, 2026-07-21).

P0-4 — a repair-only generation (route+finish rerun) used to read ORFS 'complete'
because the gate only required a clean 'finish' row; synth..cts were absent from
its ledger and nothing proved which upstream artifacts it consumed. 'complete'
now requires a reconstructable six-stage lineage: the run's own ledger, a
RECORDED parent chain (resume_meta.json parent_lineage, written by run_orfs.sh at
resume time), or attribution via sibling ledgers (weaker: a recorded caveat).

P0-1 — strict tier: only the exact verdict 'pass' may build the clean tier;
pass_with_caveats blocks under --mode strict (exit 3).

H2 — the independent verifier reports BLOCKED/not_applicable (exit 3) for a
design whose graph generation was intentionally denied, instead of crashing with
FileNotFoundError on the absent manifest.
"""
import importlib.util
import json
import os
import subprocess
import sys

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
_GATE = os.path.join(_FLOW, "signoff_gate.py")
_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "tools")

_spec = importlib.util.spec_from_file_location("signoff_gate_lineage_mod", _GATE)
sg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sg)

CLEAN_STAGES = [{"stage": s, "status": 0, "elapsed_s": 1}
                for s in ("synth", "floorplan", "place", "cts", "route", "finish")]
REPAIR_STAGES = [{"stage": s, "status": 0, "elapsed_s": 1} for s in ("route", "finish")]


def _proj(tmp_path, *, lvs="clean", stage_log=CLEAN_STAGES, ppa=None):
    """Minimal clean project fixture (drc clean, lvs configurable, route clean)."""
    proj = tmp_path / "proj"
    rep = proj / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    run = proj / "backend" / "RUN_2026-07-01_00-00-00"
    run.mkdir(parents=True, exist_ok=True)
    json.dump({"status": "clean", "total_violations": 0}, open(rep / "drc.json", "w"))
    json.dump({"status": lvs, "mismatch_count": 0}, open(rep / "lvs.json", "w"))
    json.dump({"status": "clean", "total_violations": 0}, open(rep / "route.json", "w"))
    with open(run / "stage_log.jsonl", "w") as f:
        for rec in stage_log:
            f.write(json.dumps(rec) + "\n")
    if ppa is not None:
        json.dump(ppa, open(rep / "ppa.json", "w"))
    return str(proj), str(run)


def _add_run(proj, name, stages):
    run = os.path.join(proj, "backend", name)
    os.makedirs(run, exist_ok=True)
    with open(os.path.join(run, "stage_log.jsonl"), "w") as f:
        for rec in stages:
            f.write(json.dumps(rec) + "\n")
    return run


def test_repair_only_run_without_lineage_is_incomplete(tmp_path):
    """P0-4 core: a lone route+finish ledger with no sibling and no recorded
    parent chain must NOT read complete — finish alone is not completion."""
    proj, run = _proj(tmp_path, stage_log=REPAIR_STAGES,
                      ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    v = sg.evaluate(proj, run)
    assert v["checks"]["orfs"]["status"] == "incomplete"
    assert "orfs" in v["blockers"]
    assert "lineage" in v["checks"]["orfs"]["detail"] or \
        "lineage" in json.dumps(v["checks"]["orfs"])


def test_repair_only_run_reconstructed_from_sibling(tmp_path):
    """Pre-P0-4 resumes carry no recording: attribution via a sibling full run
    completes the lineage, but weakly — recorded as the orfs_lineage caveat."""
    proj, run = _proj(tmp_path, stage_log=REPAIR_STAGES,
                      ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    _add_run(proj, "RUN_2026-06-30_00-00-00", CLEAN_STAGES)
    v = sg.evaluate(proj, run)
    orfs = v["checks"]["orfs"]
    assert orfs["status"] == "complete", orfs
    assert orfs["lineage_quality"] == "reconstructed"
    assert set(orfs["lineage"]) == {"synth", "floorplan", "place", "cts"}
    assert "orfs_lineage=reconstructed" in v["caveats"]
    assert v["status"] == "pass_with_caveats"


def test_repair_only_run_with_recorded_parent_chain(tmp_path):
    """A post-P0-4 resume records parent_lineage in resume_meta.json: the chain
    is strong (consumed-artifact digests + named parent) — complete, no caveat."""
    proj, run = _proj(tmp_path, stage_log=REPAIR_STAGES,
                      ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    parent = "RUN_2026-06-30_00-00-00"
    _add_run(proj, parent, CLEAN_STAGES)
    lineage = {s: {"artifact": a, "sha256": "ab" * 32, "parent_run": parent}
               for s, a in (("synth", "1_synth.v"), ("floorplan", "2_floorplan.odb"),
                            ("place", "3_place.odb"), ("cts", "4_cts.odb"))}
    json.dump({"from_stage": "route", "reused_stages": list(lineage),
               "parent_lineage": lineage},
              open(os.path.join(run, "resume_meta.json"), "w"))
    v = sg.evaluate(proj, run)
    orfs = v["checks"]["orfs"]
    assert orfs["status"] == "complete" and orfs["lineage_quality"] == "recorded", orfs
    # No lineage caveat (the chain is strong). The two-run project still carries
    # the ORTHOGONAL report_binding=unknown caveat (unattributed reports with >1
    # backend run, P0-R7) — that one is legitimate and unrelated to lineage.
    assert "orfs_lineage=reconstructed" not in v["caveats"]
    assert "orfs" not in v["blockers"]


def test_full_ledger_still_complete_without_lineage(tmp_path):
    """A normal six-stage run needs no lineage machinery at all."""
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    v = sg.evaluate(proj, run)
    assert v["checks"]["orfs"]["status"] == "complete"
    assert "lineage" not in v["checks"]["orfs"]


# ---- strict tier (P0-1) ------------------------------------------------------

def _cli(proj, run, mode):
    return subprocess.run([sys.executable, _GATE, proj, "--run-dir", run,
                           "--mode", mode], capture_output=True, text=True, timeout=60)


def test_strict_mode_blocks_pass_with_caveats(tmp_path):
    """lvs=skipped is pass_with_caveats: buildable in enforce (research tier),
    BLOCKED in strict (exit 3) — the pilot's P0-1 exact complaint."""
    proj, run = _proj(tmp_path, lvs="skipped",
                      ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    r = _cli(proj, run, "enforce")
    assert r.returncode == 0, r.stderr
    r = _cli(proj, run, "strict")
    assert r.returncode == 3, (r.returncode, r.stderr)
    assert "strict tier requires exact 'pass'" in r.stderr


def test_strict_mode_passes_exact_pass(tmp_path):
    proj, run = _proj(tmp_path, ppa={"summary": {"timing": {"setup_wns": 0.05}}})
    r = _cli(proj, run, "strict")
    assert r.returncode == 0, r.stderr


# ---- verifier blocked/not_applicable (H2) -----------------------------------

def test_verifier_reports_blocked_not_filenotfound(tmp_path):
    """A design whose graph generation was intentionally denied has no
    dataset/graph_manifest.json — the verifier must report BLOCKED (exit 3),
    never raise FileNotFoundError (pilot H2)."""
    case = tmp_path / "denied"
    (case / "reports").mkdir(parents=True)
    json.dump({"design": "denied", "platform": "nangate45", "variants": {},
               "status": "skipped", "reason": "signoff gate: not signed off"},
              open(case / "reports" / "graph_dataset.json", "w"))
    r = subprocess.run([sys.executable, os.path.join(_TOOLS, "verify_graph_dataset.py"),
                        str(case)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    assert "BLOCKED" in r.stdout and "FileNotFoundError" not in r.stderr
