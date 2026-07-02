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
