"""build_signoff_manifest.py — the strict signoff evidence binding (pilot P0-2/H3).

The round-2 pilot failed every CONSTRAINT/SIGNOFF gate because route.json /
rcx.json / timing_check.json were never emitted and nothing bound the evidence:
Fmax winners stayed placement proxies. The manifest must (a) enumerate what is
missing — including the absent FINAL timing confirmation (H3), never just the
matching proxy/SDC periods — (b) call the bundle strict-clean only when every
subject is clean, and (c) bind SDC digest + winner + confirming run identity.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import build_signoff_manifest as bsm

MOD = Path(bsm.__file__).resolve()


def _proj(tmp_path, *, drc="clean", lvs="clean", route=0, rcx="complete",
          tier="clean", winner=1.0243910000000003, stamped="1.02439",
          omit=()):
    proj = tmp_path / "proj"
    rep = proj / "reports"
    rep.mkdir(parents=True)
    (proj / "constraints").mkdir()
    (proj / "constraints" / "config.mk").write_text(
        "export DESIGN_NAME = demo\nexport PLATFORM = nangate45\n")
    (proj / "constraints" / "constraint.sdc").write_text(
        f"set clk_period {stamped}\n")
    run = proj / "backend" / "RUN_2026-07-21_00-00-00" / "results"
    run.mkdir(parents=True)
    (run / "6_final.def").write_text("DESIGN demo ;\n")
    docs = {
        "drc.json": {"status": drc, "total_violations": 0},
        "lvs.json": {"status": lvs, "mismatch_count": 0},
        "route.json": {"status": "clean" if route == 0 else "fail",
                       "total_violations": route},
        "rcx.json": {"status": rcx},
        "timing_check.json": {"tier": tier, "wns": 0.01},
        "ppa.json": {"summary": {"timing": {"setup_wns": 0.01}}},
        "fmax_search.json": {"status": "ok", "winner": {"period": winner}},
    }
    for fn, doc in docs.items():
        if fn not in omit:
            json.dump(doc, open(rep / fn, "w"))
    return proj


def test_clean_bundle_is_strict_and_qualified(tmp_path):
    man = bsm.build(str(_proj(tmp_path)))
    assert man["strict_clean"] and not man["strict_missing"], man["strict_missing"]
    c = man["constraint"]
    # The pilot's rounding case: stamped 1.02439 vs winner 1.0243910000000003
    # is a MATCH (the stamp rounds), and with a clean final tier -> qualified.
    assert c["period_match"] is True and c["qualified"] is True, c
    assert man["confirming_run"]["run_tag"] == "RUN_2026-07-21_00-00-00"
    assert man["confirming_run"]["def_sha256"]
    assert man["reports"]["drc.json"]["sha256"]


def test_missing_final_timing_is_enumerated(tmp_path):
    """H3: the failure must NAME the absent final-timing confirmation."""
    man = bsm.build(str(_proj(tmp_path, omit=("timing_check.json",))))
    c = man["constraint"]
    assert c["qualified"] is False
    assert any("FINAL timing confirmation" in m for m in c["missing"]), c["missing"]
    assert not man["strict_clean"]


def test_lvs_skipped_is_not_strict(tmp_path):
    """P0-2: 'skipped' LVS never counts as strict-clean."""
    man = bsm.build(str(_proj(tmp_path, lvs="skipped")))
    assert not man["strict_clean"]
    assert any(m.startswith("lvs:") for m in man["strict_missing"])


def test_missing_route_and_rcx_block_strict(tmp_path):
    man = bsm.build(str(_proj(tmp_path, omit=("route.json", "rcx.json"))))
    missing = "\n".join(man["strict_missing"])
    assert "route:" in missing and "rcx:" in missing


def test_period_mismatch_disqualifies(tmp_path):
    man = bsm.build(str(_proj(tmp_path, winner=2.0, stamped="1.0")))
    c = man["constraint"]
    assert c["period_match"] is False and c["qualified"] is False


def test_cli_writes_manifest_and_strict_exit(tmp_path):
    proj = _proj(tmp_path, drc="fail")
    r = subprocess.run([sys.executable, str(MOD), str(proj), "--strict"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    out = json.load(open(proj / "reports" / "signoff_manifest.json"))
    assert out["strict_clean"] is False
    proj2 = _proj(tmp_path / "ok")
    r = subprocess.run([sys.executable, str(MOD), str(proj2), "--strict"],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
