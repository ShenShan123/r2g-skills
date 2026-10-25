"""Digest-complete repair/resume lineage in signoff_gate.py (RMD2-P0-02).

Three-platform pilot 2026-07-24: Nangate45 I2C's repair run recorded
``synth.sha256=null`` against a nonexistent canonical artifact (``1_synth.v``),
and the gate accepted the parent when its stage ledger was clean — reporting
``lineage_quality=recorded`` / ``overall pass`` over digest-incomplete
provenance, and graph generation succeeded. The gate must now independently
verify every recorded reused stage: valid non-null sha256, existing
same-identity parent with a matching stage-manifest digest, acyclic chain, and
preserved artifact bytes that still hash to the recorded digest. Any failure is
a hard blocker (never a caveat), and the verdict carries a lineage root digest
that rides the graph manifest via signoff_health.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from lvs_fixture import add_lvs_evidence

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
_GATE = os.path.join(_FLOW, "signoff_gate.py")

_spec = importlib.util.spec_from_file_location("signoff_gate_lineage_digest_mod", _GATE)
sg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sg)

STAGES6 = ("synth", "floorplan", "place", "cts", "route", "finish")
REUSED = ("synth", "floorplan", "place", "cts")
IDENTITY = {"design_name": "demo", "platform": "nangate45", "flow_variant": "proj"}
PARENT = "RUN_2026-07-20_00-00-00"
CHILD = "RUN_2026-07-21_00-00-00"


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_stage_log(run, stages):
    with open(os.path.join(run, "stage_log.jsonl"), "w") as f:
        for s in stages:
            f.write(json.dumps({"stage": s, "status": 0, "elapsed_s": 1}) + "\n")


def _repair_project(tmp_path):
    """A COMPLETE, verifiable repair fixture: parent full run with stage-manifest
    digests; child route+finish rerun preserving the reused artifacts byte-for-
    byte, with a fully recorded resume_meta parent_lineage."""
    proj = tmp_path / "proj"
    rep = proj / "reports"
    rep.mkdir(parents=True)
    json.dump({"status": "clean", "total_violations": 0}, open(rep / "drc.json", "w"))
    json.dump({"status": "clean", "mismatch_count": 0}, open(rep / "lvs.json", "w"))
    json.dump({"status": "clean", "total_violations": 0}, open(rep / "route.json", "w"))
    json.dump({"summary": {"timing": {"setup_wns": 0.05}}}, open(rep / "ppa.json", "w"))

    parent = proj / "backend" / PARENT
    parent.mkdir(parents=True)
    _write_stage_log(str(parent), STAGES6)
    json.dump(dict(IDENTITY), open(parent / "run-meta.json", "w"))

    child = proj / "backend" / CHILD
    (child / "results").mkdir(parents=True)
    _write_stage_log(str(child), ("route", "finish"))
    json.dump(dict(IDENTITY), open(child / "run-meta.json", "w"))

    lineage = {}
    with open(parent / "stage_artifact_manifest.jsonl", "w") as mf:
        for stage in REUSED:
            art = sg.STAGE_ARTIFACT[stage]
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
                              "parent_run": PARENT, "parent_sha256": digest,
                              "source": "recorded", "verified": True}
    json.dump({"from_stage": "route", "reused_stages": list(REUSED),
               "parent_lineage": lineage, "stage_contract_version": 2,
               "platform": IDENTITY["platform"], "design": IDENTITY["design_name"],
               "flow_variant": IDENTITY["flow_variant"]},
              open(child / "resume_meta.json", "w"))
    add_lvs_evidence(proj, child)
    return proj, child, lineage


def _edit_meta(child, mutate):
    path = os.path.join(child, "resume_meta.json")
    meta = json.load(open(path))
    mutate(meta)
    json.dump(meta, open(path, "w"))


# ---- acceptance 1 + 8: a valid repair passes with a verified root digest -----

def test_valid_repair_lineage_verifies(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    v = sg.evaluate(str(proj), str(child))
    orfs = v["checks"]["orfs"]
    assert orfs["status"] == "complete", orfs
    assert orfs["lineage_quality"] == "recorded"
    assert "orfs" not in v["blockers"]
    assert "orfs_lineage=reconstructed" not in v["caveats"]
    # The manifest-facing evidence digest binds the verified lineage.
    assert sg._is_sha256(orfs["lineage_root_digest"])
    assert orfs["stage_contract_version"] == 2
    assert all(orfs["lineage"][s]["verified"] for s in REUSED)


# ---- acceptance 2: sha256=null blocks graph generation -----------------------

def test_null_sha256_blocks(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    _edit_meta(child, lambda m: m["parent_lineage"]["synth"].update(sha256=None))
    v = sg.evaluate(str(proj), str(child))
    orfs = v["checks"]["orfs"]
    assert orfs["status"] == "incomplete", orfs
    assert "orfs" in v["blockers"]
    assert any("synth" in viol and "digest" in viol
               for viol in orfs["lineage_violations"])


# ---- the exact pilot defect: a wrong canonical artifact name is rejected -----

def test_wrong_canonical_artifact_name_blocks(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    _edit_meta(child, lambda m: m["parent_lineage"]["synth"].update(artifact="1_synth.v"))
    v = sg.evaluate(str(proj), str(child))
    assert v["checks"]["orfs"]["status"] == "incomplete"
    assert "orfs" in v["blockers"]
    assert any("1_synth.v" in viol for viol in v["checks"]["orfs"]["lineage_violations"])


# ---- acceptance 4: mutating a reused ODB after recording blocks --------------

def test_mutated_reused_artifact_blocks(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    art = sg.STAGE_ARTIFACT["floorplan"]
    (child / "results" / art).write_bytes(b"tampered-after-lineage-recording")
    v = sg.evaluate(str(proj), str(child))
    assert v["checks"]["orfs"]["status"] == "incomplete"
    assert "orfs" in v["blockers"]
    assert any("mutated" in viol for viol in v["checks"]["orfs"]["lineage_violations"])


def test_missing_preserved_artifact_blocks(tmp_path):
    """Bytes that cannot be re-hashed are unverifiable — fail closed, never a
    silent pass on 'the digest was recorded once'."""
    proj, child, _ = _repair_project(tmp_path)
    os.unlink(child / "results" / sg.STAGE_ARTIFACT["place"])
    v = sg.evaluate(str(proj), str(child))
    assert v["checks"]["orfs"]["status"] == "incomplete"
    assert any("not preserved" in viol
               for viol in v["checks"]["orfs"]["lineage_violations"])


# ---- acceptance 5: foreign parent (design/platform/variant) rejected ---------

def test_foreign_platform_parent_blocks(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    parent_meta = os.path.join(proj, "backend", PARENT, "run-meta.json")
    doc = json.load(open(parent_meta))
    doc["platform"] = "sky130hd"
    json.dump(doc, open(parent_meta, "w"))
    v = sg.evaluate(str(proj), str(child))
    assert v["checks"]["orfs"]["status"] == "incomplete"
    assert any("DIFFERENT platform" in viol
               for viol in v["checks"]["orfs"]["lineage_violations"])


# ---- acceptance 6: cycles and self-parents rejected --------------------------

def test_self_parent_blocks(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    _edit_meta(child, lambda m: m["parent_lineage"]["synth"].update(parent_run=CHILD))
    v = sg.evaluate(str(proj), str(child))
    assert v["checks"]["orfs"]["status"] == "incomplete"
    assert any("cycle" in viol for viol in v["checks"]["orfs"]["lineage_violations"])


def test_parent_chain_cycle_blocks(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    # Give the parent its own resume_meta whose synth parent points back at the
    # child: child -> parent -> child is a cycle.
    parent = os.path.join(proj, "backend", PARENT)
    json.dump({"parent_lineage": {"synth": {"parent_run": CHILD}}},
              open(os.path.join(parent, "resume_meta.json"), "w"))
    v = sg.evaluate(str(proj), str(child))
    assert v["checks"]["orfs"]["status"] == "incomplete"
    assert any("cycle" in viol for viol in v["checks"]["orfs"]["lineage_violations"])


def test_nonexistent_parent_blocks(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    _edit_meta(child, lambda m: m["parent_lineage"]["cts"].update(
        parent_run="RUN_1999-01-01_00-00-00"))
    v = sg.evaluate(str(proj), str(child))
    assert v["checks"]["orfs"]["status"] == "incomplete"
    assert any("does not exist" in viol
               for viol in v["checks"]["orfs"]["lineage_violations"])


# ---- acceptance 7: legacy reconstructed-only can never build strict clean ----

def test_legacy_reconstruction_blocked_from_strict(tmp_path):
    proj, child, _ = _repair_project(tmp_path)
    os.unlink(child / "resume_meta.json")   # pre-P0-4: no recording at all
    v = sg.evaluate(str(proj), str(child))
    orfs = v["checks"]["orfs"]
    assert orfs["status"] == "complete" and orfs["lineage_quality"] == "reconstructed"
    assert "orfs_lineage=reconstructed" in v["caveats"]
    assert v["status"] == "pass_with_caveats"
    r = subprocess.run([sys.executable, _GATE, str(proj), "--run-dir", str(child),
                        "--mode", "strict"], capture_output=True, text=True, timeout=60)
    assert r.returncode == 3, (r.returncode, r.stderr)
    assert "strict tier requires exact 'pass'" in r.stderr


# ---- contract sync: the gate's fallback copy must equal the recorder's -------

def test_stage_contract_synced_with_signoff_loop():
    src = os.path.normpath(os.path.join(
        _FLOW, "..", "..", "..", "signoff-loop", "scripts", "flow",
        "stage_artifacts.py"))
    assert os.path.isfile(src), "signoff-loop stage_artifacts.py missing"
    spec = importlib.util.spec_from_file_location("stage_artifacts_src", src)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert sg._FALLBACK_STAGE_ARTIFACT == mod.STAGE_ARTIFACT
    assert sg._FALLBACK_STAGE_CONTRACT_VERSION == mod.STAGE_CONTRACT_VERSION
    # The active contract (whichever import path won) matches too.
    assert sg.STAGE_ARTIFACT == mod.STAGE_ARTIFACT
    # The pilot's exact defect can never come back silently:
    assert sg.STAGE_ARTIFACT["synth"] == "1_synth.odb"
