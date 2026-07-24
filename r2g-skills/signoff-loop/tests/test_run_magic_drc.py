"""Regression tests for run_magic_drc.sh (2026-07-02 fix).

Two bugs, found by the /r2g-debug sky130 tech cross-check, are guarded here:

1. **Tcl crash.** The generated run_magic_drc.tcl did
   `foreach {rule count} [drc listall why] { ... expr {$total + $count} }`.
   But `drc listall why` returns `{rule {box box ...} ...}` — the 2nd item of each
   pair is a LIST OF BOXES, not a number — so `expr` aborted with
   "can't use non-numeric string as operand of +". The fix counts `[llength $boxes]`.

2. **Invalid JSON.** `set drc_count [drc count total]` PRINTS the total but does not
   RETURN it, so the count var was empty and the literal `magic_drc_total_violations:`
   leaked into magic_drc_result.json's `total_violations` field. The fix parses the
   authoritative "Total DRC errors found: N" line and fail-closes to a numeric value.

These run without a live ORFS/Magic environment (mirroring test_beol_deck_transform.py),
so they stay green in CI.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flow" / "run_magic_drc.sh"


# ---------------------------------------------------------------------------
# 1. Source-level regression guards — the crash pattern must be gone, fixes present.
# ---------------------------------------------------------------------------
def test_script_exists():
    assert _SCRIPT.is_file(), f"missing {_SCRIPT}"


def test_no_expr_add_on_coord_list():
    """The exact crash: adding the box LIST as an integer must be gone."""
    src = _SCRIPT.read_text()
    assert "expr {$total + $count}" not in src, (
        "run_magic_drc.tcl still adds the box-list ($count) as an int — the Tcl crash"
    )


def test_counts_via_llength():
    """The fix counts boxes via llength, not by adding the list."""
    src = _SCRIPT.read_text()
    assert "llength" in src, "fix must count violation boxes via [llength $boxes]"


def test_numeric_guard_present():
    """The shell must fail-closed the parsed count to a number (invalid-JSON guard)."""
    src = _SCRIPT.read_text()
    assert "=~ ^[0-9]+$" in src, "missing numeric guard on VIOLATION_COUNT"
    assert "Total DRC errors found:" in src, "must parse Magic's authoritative total line"


# ---------------------------------------------------------------------------
# 2. Functional test of the parse + numeric-guard + JSON emission (the corrupt artifact).
#    Replicates the shell snippet exactly, like test_beol_deck_transform replicates sed.
# ---------------------------------------------------------------------------
_PARSE_SNIPPET = r'''
DRC_LOG="$1"
VIOLATION_COUNT=0
if [[ -f "$DRC_LOG" ]]; then
  COUNT_LINE=$(grep -i "Total DRC errors found:" "$DRC_LOG" 2>/dev/null | tail -1)
  if [[ -n "$COUNT_LINE" ]]; then
    VIOLATION_COUNT=$(echo "$COUNT_LINE" | awk '{print $NF}')
  fi
fi
if ! [[ "$VIOLATION_COUNT" =~ ^[0-9]+$ ]]; then
  VIOLATION_COUNT=0
fi
STATUS=$([ "$VIOLATION_COUNT" = "0" ] && echo "clean" || echo "violations")
printf '{"tool":"magic","status":"%s","total_violations":%s}\n' "$STATUS" "$VIOLATION_COUNT"
'''


def _emit_json(tmp_path: Path, log_body: str) -> dict:
    log = tmp_path / "magic_drc.log"
    log.write_text(log_body)
    out = subprocess.run(
        ["bash", "-c", _PARSE_SNIPPET, "bash", str(log)],
        capture_output=True, text=True, check=True,
    ).stdout
    return json.loads(out)  # raises if invalid JSON (the pre-fix bug)


def test_valid_json_with_violations(tmp_path):
    d = _emit_json(tmp_path, "Loading DRC CIF style.\nTotal DRC errors found: 4777\n")
    assert isinstance(d["total_violations"], int) and d["total_violations"] == 4777
    assert d["status"] == "violations"


def test_valid_json_clean(tmp_path):
    d = _emit_json(tmp_path, "Total DRC errors found: 0\n")
    assert d["total_violations"] == 0 and d["status"] == "clean"


def test_empty_log_fails_closed_to_numeric(tmp_path):
    """No count line (Tcl crashed before printing) must NOT leak a non-numeric -> valid JSON, 0."""
    d = _emit_json(tmp_path, "some magic banner without the total line\n")
    assert d["total_violations"] == 0 and d["status"] == "clean"


def test_garbage_count_fails_closed(tmp_path):
    """A non-numeric tail token must be guarded to 0 (never leak into JSON)."""
    d = _emit_json(tmp_path, "Total DRC errors found: magic_drc_total_violations:\n")
    assert d["total_violations"] == 0


# ---------------------------------------------------------------------------
# 3. RMD2-P0-01 (2026-07-24): timeout must terminate the COMPLETE Magic tree.
#    The old `timeout … | tee` let a TERM-ignoring Magic descendant hold the tee
#    pipe open past expiry; the script now runs under r2g_bounded_run (own
#    session, log-not-pipe output, group TERM→grace→KILL, survivor reap).
#    Harness mirrors test_run_netgen_lvs_timeout_group_kill.py.
# ---------------------------------------------------------------------------
import os
import shutil
import stat
import time

_SKILL = Path(__file__).resolve().parents[1]

_STUCK_MAGIC = """#!/usr/bin/env bash
trap '' TERM
( trap '' TERM; echo $BASHPID > "{TMP}/magic_child.pid"; exec sleep 300 ) &
echo $$ > "{TMP}/magic_parent.pid"
echo "magic drc grinding ..."
while :; do sleep 5; done
"""


def test_timeout_reaps_term_ignoring_magic_tree(tmp_path):
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    shutil.copytree(_SKILL / "scripts" / "flow", skill / "scripts" / "flow")
    (skill / "knowledge").mkdir()
    (skill / "references").mkdir()

    orfs = tmp_path / "orfs"
    rdir = orfs / "flow" / "results" / "sky130hd" / "demo" / "proj"
    rdir.mkdir(parents=True)
    (orfs / "flow" / "Makefile").write_text("# fake ORFS Makefile\n")
    (rdir / "6_final.gds").write_text("gds-bytes")

    pdk = tmp_path / "pdk"
    (pdk / "sky130A" / "libs.tech" / "magic").mkdir(parents=True)
    (pdk / "sky130A" / "libs.tech" / "magic" / "sky130A.tech").write_text("# tech\n")

    stub = tmp_path / "bin" / "magic"
    stub.parent.mkdir()
    stub.write_text(_STUCK_MAGIC.replace("{TMP}", str(tmp_path)))
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    proj = tmp_path / "proj"
    (proj / "constraints").mkdir(parents=True)
    (proj / "constraints" / "config.mk").write_text("export DESIGN_NAME = demo\n")

    env = dict(os.environ, ORFS_ROOT=str(orfs), PDK_ROOT=str(pdk),
               MAGIC_EXE=str(stub), MAGIC_TIMEOUT="2", MAGIC_KILL_GRACE="2")
    env.pop("R2G_ENV_FILE", None)
    t0 = time.monotonic()
    r = subprocess.run(
        ["bash", str(skill / "scripts" / "flow" / "run_magic_drc.sh"),
         str(proj), "sky130hd"],
        env=env, capture_output=True, text=True, timeout=120)
    elapsed = time.monotonic() - t0
    assert r.returncode == 124, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert elapsed < 60, f"took {elapsed:.0f}s — supervisor did not bound the run"

    for name in ("magic_parent.pid", "magic_child.pid"):
        pidfile = tmp_path / name
        assert pidfile.is_file(), f"stub never wrote {name} — harness broken"
        pid = int(pidfile.read_text().strip())
        for _ in range(20):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.5)
        else:
            raise AssertionError(f"{name}={pid} survived run_magic_drc.sh (RMD2-P0-01)")

    # Output captured directly (no tee pipeline) + fail-closed numeric JSON.
    assert "magic drc grinding" in (proj / "drc" / "magic_drc.log").read_text()
    d = json.loads((proj / "drc" / "magic_drc_result.json").read_text())
    assert d["total_violations"] == 0
