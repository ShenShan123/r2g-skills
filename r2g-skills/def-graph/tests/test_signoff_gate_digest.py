"""Artifact-digest binding in the signoff gate (RMD-P0-02, 2026-07-22).

Run-tag binding proves a report names the selected run's DIRECTORY; it cannot
see foreign bytes copied into the expected path. Each checker now records the
sha256 of the GDS it graded, and the gate compares every recorded digest with
the selected run's actual 6_final.gds:

  recorded == actual            -> bound
  recorded != actual            -> `mismatch` (HARD block)
  reports carry no digest       -> `unrecorded` caveat (research buildable,
                                   never strict/r2g_clean)
  unreadable .r2g_signoff_run   -> `unreadable_record` (HARD block, fail closed)
  no run GDS to compare         -> unknown (no claim, no caveat)
"""
import hashlib
import importlib.util
import json
import os
import sys

_FLOW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "flow")
_spec = importlib.util.spec_from_file_location(
    "signoff_gate_digest_mod", os.path.join(_FLOW, "signoff_gate.py"))
sg = importlib.util.module_from_spec(_spec)
sys.modules["signoff_gate_digest_mod"] = sg
_spec.loader.exec_module(sg)

CLEAN_STAGES = [{"stage": s, "status": 0, "elapsed_s": 1}
                for s in ("synth", "floorplan", "place", "cts", "route", "finish")]

GDS_BYTES = b"THE-REAL-LAYOUT"
GDS_SHA = hashlib.sha256(GDS_BYTES).hexdigest()


def _proj(tmp_path, *, drc_digest=None, lvs_digest=None, gds=GDS_BYTES,
          record=None):
    proj = tmp_path / "proj"
    rep = proj / "reports"
    rep.mkdir(parents=True)
    run = proj / "backend" / "RUN_2026-07-22_00-00-00"
    (run / "results").mkdir(parents=True)
    if gds is not None:
        (run / "results" / "6_final.gds").write_bytes(gds)
    drc = {"status": "clean", "total_violations": 0}
    if drc_digest:
        drc["gds_sha256"] = drc_digest
    json.dump(drc, open(rep / "drc.json", "w"))
    lvs = {"status": "clean", "mismatch_count": 0}
    if lvs_digest:
        lvs["gds_sha256"] = lvs_digest
    json.dump(lvs, open(rep / "lvs.json", "w"))
    json.dump({"status": "clean", "total_violations": 0}, open(rep / "route.json", "w"))
    with open(run / "stage_log.jsonl", "w") as f:
        for recd in CLEAN_STAGES:
            f.write(json.dumps(recd) + "\n")
    if record is not None:
        (proj / "backend" / ".r2g_signoff_run").write_text(record)
    return str(proj), str(run)


def test_matching_digest_is_bound(tmp_path):
    proj, run = _proj(tmp_path, drc_digest=GDS_SHA, lvs_digest=GDS_SHA)
    v = sg.evaluate(proj, run)
    assert v["checks"]["artifact_digest"]["status"] == "bound"
    assert "artifact_digest" not in v["blockers"]
    assert not any(c.startswith("artifact_digest") for c in v["caveats"])


def test_foreign_bytes_hard_block(tmp_path):
    """Acceptance: copying foreign GDS bytes into the expected run path cannot
    bypass the gate — the recorded digest differs and publication fails."""
    proj, run = _proj(tmp_path, drc_digest=GDS_SHA, lvs_digest=GDS_SHA,
                      gds=b"FOREIGN-BYTES-SWAPPED-IN")
    v = sg.evaluate(proj, run)
    assert v["checks"]["artifact_digest"]["status"] == "mismatch"
    assert "artifact_digest" in v["blockers"]
    assert v["status"] == "dirty"


def test_one_foreign_report_is_enough(tmp_path):
    proj, run = _proj(tmp_path, drc_digest=GDS_SHA,
                      lvs_digest=hashlib.sha256(b"other").hexdigest())
    v = sg.evaluate(proj, run)
    assert v["checks"]["artifact_digest"]["status"] == "mismatch"
    assert "artifact_digest" in v["blockers"]


def test_unrecorded_digest_is_caveat_not_block(tmp_path):
    """Legacy evidence (no digest) may build research tier but must never be an
    exact 'pass' — the strict r2g_clean tier requires digest-bound reports."""
    proj, run = _proj(tmp_path)
    v = sg.evaluate(proj, run)
    assert v["checks"]["artifact_digest"]["status"] == "unrecorded"
    assert "artifact_digest" not in v["blockers"]
    assert "artifact_digest=unrecorded" in v["caveats"]
    assert v["status"] == "pass_with_caveats"


def test_unreadable_record_hard_block(tmp_path):
    proj, run = _proj(tmp_path, drc_digest=GDS_SHA, lvs_digest=GDS_SHA,
                      record="{corrupt json")
    v = sg.evaluate(proj, run)
    assert v["checks"]["artifact_digest"]["status"] == "unreadable_record"
    assert "artifact_digest" in v["blockers"]
    assert v["status"] == "dirty"


def test_no_gds_no_claim(tmp_path):
    proj, run = _proj(tmp_path, gds=None)
    v = sg.evaluate(proj, run)
    assert v["checks"]["artifact_digest"]["status"] == "unknown"
    assert "artifact_digest" not in v["blockers"]
    assert not any(c.startswith("artifact_digest") for c in v["caveats"])


def test_provenance_envelope_digest_counts(tmp_path):
    """A digest carried only in the report's provenance envelope (the signoff
    record path, no checker-level field) still binds/mismatches."""
    proj, run = _proj(tmp_path)
    rep = os.path.join(proj, "reports", "drc.json")
    doc = json.load(open(rep))
    doc["provenance"] = {"run_tag": "RUN_2026-07-22_00-00-00",
                         "source": "signoff_record", "gds_sha256": GDS_SHA}
    json.dump(doc, open(rep, "w"))
    v = sg.evaluate(proj, run)
    assert v["checks"]["artifact_digest"]["status"] == "bound"
    doc["provenance"]["gds_sha256"] = hashlib.sha256(b"nope").hexdigest()
    json.dump(doc, open(rep, "w"))
    v = sg.evaluate(proj, run)
    assert v["checks"]["artifact_digest"]["status"] == "mismatch"
    assert "artifact_digest" in v["blockers"]
