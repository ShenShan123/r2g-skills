"""RMD3-P1-01 / P1-HO-01 (failure-patterns.md #58): ONE effective-stage resolver.

A digest-verified FROM_STAGE resume must resolve to the SAME completion status
in the def-graph FLOW gate, ppa.json (extract_ppa), and the knowledge store
(ingest_run / repair_run_status). Reproduced on sky130hs SHA-256 (fixed pilot)
and sky130hs AES (held-out): FLOW said complete, LEARNING said partial.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import ingest_run
import knowledge_db
import repair_run_status

SKILL = Path(__file__).resolve().parents[1]
FLOW = SKILL / "scripts" / "flow"
EXTRACT_PPA = SKILL / "scripts" / "extract" / "extract_ppa.py"
DEF_GRAPH_GATE = SKILL.parent / "def-graph" / "scripts" / "flow" / "signoff_gate.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


es = _load(FLOW / "effective_stages.py", "effective_stages_test_mod")

STAGES6 = ("synth", "floorplan", "place", "cts", "route", "finish")
REUSED = ("synth",)                     # the held-out AES case: floorplan resume
RERUN = ("floorplan", "place", "cts", "route", "finish")
IDENTITY = {"design_name": "demo", "platform": "sky130hs", "flow_variant": "proj"}
PARENT = "RUN_2026-07-25_00-00-00"
CHILD = "RUN_2026-07-26_00-00-00"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_stage_log(run: Path, stages):
    with open(run / "stage_log.jsonl", "w") as f:
        for s in stages:
            f.write(json.dumps({"stage": s, "status": 0, "elapsed_s": 1}) + "\n")


def _resume_project(tmp_path) -> tuple[Path, Path]:
    """The held-out sky130hs AES shape: a full parent run, then a density_relief
    resume from floorplan whose ledger holds floorplan..finish only, with synth
    consumed from the parent under a fully recorded, byte-verified lineage."""
    proj = tmp_path / "proj"
    (proj / "reports").mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = sky130hs\n")

    parent = proj / "backend" / PARENT
    parent.mkdir(parents=True)
    _write_stage_log(parent, STAGES6)
    json.dump(dict(IDENTITY), open(parent / "run-meta.json", "w"))

    child = proj / "backend" / CHILD
    (child / "results").mkdir(parents=True)
    _write_stage_log(child, RERUN)
    json.dump(dict(IDENTITY), open(child / "run-meta.json", "w"))
    (child / "results" / "6_final.def").write_text("DESIGN demo ;\n")

    lineage = {}
    with open(parent / "stage_artifact_manifest.jsonl", "w") as mf:
        for stage in REUSED:
            art = es.STAGE_ARTIFACT[stage]
            payload = f"bytes-of-{art}".encode()
            (child / "results" / art).write_bytes(payload)
            digest = _sha(payload)
            mf.write(json.dumps({
                "schema_version": 1, "stage_contract_version": 2, "stage": stage,
                "status": 0, "run_tag": PARENT, "artifact": art,
                "sha256": digest, "size": len(payload),
                "platform": IDENTITY["platform"], "design": IDENTITY["design_name"],
                "flow_variant": IDENTITY["flow_variant"]}) + "\n")
            lineage[stage] = {"artifact": art, "sha256": digest,
                              "parent_run": PARENT, "source": "recorded"}
    json.dump({"from_stage": "floorplan", "reused_stages": list(REUSED),
               "parent_lineage": lineage, "stage_contract_version": 2,
               "platform": IDENTITY["platform"], "design": IDENTITY["design_name"],
               "flow_variant": IDENTITY["flow_variant"]},
              open(child / "resume_meta.json", "w"))
    # The resume happened AFTER the parent run; same-second fixture mtimes would
    # let newest-run selection (ingest/extract) pick either arbitrarily.
    import os as _os
    import time as _time
    now = _time.time()
    _os.utime(parent, (now - 100, now - 100))
    _os.utime(child, (now, now))
    return proj, child


# ── the resolver itself ──────────────────────────────────────────────────────

def test_resolver_complete_via_recorded_lineage(tmp_path):
    proj, child = _resume_project(tmp_path)
    res = es.resolve(str(child))
    assert res["status"] == "complete"
    assert res["lineage_quality"] == "recorded"
    assert res["lineage"]["synth"]["verified"] is True
    assert res["lineage_root_digest"]
    assert res["resolver_version"] == es.STAGE_RESOLVER_VERSION
    # Local-only history preserved separately for audit.
    assert set(res["local_stages"]) == set(RERUN)


def test_resolver_fail_closed_on_tampered_bytes(tmp_path):
    proj, child = _resume_project(tmp_path)
    art = es.STAGE_ARTIFACT["synth"]
    (child / "results" / art).write_bytes(b"MUTATED")
    res = es.resolve(str(child))
    assert res["status"] == "partial"
    assert res["lineage_violations"]
    assert es.effective_upgrade(str(child), "partial") is None


def test_resolver_never_upgrades_a_failed_run(tmp_path):
    proj, child = _resume_project(tmp_path)
    with open(child / "stage_log.jsonl", "a") as f:
        f.write(json.dumps({"stage": "route", "status": 2}) + "\n")
    res = es.resolve(str(child))
    assert res["status"] == "fail" and res["fail_stage"] == "route"
    assert es.effective_upgrade(str(child), "partial") is None  # not partial


def test_synth_only_scope_is_exempt(tmp_path):
    proj, child = _resume_project(tmp_path)
    assert es.effective_upgrade(str(child), "partial", "synth_only") is None


def test_full_local_run_reads_complete_local(tmp_path):
    proj, child = _resume_project(tmp_path)
    parent = proj / "backend" / PARENT
    res = es.resolve(str(parent))
    assert res["status"] == "complete" and res["lineage_quality"] == "local"


# ── consumer agreement (the RMD3-P1-01 acceptance) ───────────────────────────

def test_extract_ppa_reads_complete_with_evidence(tmp_path):
    proj, child = _resume_project(tmp_path)
    out = proj / "reports" / "ppa.json"
    r = subprocess.run([sys.executable, str(EXTRACT_PPA), str(proj), str(out)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout, r.stderr)
    ppa = json.loads(out.read_text())
    assert ppa["orfs_status"] == "complete", ppa
    assert ppa["orfs_fail_stage"] is None
    eff = ppa["orfs_effective"]
    assert eff["resolver_version"] == es.STAGE_RESOLVER_VERSION
    assert eff["lineage_quality"] == "recorded"
    assert eff["inherited_stages"] == ["synth"]


def test_flow_gate_ppa_and_learning_agree(tmp_path):
    """The acceptance condition: FLOW (def-graph gate), PPA, ingestion, and a
    later repair pass all resolve the same digest-bound resume as complete."""
    proj, child = _resume_project(tmp_path)
    # FLOW: the def-graph gate's orfs check.
    sg = _load(DEF_GRAPH_GATE, "signoff_gate_agreement_mod")
    orfs = sg._check_orfs(str(child))
    assert orfs["status"] == "complete", orfs
    assert orfs["lineage_quality"] == "recorded"
    # PPA:
    out = proj / "reports" / "ppa.json"
    subprocess.run([sys.executable, str(EXTRACT_PPA), str(proj), str(out)],
                   check=True, capture_output=True, timeout=120)
    assert json.loads(out.read_text())["orfs_status"] == "complete"
    # LEARNING (ingest -> runs.orfs_status):
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(conn)
    run_id = ingest_run.ingest(proj, conn)
    (status,) = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert status == "pass", status
    # No phantom backend-failure event for a complete run (H2 stays intact).
    n_fail = conn.execute(
        "SELECT COUNT(*) FROM failure_events WHERE run_id=? "
        "AND signature LIKE 'orfs-fail-%'", (run_id,)).fetchone()[0]
    assert n_fail == 0
    # A reconciliation pass must NOT re-downgrade the lineage-complete resume.
    conn.commit()
    changed = repair_run_status.repair(tmp_path, conn)
    (status2,) = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert status2 == "pass", (changed, status2)
    conn.close()


def test_invalid_lineage_fails_closed_everywhere(tmp_path):
    proj, child = _resume_project(tmp_path)
    art = es.STAGE_ARTIFACT["synth"]
    (child / "results" / art).write_bytes(b"MUTATED")
    # FLOW blocks (incomplete + violations recorded):
    sg = _load(DEF_GRAPH_GATE, "signoff_gate_tamper_mod")
    orfs = sg._check_orfs(str(child))
    assert orfs["status"] == "incomplete"
    assert orfs.get("lineage_violations")
    # PPA keeps the honest partial:
    out = proj / "reports" / "ppa.json"
    subprocess.run([sys.executable, str(EXTRACT_PPA), str(proj), str(out)],
                   check=True, capture_output=True, timeout=120)
    ppa = json.loads(out.read_text())
    assert ppa["orfs_status"] == "partial" and "orfs_effective" not in ppa
    # LEARNING keeps partial too:
    conn = knowledge_db.connect(tmp_path / "k.sqlite")
    knowledge_db.ensure_schema(conn)
    run_id = ingest_run.ingest(proj, conn)
    (status,) = conn.execute(
        "SELECT orfs_status FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert status == "partial"
    conn.close()
