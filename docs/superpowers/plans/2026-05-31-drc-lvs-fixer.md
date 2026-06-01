# DRC/LVS Violation-Fixing Ability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real-layout-fix DRC/LVS violation-fixing ability to the `r2g-rtl2gds` skill — diagnose a signoff violation, apply a genuine layout/config fix, re-route, re-check, iterate ≤3× with early-exit — then validate it on the corpus.

**Architecture:** A pure/testable Python diagnoser (`diagnose_signoff_fix.py`) turns `reports/{drc,lvs}.json` + `config.mk` into an ordered fix-plan and can `--apply` a strategy into a marked `config.mk` block; a bash loop driver (`fix_signoff.sh`) orchestrates re-run + re-check + logging, with injectable command seams for testing. Honest 300:1 antenna deck (no rule relaxation). Spec: `docs/superpowers/specs/2026-05-31-drc-lvs-fixer-design.md`.

**Tech Stack:** Python 3.10+ (stdlib only), bash, pytest, OpenROAD-flow-scripts (ORFS) make targets, KLayout DRC/LVS.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py` | Pure violation→fix-plan logic + CLI (`--apply`, `--next`) | Create |
| `r2g-rtl2gds/scripts/flow/fix_signoff.sh` | Iterate diagnose→apply→re-run→re-check; log on-the-fly | Create |
| `r2g-rtl2gds/tests/test_diagnose_signoff_fix.py` | Unit tests (build_plan, apply, next) + driver integration test | Create |
| `r2g-rtl2gds/tests/conftest.py` | Add `scripts/reports` to sys.path | Modify |
| `tools/install_nangate45_drc.sh` | Install honest 300:1 deck into ORFS, verify ratio | Create |
| `r2g-rtl2gds/assets/platforms/nangate45/drc/FreePDK45.lydrc` | Restore to 300:1 | Modify |
| `r2g-rtl2gds/references/signoff-fixing.md` | Fixer workflow, strategy catalog, schemas | Create |
| `r2g-rtl2gds/SKILL.md` | Wire fixer into signoff workflow | Modify |
| `r2g-rtl2gds/references/failure-patterns.md` | Cross-ref fixer; retire 400:1 | Modify |
| `CLAUDE.md` | "Where to find X" row | Modify |

---

## Task 1: Honest 300:1 antenna deck + installer

**Files:**
- Modify: `r2g-rtl2gds/assets/platforms/nangate45/drc/FreePDK45.lydrc`
- Create: `tools/install_nangate45_drc.sh`

- [ ] **Step 1: Restore the skill-asset deck to 300:1**

Run (rewrites the 10 antenna_check lines in-place; ratio + the `: NNN:1` label):
```bash
cd /proj/workarea/user5/agent-r2g
f=r2g-rtl2gds/assets/platforms/nangate45/drc/FreePDK45.lydrc
sed -i -E 's/(antenna_check\(gate, metal[0-9]+, )400\.0/\1300.0/; s/area : 400:1/area : 300:1/' "$f"
grep -c 'antenna_check(gate, metal.*300.0' "$f"   # expect 10
grep -c '400' "$f"                                 # expect 0 antenna refs
```
Expected: `10` then `0`.

- [ ] **Step 2: Restore the ORFS-installed deck from its 300:1 backup**

```bash
cd /proj/workarea/user5/agent-r2g
source r2g-rtl2gds/scripts/flow/_env.sh >/dev/null 2>&1
cp "$FLOW_DIR/platforms/nangate45/drc/FreePDK45.lydrc.orig-300ratio" \
   "$FLOW_DIR/platforms/nangate45/drc/FreePDK45.lydrc"
grep -c 'antenna_check(gate, metal.*300.0' "$FLOW_DIR/platforms/nangate45/drc/FreePDK45.lydrc"  # expect 10
```
Expected: `10`.

- [ ] **Step 3: Create the installer** `tools/install_nangate45_drc.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
# Install the honest 300:1 FreePDK45 antenna DRC deck into the ORFS checkout.
# Real-fixes-only policy: we do NOT relax the antenna ratio to mask violations.
# Mirrors tools/install_nangate45_lvs.sh.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
# shellcheck source=/dev/null
source "$REPO/r2g-rtl2gds/scripts/flow/_env.sh" >/dev/null 2>&1 || true
: "${FLOW_DIR:?ORFS FLOW_DIR not found; set ORFS_ROOT}"
SRC="$REPO/r2g-rtl2gds/assets/platforms/nangate45/drc/FreePDK45.lydrc"
DST_DIR="$FLOW_DIR/platforms/nangate45/drc"
DST="$DST_DIR/FreePDK45.lydrc"
mkdir -p "$DST_DIR"
[[ -f "$DST" && ! -f "$DST.orig-300ratio" ]] && cp "$DST" "$DST.bak-$(date +%s)" || true
cp "$SRC" "$DST"
n=$(grep -c 'antenna_check(gate, metal.*300.0' "$DST" || true)
if [[ "$n" -ne 10 ]]; then
  echo "ERROR: installed deck is not 300:1 (found $n/10 antenna lines at 300.0)" >&2
  exit 1
fi
echo "Installed honest 300:1 FreePDK45.lydrc → $DST"
```

- [ ] **Step 4: Verify the installer runs idempotently**

Run: `bash tools/install_nangate45_drc.sh && bash tools/install_nangate45_drc.sh`
Expected: prints `Installed honest 300:1 …` twice, exit 0.

- [ ] **Step 5: Commit**

```bash
cd /proj/workarea/user5/agent-r2g
chmod +x tools/install_nangate45_drc.sh
git add r2g-rtl2gds/assets/platforms/nangate45/drc/FreePDK45.lydrc tools/install_nangate45_drc.sh
git commit -m "fix(skill): restore honest 300:1 nangate45 antenna deck + installer

Real-fixes-only: stop masking antenna DRC by relaxing the rule ratio.
Reverts the 2026-05-30 400:1 relaxation in both the skill asset and ORFS
install; adds tools/install_nangate45_drc.sh (verifies 300:1 on install).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Diagnoser pure core (`build_plan`) — TDD

**Files:**
- Create: `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py`
- Modify: `r2g-rtl2gds/tests/conftest.py`
- Test: `r2g-rtl2gds/tests/test_diagnose_signoff_fix.py`

- [ ] **Step 1: Make `scripts/reports` importable in tests**

In `r2g-rtl2gds/tests/conftest.py`, after the `EXTRACT_DIR` block, add:
```python
# Make scripts/reports/ importable for signoff-fixer tests.
REPORTS_DIR = SKILL_ROOT / "scripts" / "reports"
if str(REPORTS_DIR) not in sys.path:
    sys.path.insert(0, str(REPORTS_DIR))
```

- [ ] **Step 2: Write the failing tests** `r2g-rtl2gds/tests/test_diagnose_signoff_fix.py`

```python
"""Tests for diagnose_signoff_fix.py: signoff (DRC/LVS) violation→fix-plan logic."""
from __future__ import annotations

import diagnose_signoff_fix as d


def _drc(status, count=0, cats=None):
    return {"status": status, "total_violations": count, "categories": cats or {}}


def _antenna_cats(n=7, layer="METAL7_ANTENNA"):
    return {layer: {"count": n, "description": ""}}


def test_clean_drc_yields_no_strategies():
    plan = d.build_plan(_drc("clean"), {}, {}, check="drc")
    assert plan["status"] == "clean"
    assert plan["strategies"] == []


def test_antenna_fail_yields_three_ordered_strategies():
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, {"CORE_UTILIZATION": "10"}, check="drc")
    ids = [s["id"] for s in plan["strategies"]]
    assert ids == ["antenna_diode_iters", "antenna_route_effort", "antenna_density_relief"]
    assert plan["dominant_category"] == "METAL7_ANTENNA"
    # density relief computes a concrete lowered utilization
    relief = plan["strategies"][2]["config_edits"]
    assert relief["CORE_UTILIZATION"] == "5"


def test_applied_strategy_is_filtered_out():
    cfg = {"CORE_ANTENNACELL": "ANTENNA_X1", "MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
           "MAX_REPAIR_ANTENNAS_ITER_DRT": "10", "CORE_UTILIZATION": "10"}
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, cfg, check="drc")
    ids = [s["id"] for s in plan["strategies"]]
    assert "antenna_diode_iters" not in ids
    assert ids[0] == "antenna_route_effort"


def test_exhausted_antenna_is_residual():
    cfg = {"CORE_ANTENNACELL": "ANTENNA_X1", "MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
           "MAX_REPAIR_ANTENNAS_ITER_DRT": "10", "DETAILED_ROUTE_ARGS": "-droute_end_iteration 10",
           "CORE_UTILIZATION": "5"}
    plan = d.build_plan(_drc("fail", 7, _antenna_cats()), {}, cfg, check="drc")
    assert plan["status"] == "residual"
    assert plan["strategies"] == []


def test_non_antenna_drc_is_unhandled_residual():
    plan = d.build_plan(_drc("fail", 3, {"M2.SP.1": {"count": 3}}), {}, {}, check="drc")
    assert "non-antenna" in plan["residual_reason"]
    assert plan["strategies"] == []


def test_stuck_drc_is_out_of_scope():
    plan = d.build_plan(_drc("stuck"), {}, {}, check="drc")
    assert plan["strategies"] == []
    assert "out_of_v1_scope" in plan["residual_reason"]


def test_lvs_unknown_yields_resolve_strategy():
    plan = d.build_plan({}, {"status": "unknown", "mismatch_count": None}, {}, check="lvs")
    assert [s["id"] for s in plan["strategies"]] == ["lvs_resolve_unknown"]


def test_lvs_cpp_crash_is_residual():
    lvs = {"status": "fail", "log_info": {"errors": ["...sort_circuit::gen_log_entry SIGSEGV"]}}
    plan = d.build_plan({}, lvs, {}, check="lvs")
    assert plan["strategies"] == []
    assert "klayout_cpp_crash" in plan["residual_reason"]


def test_lvs_macro_emits_operator_only_strategy():
    lvs = {"status": "fail", "log_info": {"errors": ["Netlists don't match"]}}
    cfg = {"VERILOG_FILES": "/x/fakeram45_64x32.v /x/top.v"}
    plan = d.build_plan({}, lvs, cfg, check="lvs")
    s = plan["strategies"][0]
    assert s["id"] == "lvs_macro_cdl"
    assert s["auto_apply"] is False
    assert "operator_note" in s
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_diagnose_signoff_fix.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'diagnose_signoff_fix'`.

- [ ] **Step 4: Implement `diagnose_signoff_fix.py` (pure core + helpers)**

Create `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py`:
```python
#!/usr/bin/env python3
"""Diagnose DRC/LVS signoff violations and emit an ordered real-layout-fix plan.

Sibling of knowledge/analyze_execution.py (which proposes fixes for *backend*
stage failures); this module handles *signoff* (DRC/LVS) violations only.

Real-fixes-only policy: strategies apply genuine layout/config changes (antenna
diode insertion + repair iters, route effort, density/area relief) and NEVER
relax the DRC rule deck. See references/signoff-fixing.md.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BLOCK_START = "# >>> r2g signoff-fix (auto) >>>"
BLOCK_END = "# <<< r2g signoff-fix (auto) <<<"
ANTENNA_DIODE_CELL = "ANTENNA_X1"  # nangate45 ships MACRO ANTENNA_X1
KLAYOUT_CPP_CRASH = re.compile(r"sort_circuit|gen_log_entry|segmentation|sigsegv", re.I)


def parse_config(text: str) -> dict:
    """Parse `export VAR = value` / `VAR := value` lines (last assignment wins)."""
    cfg = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:override\s+)?(?:export\s+)?([A-Z0-9_]+)\s*[:?]?=\s*(.*?)\s*$", line)
        if m:
            cfg[m.group(1)] = m.group(2).strip()
    return cfg


def _all_antenna(categories: dict) -> bool:
    keys = list(categories or {})
    return bool(keys) and all(k.upper().endswith("_ANTENNA") for k in keys)


def _applied(cfg: dict, edits: dict) -> bool:
    return all(str(cfg.get(k)) == str(v) for k, v in edits.items())


def _antenna_strategies(cfg: dict) -> list:
    try:
        cur_util = int(float(cfg.get("CORE_UTILIZATION", "")))
    except (TypeError, ValueError):
        cur_util = None
    new_util = max(5, cur_util - 5) if cur_util is not None else 20
    catalog = [
        {"id": "antenna_diode_iters",
         "rationale": "Wire ANTENNA_X1 as the antenna diode and raise repair_antennas "
                      "iterations so OpenROAD inserts diodes / jumpers to break long metal.",
         "config_edits": {"CORE_ANTENNACELL": ANTENNA_DIODE_CELL,
                          "MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
                          "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
         "rerun_from": "route", "recheck": "drc", "auto_apply": True},
        {"id": "antenna_route_effort",
         "rationale": "Give the detailed router more end iterations to reroute long metal "
                      "onto additional layers.",
         "config_edits": {"DETAILED_ROUTE_ARGS": "-droute_end_iteration 10"},
         "rerun_from": "route", "recheck": "drc", "auto_apply": True},
        {"id": "antenna_density_relief",
         "rationale": "Lower placement utilization so the router has room to spread routes "
                      "across layers (reduces long single-layer runs). "
                      "PLACE_DENSITY_LB_ADDON is left untouched (hard rule: never < 0.10).",
         "config_edits": {"CORE_UTILIZATION": str(new_util)},
         "rerun_from": "floorplan", "recheck": "drc", "auto_apply": True},
    ]
    return [s for s in catalog if not _applied(cfg, s["config_edits"])]


def _drc_plan(drc: dict, cfg: dict, exclude: set) -> dict:
    status = drc.get("status", "unknown")
    cats = drc.get("categories") or {}
    dominant = max(cats, key=lambda k: cats[k].get("count", 0)) if cats else None
    plan = {"check": "drc", "status": status, "violation_count": drc.get("total_violations"),
            "dominant_category": dominant, "strategies": [], "residual_reason": None}
    if status in ("clean", "skipped"):
        return plan
    if status in ("stuck", "timeout"):
        plan["residual_reason"] = f"drc_{status}_tooling_out_of_v1_scope"
        return plan
    if status == "fail":
        if _all_antenna(cats):
            strategies = [s for s in _antenna_strategies(cfg) if s["id"] not in exclude]
            plan["strategies"] = strategies
            if not strategies:
                plan["status"] = "residual"
                plan["residual_reason"] = "antenna: all real-fix strategies exhausted"
        else:
            non_antenna = sorted(k for k in cats if not k.upper().endswith("_ANTENNA"))
            plan["residual_reason"] = "non-antenna DRC class not handled in v1: " + ", ".join(non_antenna)
    return plan


def _lvs_plan(lvs: dict, cfg: dict, exclude: set) -> dict:
    status = lvs.get("status", "unknown")
    plan = {"check": "lvs", "status": status, "violation_count": lvs.get("mismatch_count"),
            "dominant_category": None, "strategies": [], "residual_reason": None}
    if status in ("clean", "skipped"):
        return plan
    if status == "unknown":
        s = {"id": "lvs_resolve_unknown",
             "rationale": "Re-extract / inspect the LVS log to resolve the ambiguous status "
                          "to clean or fail before attempting any fix.",
             "config_edits": {}, "rerun_from": None, "recheck": "lvs", "auto_apply": True}
        if s["id"] not in exclude:
            plan["strategies"].append(s)
        return plan
    if status in ("fail", "failed"):
        errors = " ".join((lvs.get("log_info") or {}).get("errors", []))
        if KLAYOUT_CPP_CRASH.search(errors):
            plan["residual_reason"] = "klayout_cpp_crash_needs_upgrade (>=0.30.10)"
            return plan
        blob = (cfg.get("VERILOG_FILES", "") + " " + cfg.get("CDL_FILE", "")
                + " " + cfg.get("ADDITIONAL_LEFS", "")).lower()
        if "fakeram" in blob:
            s = {"id": "lvs_macro_cdl",
                 "rationale": "Macro design: point CDL_FILE at a combined CDL (std cells + "
                              "fakeram stubs) via `override export` so KLayout sees macro subckts.",
                 "config_edits": {}, "rerun_from": None, "recheck": "lvs", "auto_apply": False,
                 "operator_note": "Generate combined.cdl and add `override export CDL_FILE = "
                                  "<combined.cdl>`; see failure-patterns.md 'LVS CDL_FILE Override'."}
            if s["id"] not in exclude:
                plan["strategies"].append(s)
        else:
            plan["residual_reason"] = ("lvs mismatch with no auto-fix in v1; likely rule-deck "
                                       "(.lylvs) issue — operator review required")
        return plan
    plan["residual_reason"] = f"lvs status '{status}' not actionable in v1"
    return plan


def build_plan(drc: dict, lvs: dict, cfg: dict, *, check: str = "drc", exclude=()) -> dict:
    """Pure: (drc.json, lvs.json, parsed config.mk) -> ordered fix plan dict."""
    excl = set(exclude or ())
    return _drc_plan(drc or {}, cfg, excl) if check == "drc" else _lvs_plan(lvs or {}, cfg, excl)


def apply_edits(config_text: str, edits: dict) -> str:
    """Replace the marked auto-block with `edits` (idempotent; re-apply replaces)."""
    out, skip = [], False
    for ln in config_text.splitlines():
        s = ln.strip()
        if s == BLOCK_START:
            skip = True
            continue
        if s == BLOCK_END:
            skip = False
            continue
        if not skip:
            out.append(ln)
    body = "\n".join(out).rstrip("\n")
    block = [BLOCK_START] + [f"export {k} = {v}" for k, v in edits.items()] + [BLOCK_END]
    prefix = (body + "\n\n") if body else ""
    return prefix + "\n".join(block) + "\n"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Diagnose DRC/LVS violations → real-fix plan.")
    ap.add_argument("project_dir")
    ap.add_argument("--check", choices=["drc", "lvs"], default="drc")
    ap.add_argument("--apply", metavar="STRATEGY_ID", help="write the strategy's edits into config.mk")
    ap.add_argument("--next", action="store_true", help="print one tab-separated action line for the driver")
    ap.add_argument("--exclude", default="", help="comma-separated strategy ids to skip")
    args = ap.parse_args(argv)

    proj = Path(args.project_dir)
    drc = _load(proj / "reports" / "drc.json")
    lvs = _load(proj / "reports" / "lvs.json")
    cfg_path = proj / "constraints" / "config.mk"
    cfg_text = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    cfg = parse_config(cfg_text)
    exclude = [x for x in args.exclude.split(",") if x]
    plan = build_plan(drc, lvs, cfg, check=args.check, exclude=exclude)

    if args.apply:
        strat = next((s for s in plan["strategies"] if s["id"] == args.apply), None)
        if strat is None:
            print(f"ERROR: strategy '{args.apply}' not in current plan", file=sys.stderr)
            return 2
        if not strat.get("auto_apply", False):
            print(f"ERROR: '{args.apply}' is operator-only: {strat.get('operator_note','')}", file=sys.stderr)
            return 3
        if strat["config_edits"]:
            cfg_path.write_text(apply_edits(cfg_text, strat["config_edits"]), encoding="utf-8")
        print(json.dumps({"applied": strat["id"], "config_edits": strat["config_edits"]}))
        return 0

    if args.next:
        auto = next((s for s in plan["strategies"] if s.get("auto_apply")), None)
        if auto is None:
            reason = plan.get("residual_reason") or "no_auto_strategy"
            print(f"STOP\t{plan['status']}\t{reason}")
        else:
            print(f"{auto['id']}\t{auto.get('rerun_from') or ''}\t{auto['recheck']}")
        return 0

    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_diagnose_signoff_fix.py -q`
Expected: PASS (10 tests).

- [ ] **Step 6: Commit**

```bash
cd /proj/workarea/user5/agent-r2g
chmod +x r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py
git add r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py r2g-rtl2gds/tests/test_diagnose_signoff_fix.py r2g-rtl2gds/tests/conftest.py
git commit -m "feat(skill): diagnose_signoff_fix.py — DRC/LVS violation→real-fix plan

Pure build_plan() maps reports/{drc,lvs}.json + config.mk to an ordered
strategy plan (antenna diode/iters → route effort → density relief; LVS
triage). --apply writes an idempotent marked config.mk block; --next emits a
driver action line. Real-fixes-only: never relaxes the rule deck.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: CLI `--apply` / `--next` behavior — TDD

**Files:**
- Test: `r2g-rtl2gds/tests/test_diagnose_signoff_fix.py` (append)
- (implementation already in Task 2's `main`/`apply_edits` — these tests lock the CLI contract)

- [ ] **Step 1: Append failing CLI tests**

Append to `r2g-rtl2gds/tests/test_diagnose_signoff_fix.py`:
```python
import json
import subprocess
import sys
from pathlib import Path

MOD = Path(__file__).resolve().parents[1] / "scripts" / "reports" / "diagnose_signoff_fix.py"


def _mk_project(tmp_path, drc=None, lvs=None, config="export DESIGN_NAME = t\nexport CORE_UTILIZATION = 10\n"):
    p = tmp_path / "proj"
    (p / "reports").mkdir(parents=True)
    (p / "constraints").mkdir(parents=True)
    if drc is not None:
        (p / "reports" / "drc.json").write_text(json.dumps(drc))
    if lvs is not None:
        (p / "reports" / "lvs.json").write_text(json.dumps(lvs))
    (p / "constraints" / "config.mk").write_text(config)
    return p


def test_apply_writes_idempotent_block(tmp_path):
    p = _mk_project(tmp_path, drc={"status": "fail", "total_violations": 7,
                                   "categories": {"METAL7_ANTENNA": {"count": 7}}})
    cfg = p / "constraints" / "config.mk"
    for _ in range(2):  # apply twice → block must not duplicate
        subprocess.run([sys.executable, str(MOD), str(p), "--check", "drc",
                        "--apply", "antenna_diode_iters"], check=True)
    text = cfg.read_text()
    assert text.count("# >>> r2g signoff-fix (auto) >>>") == 1
    assert "export CORE_ANTENNACELL = ANTENNA_X1" in text
    assert text.count("export DESIGN_NAME = t") == 1  # original preserved once


def test_next_prints_first_auto_strategy(tmp_path):
    p = _mk_project(tmp_path, drc={"status": "fail", "total_violations": 7,
                                   "categories": {"METAL7_ANTENNA": {"count": 7}}})
    out = subprocess.run([sys.executable, str(MOD), str(p), "--check", "drc", "--next"],
                         capture_output=True, text=True, check=True).stdout.strip()
    sid, rerun, recheck = out.split("\t")
    assert sid == "antenna_diode_iters" and rerun == "route" and recheck == "drc"


def test_next_prints_stop_when_clean(tmp_path):
    p = _mk_project(tmp_path, drc={"status": "clean", "total_violations": 0, "categories": {}})
    out = subprocess.run([sys.executable, str(MOD), str(p), "--check", "drc", "--next"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out.startswith("STOP\tclean")


def test_apply_operator_only_strategy_errors(tmp_path):
    p = _mk_project(tmp_path, lvs={"status": "fail", "log_info": {"errors": ["don't match"]}},
                    config="export VERILOG_FILES = /x/fakeram45_64x32.v\n")
    r = subprocess.run([sys.executable, str(MOD), str(p), "--check", "lvs",
                        "--apply", "lvs_macro_cdl"], capture_output=True, text=True)
    assert r.returncode == 3 and "operator-only" in r.stderr
```

- [ ] **Step 2: Run to verify pass**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_diagnose_signoff_fix.py -q`
Expected: PASS (14 tests).

- [ ] **Step 3: Commit**

```bash
cd /proj/workarea/user5/agent-r2g
git add r2g-rtl2gds/tests/test_diagnose_signoff_fix.py
git commit -m "test(skill): lock --apply idempotency + --next action contract for signoff fixer

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Loop driver `fix_signoff.sh` + integration test

**Files:**
- Create: `r2g-rtl2gds/scripts/flow/fix_signoff.sh`
- Test: `r2g-rtl2gds/tests/test_diagnose_signoff_fix.py` (append integration test)

- [ ] **Step 1: Write the failing integration test**

Append to `r2g-rtl2gds/tests/test_diagnose_signoff_fix.py`:
```python
import os

DRIVER = Path(__file__).resolve().parents[1] / "scripts" / "flow" / "fix_signoff.sh"


def _stub_dir(tmp_path, counts):
    """Build stub run_orfs/run_drc + an extract that pops `counts` into drc.json."""
    sd = tmp_path / "stubs"
    sd.mkdir()
    (sd / "counts.txt").write_text("\n".join(str(c) for c in counts) + "\n")
    (sd / "run_orfs.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    (sd / "run_drc.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    extract = f"""#!/usr/bin/env bash
proj="$1"; out="$2"
cf="{sd}/counts.txt"
n=$(head -1 "$cf"); tail -n +2 "$cf" > "$cf.tmp" && mv "$cf.tmp" "$cf"
[ -z "$n" ] && n=0
if [ "$n" = "0" ]; then
  printf '{{"status":"clean","total_violations":0,"categories":{{}}}}' > "$out"
else
  printf '{{"status":"fail","total_violations":%s,"categories":{{"METAL7_ANTENNA":{{"count":%s}}}}}}' "$n" "$n" > "$out"
fi
"""
    (sd / "extract_drc.py").write_text(extract)
    for f in ("run_orfs.sh", "run_drc.sh", "extract_drc.py"):
        os.chmod(sd / f, 0o755)
    return sd


def _run_driver(proj, sd, max_iters=3):
    env = dict(os.environ,
               R2G_RUN_ORFS=str(sd / "run_orfs.sh"),
               R2G_RUN_DRC=str(sd / "run_drc.sh"),
               R2G_EXTRACT_DRC=str(sd / "extract_drc.py"))
    return subprocess.run(["bash", str(DRIVER), str(proj), "nangate45",
                           "--check", "drc", "--max-iters", str(max_iters)],
                          capture_output=True, text=True, env=env)


def test_driver_stops_when_cleaned(tmp_path):
    # seeded fail=7, first re-check returns 0 → cleaned in 1 applied iter
    p = _mk_project(tmp_path, drc={"status": "fail", "total_violations": 7,
                                   "categories": {"METAL7_ANTENNA": {"count": 7}}})
    sd = _stub_dir(tmp_path, counts=[0])
    r = _run_driver(p, sd)
    assert r.returncode == 0, r.stderr
    log = (p / "reports" / "fix_log.jsonl").read_text().strip().splitlines()
    assert len(log) >= 1
    assert (p / "reports" / "fix_summary.md").exists()
    final = json.loads((p / "reports" / "drc.json").read_text())
    assert final["status"] == "clean"


def test_driver_early_exits_on_no_improvement(tmp_path):
    # seeded fail=7, re-checks keep returning 7 → early-exit, not 3 full iters
    p = _mk_project(tmp_path, drc={"status": "fail", "total_violations": 7,
                                   "categories": {"METAL7_ANTENNA": {"count": 7}}})
    sd = _stub_dir(tmp_path, counts=[7, 7, 7])
    r = _run_driver(p, sd)
    summary = (p / "reports" / "fix_summary.md").read_text()
    assert "no_improvement" in summary
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd r2g-rtl2gds && python -m pytest tests/test_diagnose_signoff_fix.py -k driver -q`
Expected: FAIL — driver script missing / non-zero exit.

- [ ] **Step 3: Implement** `r2g-rtl2gds/scripts/flow/fix_signoff.sh`

```bash
#!/usr/bin/env bash
set -euo pipefail
# usage: fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both] [--max-iters N] [--resume]
#
# Iteratively applies REAL layout fixes for DRC/LVS violations: diagnose →
# apply (config.mk marked block) → re-run flow → re-check → compare, up to
# --max-iters, with early-exit when an iteration does not reduce the count.
# Real-fixes-only: never relaxes the DRC rule deck. See references/signoff-fixing.md.
#
# Progress is flushed to <project>/reports/fix_log.jsonl per iteration (long
# DRC/LVS runtimes mean we never batch logging to the end); a human-readable
# <project>/reports/fix_summary.md is written at the end.
#
# Command seams are overridable for testing:
#   R2G_RUN_ORFS R2G_RUN_DRC R2G_RUN_LVS R2G_EXTRACT_DRC R2G_EXTRACT_LVS R2G_DIAGNOSE

PROJECT_DIR=""; PLATFORM="nangate45"; CHECK="both"; MAX_ITERS=3; RESUME=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK="$2"; shift 2;;
    --max-iters) MAX_ITERS="$2"; shift 2;;
    --resume) RESUME=1; shift;;
    -*) echo "unknown flag: $1" >&2; exit 1;;
    *) if [[ -z "$PROJECT_DIR" ]]; then PROJECT_DIR="$1"; else PLATFORM="$1"; fi; shift;;
  esac
done
[[ -z "$PROJECT_DIR" ]] && { echo "usage: fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both] [--max-iters N] [--resume]" >&2; exit 1; }
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXTRACT_DIR="$(cd "$SCRIPT_DIR/../extract" && pwd)"
REPORTS_DIR_SCRIPTS="$(cd "$SCRIPT_DIR/../reports" && pwd)"
RUN_ORFS="${R2G_RUN_ORFS:-$SCRIPT_DIR/run_orfs.sh}"
RUN_DRC="${R2G_RUN_DRC:-$SCRIPT_DIR/run_drc.sh}"
RUN_LVS="${R2G_RUN_LVS:-$SCRIPT_DIR/run_lvs.sh}"
EXTRACT_DRC="${R2G_EXTRACT_DRC:-$EXTRACT_DIR/extract_drc.py}"
EXTRACT_LVS="${R2G_EXTRACT_LVS:-$EXTRACT_DIR/extract_lvs.py}"
DIAGNOSE="${R2G_DIAGNOSE:-$REPORTS_DIR_SCRIPTS/diagnose_signoff_fix.py}"

REPORTS="$PROJECT_DIR/reports"
mkdir -p "$REPORTS"
LOG="$REPORTS/fix_log.jsonl"

_count() {  # read current violation count from a report json
  python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
v=d.get("total_violations"); v=d.get("mismatch_count") if v is None else v
print("" if v is None else v)' "$1" 2>/dev/null || echo ""
}

_run_extract() {  # $1=check
  if [[ "$1" == "drc" ]]; then
    [[ -x "$EXTRACT_DRC" || "$EXTRACT_DRC" == *.py ]] && python3 "$EXTRACT_DRC" "$PROJECT_DIR" "$REPORTS/drc.json" || "$EXTRACT_DRC" "$PROJECT_DIR" "$REPORTS/drc.json"
  else
    python3 "$EXTRACT_LVS" "$PROJECT_DIR" "$REPORTS/lvs.json"
  fi
}

_log_iter() {  # check iter strategy before after verdict
  python3 -c 'import json,sys
o=dict(check=sys.argv[1],iter=int(sys.argv[2]),strategy=sys.argv[3],
       before=(sys.argv[4] or None),after=(sys.argv[5] or None),
       verdict=sys.argv[6],ts=sys.argv[7])
open(sys.argv[8],"a").write(json.dumps(o)+"\n")' \
    "$1" "$2" "$3" "$4" "$5" "$6" "$(date -u +%FT%TZ)" "$LOG"
}

fix_one() {  # $1 = drc|lvs
  local check="$1" report="$REPORTS/$1.json" tried="" before after it sid rerun recheck line verdict
  [[ -f "$report" ]] || _run_extract "$check"
  before="$(_count "$report")"
  for ((it=1; it<=MAX_ITERS; it++)); do
    line="$(python3 "$DIAGNOSE" "$PROJECT_DIR" --check "$check" --exclude "$tried" --next)"
    IFS=$'\t' read -r sid rerun recheck <<<"$line"
    if [[ "$sid" == "STOP" ]]; then
      _log_iter "$check" "$it" "none" "$before" "$before" "stop_${rerun}"
      echo "[$check] stop: $rerun ${recheck:-}"; return 0
    fi
    echo "[$check] iter $it: applying $sid (rerun_from=${rerun:-none})"
    python3 "$DIAGNOSE" "$PROJECT_DIR" --check "$check" --apply "$sid" >/dev/null
    tried="${tried:+$tried,}$sid"
    if [[ -n "$rerun" ]]; then
      if [[ "$RESUME" == "1" ]]; then FROM_STAGE="$rerun" "$RUN_ORFS" "$PROJECT_DIR" "$PLATFORM"
      else "$RUN_ORFS" "$PROJECT_DIR" "$PLATFORM"; fi
    fi
    if [[ "$check" == "drc" ]]; then "$RUN_DRC" "$PROJECT_DIR" "$PLATFORM" || true; else "$RUN_LVS" "$PROJECT_DIR" "$PLATFORM" || true; fi
    _run_extract "$check"
    after="$(_count "$report")"
    verdict="applied"
    if [[ -n "$before" && -n "$after" ]] && python3 -c "import sys;sys.exit(0 if float('$after')>=float('$before') else 1)" 2>/dev/null; then
      verdict="no_improvement"
    fi
    _log_iter "$check" "$it" "$sid" "$before" "$after" "$verdict"
    echo "[$check] iter $it: $before -> $after ($verdict)"
    if [[ "$after" == "0" ]]; then echo "[$check] CLEAN"; return 0; fi
    if [[ "$verdict" == "no_improvement" ]]; then echo "[$check] no improvement; stopping"; return 0; fi
    before="$after"
  done
  echo "[$check] reached max-iters=$MAX_ITERS"
}

: > "$LOG"
[[ "$CHECK" == "drc" || "$CHECK" == "both" ]] && fix_one drc || true
[[ "$CHECK" == "lvs" || "$CHECK" == "both" ]] && fix_one lvs || true

# Markdown summary from the JSONL log
python3 -c 'import json,sys
log=sys.argv[1]; out=sys.argv[2]
rows=[json.loads(l) for l in open(log) if l.strip()]
lines=["# Signoff fix summary","","| check | iter | strategy | before | after | verdict |","|---|---|---|---|---|---|"]
for r in rows:
    lines.append("| {check} | {iter} | {strategy} | {before} | {after} | {verdict} |".format(**r))
open(out,"w").write("\n".join(lines)+"\n")' "$LOG" "$REPORTS/fix_summary.md"
echo "Summary: $REPORTS/fix_summary.md"

# exit 0 if final state clean, else 2 if residual remains
python3 -c 'import json,sys,os
proj=sys.argv[1]; rc=0
for c in ("drc","lvs"):
    p=os.path.join(proj,"reports",c+".json")
    if os.path.exists(p):
        d=json.load(open(p))
        if d.get("status") in ("fail","failed","residual"): rc=2
sys.exit(rc)' "$PROJECT_DIR" || exit 2
```

- [ ] **Step 4: Run the integration test to verify it passes**

Run: `cd r2g-rtl2gds && chmod +x scripts/flow/fix_signoff.sh && python -m pytest tests/test_diagnose_signoff_fix.py -k driver -q`
Expected: PASS (2 driver tests).

- [ ] **Step 5: Commit**

```bash
cd /proj/workarea/user5/agent-r2g
git add r2g-rtl2gds/scripts/flow/fix_signoff.sh r2g-rtl2gds/tests/test_diagnose_signoff_fix.py
git commit -m "feat(skill): fix_signoff.sh — iterative DRC/LVS real-fix loop driver

Diagnose→apply→re-run→re-check up to --max-iters with early-exit on
no-improvement. Flushes reports/fix_log.jsonl per iteration (on-the-fly) and
writes reports/fix_summary.md. Command seams (R2G_RUN_ORFS/RUN_DRC/EXTRACT_*)
are injectable for the deterministic integration test.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Skill documentation integration

**Files:**
- Create: `r2g-rtl2gds/references/signoff-fixing.md`
- Modify: `r2g-rtl2gds/SKILL.md`, `r2g-rtl2gds/references/failure-patterns.md`, `CLAUDE.md`

- [ ] **Step 1: Create `references/signoff-fixing.md`**

Write a reference doc covering: the real-fixes-only policy; the honest 300:1 deck; the
strategy catalog (antenna diode/iters → route effort → density relief; LVS resolve-unknown
/ macro-CDL / honest residual); the fix-plan JSON schema; `fix_log.jsonl` + `fix_summary.md`
formats; usage `scripts/flow/fix_signoff.sh <project-dir> [platform] [--check] [--max-iters] [--resume]`;
and the residual taxonomy (stuck/timeout/klayout_cpp_crash → not fixable in v1).

- [ ] **Step 2: Wire into `SKILL.md`** — in the signoff section (after the DRC/LVS extract
lines near 365–366), add:
```markdown
    - If DRC/LVS status is `fail`, attempt real layout fixes (antenna diode insertion +
      repair iters, route effort, density relief; LVS triage):
      `scripts/flow/fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both] [--max-iters 3]`
      Real-fixes-only — never relaxes the rule deck. Residual stuck/timeout/KLayout-crash
      cases are reported honestly. See `references/signoff-fixing.md`.
```

- [ ] **Step 3: Cross-reference from `failure-patterns.md`** — under "Antenna DRC Violations"
and "LVS Mismatch", add a line: `**Automated fix:** scripts/flow/fix_signoff.sh (see references/signoff-fixing.md). Note: the 400:1 antenna ratio relaxation is RETIRED — real fixes only.`

- [ ] **Step 4: Add a `CLAUDE.md` "Where to find X" row**
```markdown
| DRC/LVS violation fixing (antenna diode insertion, route/density, LVS triage) | `r2g-rtl2gds/references/signoff-fixing.md` |
```

- [ ] **Step 5: Commit**

```bash
cd /proj/workarea/user5/agent-r2g
git add r2g-rtl2gds/references/signoff-fixing.md r2g-rtl2gds/SKILL.md r2g-rtl2gds/references/failure-patterns.md CLAUDE.md
git commit -m "docs(skill): document DRC/LVS signoff-fixing ability + retire 400:1 relaxation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Full regression

- [ ] **Step 1: Run the whole suite**

Run: `cd r2g-rtl2gds && python -m pytest -q`
Expected: all prior tests + the new signoff-fixer tests PASS (≥ 223 + 16).

- [ ] **Step 2: If green, no commit needed** (code already committed). If red, fix the
regression before proceeding to validation.

---

## Task 7 (Execution / not TDD): Phase 0 — honest baseline + root-cause + stuck probe

- [ ] **Step 1: Re-DRC the antenna designs against the honest 300:1 deck** to capture the
true baseline (the ~29 antenna designs from the 2026-05-30 campaign). For each, run
`scripts/flow/run_drc.sh <proj> nangate45` then `extract_drc.py`. Record counts in a dated
`docs/` log **as each completes** (on-the-fly).

- [ ] **Step 2: Root-cause the residual-7** on 1–2 designs (e.g. `PicoRV32_Based_SoC_fifo_basic`):
inspect `backend/RUN_*/reports/.../drt_antennas.log` + the `antenna_diodes_count` metric to
learn whether diodes are inserted and why 7 survive. Adjust strategy ordering in
`diagnose_signoff_fix.py` if the finding warrants (commit any change with a `fix(skill):`).

- [ ] **Step 3: Bounded stuck-DRC probe** on 2–3 stuck designs: try a KLayout flag / rule
subset / region tiling experiment (time-boxed). Decide and **document** whether stuck-handling
enters scope. Update the spec/plan with a dated note (commit hash + decision).

---

## Task 8 (Execution / not TDD): Phase 1 — fixer validation on the known-fail set

- [ ] **Step 1: Build the target list** — DRC `fail` (9) + the honest-baseline antenna
reopens + LVS `fail` (10) + LVS `unknown` (52). Save to a worklist file under `docs/`.

- [ ] **Step 2: Run the fixer per design** (small designs; full re-run each iter is cheap):
`scripts/flow/fix_signoff.sh <proj> nangate45 --check both --max-iters 3`. Respect hard
rules (no concurrent same-DESIGN_NAME/variant; ≤1 LVS on >100K-cell designs). Append results
to the dated `docs/` log on-the-fly.

- [ ] **Step 3: Iterate the skill** — when the fixer mishandles a case, update
`diagnose_signoff_fix.py` / `fix_signoff.sh`, re-validate, and record under the matching
`failure-patterns.md` bucket. This is the core deliverable: improving the skill from real runs.

---

## Task 9 (Execution / not TDD): Phase 2 — expand to large designs

- [ ] **Step 1:** Run `large_rtl_designs` (BOOM CPU, Faraday ASIC, Gaisler leon2) through the
full flow + fixer, using `--resume` (FROM_STAGE) to avoid re-synthesizing on each iteration,
plus PLACE_FAST/ROUTE_FAST per the ChipTop guidance. Document on-the-fly.

- [ ] **Step 2:** Final campaign write-up in `docs/` + memory update (per repo feedback rule:
dated note with commit hash + any superseded invariants in the spec/plan).

---

## Self-Review

- **Spec coverage:** §5.1 diagnoser → Task 2/3; §5.2 driver → Task 4; §5.3 honest deck →
  Task 1; §5.4 integration → Task 5; §6 strategy catalog → Task 2 (`_antenna_strategies`,
  `_lvs_plan`); §7 data contracts → Task 4 (`fix_log.jsonl`/`fix_summary.md`); §8 validation
  → Tasks 7–9; §9 testing → Tasks 2–4,6. All covered.
- **Placeholder scan:** All code is complete; doc tasks (Task 5) specify exact insertions —
  the `signoff-fixing.md` body is described by required content, acceptable for a docs file.
- **Type/name consistency:** `build_plan(drc, lvs, cfg, *, check, exclude)`, `apply_edits`,
  strategy ids (`antenna_diode_iters`/`antenna_route_effort`/`antenna_density_relief`,
  `lvs_resolve_unknown`/`lvs_macro_cdl`), and `--next` tab format are consistent across
  diagnoser, driver, and tests.

---

## Amendments (2026-06-01, during execution)

Code-quality review surfaced bugs in the verbatim code blocks above; the shipped code
differs from the listing in these (intentional) ways. The committed code is the source of
truth:

- **Diagnoser hardening — commit `26d133e`:**
  - `_drc_plan` handles `status in ("fail","failed")` (extract_drc.py can emit `"failed"`).
  - `dominant_category` is null-safe: `cats[k].get("count") or 0` (a `None` count no longer
    raises `TypeError`).
  - `_applied(cfg, edits)` returns `False` for empty `edits` (was vacuously `True`).
  - DRC `"unknown"` status sets `residual_reason = "drc status unknown — no report yet"`.
  - `--apply` falls back to the full antenna catalog so re-applying an already-applied
    strategy is idempotent (exit 0), not a spurious exit 2. Implemented via the
    `_antenna_catalog` (full) vs `_antenna_strategies` (filtered) split — `build_plan` still
    returns the FILTERED list so `--next` advances and exhaustion → `residual`.
- **Driver hardening — commit `d76daed`:**
  - `fix_one` captures `run_orfs` exit code and aborts the check on failure
    (`verdict=rerun_failed_rcN`) instead of silently re-reading a stale report and logging a
    false `no_improvement`. (Root cause: `set -e` is suppressed inside a function invoked via
    an `&&/|| true` chain — so the rc must be checked explicitly.)
  - `_run_extract` always calls `python3 "$script" …` (the plan's `(A && B) || C` guard
    double-invoked the extractor on failure). Test stubs rewritten as real Python.
  - Empty `sid` from `--next` is guarded (aborts, no `--apply ""` no-op spin).
  - Tab-delimited `--next` parsing maps `\t`→`\x1f` before `read` so an EMPTY middle field
    (`lvs_resolve_unknown` has empty `rerun_from`) is preserved — whitespace-IFS `read` had
    collapsed it, shifting `recheck` into `rerun` and triggering a spurious re-route.
  - `--check` value validated; strategy blacklisted (`tried`) only after a successful apply;
    `None` counts render as blank in `fix_summary.md`.
- **Antenna catalog corrected to real ORFS knobs — commit `42d0e0b`** (final integration
  review): the §6 antenna catalog as originally specced had two domain bugs, both verified
  against the live ORFS install:
  - `CORE_ANTENNACELL` is **not** an env var ORFS reads — `repair_antennas` auto-discovers
    the diode from the LEF (`ANTENNA_X1` declares `CLASS CORE ANTENNACELL`). Setting it in
    config.mk is a no-op → removed from `antenna_diode_iters`.
  - `DETAILED_ROUTE_ARGS=-droute_end_iteration 10` is invalid (real flag `-droute_end_iter`,
    knob `DETAILED_ROUTE_END_ITERATION` defaults to **64**, so 10 would *reduce* iterations)
    and isn't an antenna lever → the `antenna_route_effort` strategy was **removed**.
  - Final antenna catalog is **two** real-fix strategies: `antenna_diode_iters`
    (`MAX_REPAIR_ANTENNAS_ITER_GRT/_DRT=10`, default 5) then `antenna_density_relief`
    (lower `CORE_UTILIZATION`). Tests + `signoff-fixing.md` + `failure-patterns.md` updated.

### Amendments (2026-06-01, Phase-0/1 execution)

- **nangate45 antenna = immediate honest residual — commits `bd2b67b`, `4d15d76`.** Phase 0
  proved both antenna strategies fail on nangate45 (S1 `repair_antennas` inert: no tech-LEF
  antenna rules + zero-`ANTENNADIFFAREA` diode; S2 density-relief counterproductive,
  fifo_basic 14→16). Diagnoser now returns empty strategies + documented `residual_reason`.
  Also: true DRC item-count fix (`bd2b67b`), strategy escalation on `no_improvement`, LVS
  crash/incomplete reclassification (`4d15d76`).
- **BEOL-only DRC mode + `clean_beol` status — commits `b8d6`, `56a1`, `76c81b9`.**
  Fallback for the 271 FEOL-hang `stuck` designs: a deck copy with `FEOL=false` AND
  `ANTENNA=false` (ANTENNA's `connect` rules depend on the FEOL-derived `gate` layer, so it
  must also be disabled or KLayout errors and the run mis-classifies as `stuck`). Because it
  skips two rule groups, a 0-violation BEOL-only run is given the qualified status
  **`clean_beol`** (commit `76c81b9`) — NOT plain `clean` — so aggregation cannot miscount a
  partial check as a full clean. `diagnose_signoff_fix.py` treats `clean_beol` as no-fix.
  - **Superseded invariant:** "0-violation DRC ⇒ `clean`" → BEOL-only ⇒ `clean_beol`.
  - **Empirical validation (real ORFS):** DMA_Controller_DMA_registers (hung ~4h at FEOL
    `:131`) → `clean_beol` in **7.7s**; ip_demux → **34s**; both 0 violations. Full pytest
    suite 265 pass / 8 skip.
  - **Large-design BEOL CONTACT hang (confirmed):** at ≥~470K instances the hang **migrates
    from FEOL to the BEOL `CONTACT.1/2` ops** (`cont.width`/`cont.space`, deck line ~143–144)
    over millions of contact polygons — eth_mac_1g_fifo + koios_gemm_layer advanced 1–2 deck
    lines then froze 5–8 min at 100% CPU, RSS 7.3GB; killed (anti-zombie), left honest `stuck`.
    `cont` is library-internal (P&R adds only vias, never intra-cell contacts), so a deeper
    fallback could also skip `CONTACT.*` (same justification as FEOL) — rule-line surgery,
    deferred pending evidence. → candidate improvement #8. A population batch must also bound
    parallelism by memory (~7GB/large design).
