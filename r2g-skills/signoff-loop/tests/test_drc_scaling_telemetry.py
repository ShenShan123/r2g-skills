"""LIM-HO-01 (held-out 2026-07-26): bounded full-deck DRC runs must be
CHARACTERIZABLE — design scale, GDS size, wall time, peak checker memory, last
active rule, and the configured timeout ride the verdict into reports/drc.json.
The bound and the stuck/incomplete classification are unchanged (this is an
investigation aid, never an acceptance relaxation)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
BOUNDED = SKILL / "scripts" / "flow" / "_bounded_run.sh"
EXTRACT_DRC = SKILL / "scripts" / "extract" / "extract_drc.py"


def test_bounded_run_samples_peak_rss(tmp_path):
    # A ~50MB, ~3s python child: the 1s supervision tick must sample its RSS.
    script = ("source '%s'\n"
              "r2g_bounded_run 30 5 '%s' python3 -c '"
              "import time; b = bytearray(50*1024*1024); time.sleep(3)'\n"
              "rc=$?\n"
              "echo \"peak=$_R2G_BOUNDED_PEAK_RSS_KB rc=$rc\"\n"
              % (BOUNDED, tmp_path / "out.log"))
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    peak = int(r.stdout.split("peak=")[1].split()[0])
    assert peak > 40 * 1024, r.stdout   # >40MB observed


def test_extract_drc_carries_scaling_telemetry(tmp_path):
    proj = tmp_path / "proj"
    drc_dir = proj / "drc"
    drc_dir.mkdir(parents=True)
    (proj / "reports").mkdir()
    # A stuck verdict as run_drc.sh writes it, with the LIM-HO-01 quad.
    (drc_dir / "drc_result.json").write_text(json.dumps({
        "status": "stuck", "reason": "klayout_polygon_op_no_progress",
        "stuck_at_rule": "FreePDK45.lydrc:131", "timeout_s": 7200,
        "exit_code": 124, "drc_mode": "full",
        "cell_count": 21261, "gds_bytes": 123456789,
        "wall_s": 7260, "peak_rss_kb": 8388608,
    }))
    out = proj / "reports" / "drc.json"
    r = subprocess.run([sys.executable, str(EXTRACT_DRC), str(proj), str(out)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    d = json.loads(out.read_text())
    assert d["status"] == "stuck"                  # classification unchanged
    assert d["stuck_at_rule"] == "FreePDK45.lydrc:131"
    assert d["cell_count"] == 21261
    assert d["gds_bytes"] == 123456789
    assert d["wall_s"] == 7260
    assert d["peak_rss_kb"] == 8388608
    assert d["timeout_s"] == 7200
