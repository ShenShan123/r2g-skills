# Signoff Fixing (DRC / LVS)

Automated, iterative loop that applies **real layout fixes** for DRC and LVS violations
after the main backend run. Real-fixes-only means the fix loop modifies routing, diode
insertion, or placement density — it **never** relaxes the DRC rule deck.

The honest 300:1 antenna-ratio rule deck for nangate45 is the reference. Install it once
with `tools/install_nangate45_drc.sh`. The 400:1 ratio relaxation used in an earlier
campaign is **retired**; that deck is no longer in use.

---

## Scripts

### `scripts/reports/diagnose_signoff_fix.py` — pure diagnoser

```
diagnose_signoff_fix.py <project-dir> --check drc|lvs [--apply <strategy-id>]
                        [--next] [--exclude id1,id2]
```

- **Default (no flags):** prints the full fix-plan JSON to stdout.
- **`--apply <id>`:** writes the named strategy's `config_edits` into an idempotent
  marked block in `constraints/config.mk` delimited by:
  ```
  # >>> r2g signoff-fix (auto) >>>
  # <<< r2g signoff-fix (auto) <<<
  ```
  Re-applying the same (or a new) strategy replaces the block — never duplicates lines.
  Exit 2 = unknown strategy id. Exit 3 = operator-only strategy (no auto-apply allowed).
- **`--next`:** prints one tab-separated action line consumed by `fix_signoff.sh`:
  `<id>\t<rerun_from>\t<recheck>`, or `STOP\t<status>\t<reason>` when no auto strategy
  remains.
- **`--exclude id1,id2`:** skip listed strategy ids (already-tried, in the driver loop).

### `scripts/flow/fix_signoff.sh` — iterative driver

```
fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both] [--max-iters N] [--resume]
```

Default: `platform=nangate45`, `--check both`, `--max-iters 3`.

**Loop per check (drc / lvs):**

1. Read current violation count from `reports/{drc,lvs}.json` (re-extracts if missing).
2. Call `diagnose_signoff_fix.py --next` to get the next auto strategy.
3. Call `--apply <id>` to write `config_edits` into the marked block in `config.mk`.
4. Re-run the flow:
   - Without `--resume`: `run_orfs.sh <project-dir> <platform>` (full run from scratch).
   - With `--resume`: `FROM_STAGE=<rerun_from> run_orfs.sh <project-dir> <platform>`.
5. Re-run `run_drc.sh` or `run_lvs.sh` and re-extract results.
6. Compare before/after violation count.

**Early-exit conditions:**

- Violation count reaches 0 (`CLEAN`).
- An iteration does not reduce the violation count (`no_improvement`).
- `diagnose_signoff_fix.py --next` returns `STOP` (residual — no auto strategy left).
- `run_orfs.sh` fails (rc ≠ 0) — aborts that check, does NOT re-read a stale report.
- `max-iters` reached.

**Outputs:**

| File | Content |
|------|---------|
| `<project>/reports/fix_log.jsonl` | One JSON line per iteration: `{check, iter, strategy, before, after, verdict, ts}`. Flushed on-the-fly (not buffered to end). |
| `<project>/reports/fix_summary.md` | Markdown table of all iterations, written once at end. |

**Exit codes:** 0 = final status clean; 2 = residual violations remain.

---

## Fix-plan JSON schema

```json
{
  "check": "drc|lvs",
  "status": "fail|residual|clean|skipped|stuck|timeout|unknown",
  "violation_count": 42,
  "dominant_category": "METAL4_ANTENNA",
  "strategies": [
    {
      "id": "antenna_diode_iters",
      "rationale": "...",
      "config_edits": {"MAX_REPAIR_ANTENNAS_ITER_GRT": "10", "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
      "rerun_from": "route",
      "recheck": "drc",
      "auto_apply": true,
      "operator_note": "(optional, operator-only strategies only)"
    }
  ],
  "residual_reason": null
}
```

`strategies` is an ordered list — apply from front to back. Already-applied strategies
(all `config_edits` match the current `config.mk`) are filtered out before delivery.

---

## Strategy catalog (v1)

### DRC — antenna violations only

Strategies are `auto_apply: true`, applied in order; already-applied entries are skipped;
when all are exhausted `status` becomes `residual`. **The catalog is platform-aware** —
`antenna_diode_iters` is offered only on platforms with a working antenna-repair flow; on
`nangate45` (in `ANTENNA_REPAIR_INERT_PLATFORMS`) only `antenna_density_relief` is offered.

| id | platforms | config_edits | rerun_from | Effect |
|----|-----------|-------------|------------|--------|
| `antenna_diode_iters` | non-nangate45 (working diode) | `MAX_REPAIR_ANTENNAS_ITER_GRT=10`, `MAX_REPAIR_ANTENNAS_ITER_DRT=10` | `route` | Raises OpenROAD repair-antennas iterations (default 5) so more diodes are inserted. Diode auto-discovered from its `CLASS CORE ANTENNACELL` LEF declaration; do NOT set `CORE_ANTENNACELL` (not an ORFS env var). |
| `antenna_density_relief` | all | `CORE_UTILIZATION` lowered by 5 (floor 5) | `floorplan` | Reduces placement density / grows area so the router can break long metal runs. `PLACE_DENSITY_LB_ADDON` is **never** touched (hard rule: never below 0.10). |

**Why nangate45 skips diode insertion (verified 2026-06-01, campaign Finding B):** the
nangate45 tech LEF has no antenna rules (OpenROAD `check_antennas` → 0 violations) and its
only diode `ANTENNA_X1` has `ANTENNADIFFAREA 0.0` (so `repair_antennas` rejects it,
`GRT-0244`/`GRT-0246`). OpenROAD therefore cannot see or repair the antennas that KLayout's
`FreePDK45.lydrc` (300:1) flags. Density relief is the only tool-agnostic lever; if it cannot
clear them, the honest outcome is `residual` (the deck is never relaxed). Note density relief
enlarges the die, which **increases KLayout DRC runtime**.

Non-antenna DRC categories are **not** handled in v1 — reported as residual.

### LVS

| id | auto_apply | Effect |
|----|-----------|--------|
| `lvs_resolve_unknown` | yes | Re-extract to resolve an ambiguous `unknown` status before attempting any fix. `config_edits` is empty (no config change). |
| `lvs_macro_cdl` | **no** (operator-only) | Macro design: generate a combined CDL (std-cells + fakeram stubs) and add `override export CDL_FILE = <combined.cdl>` to `config.mk`. See `failure-patterns.md` "LVS CDL_FILE Override". |

---

## Residual taxonomy (NOT fixable in v1)

These are reported honestly by `diagnose_signoff_fix.py` with a non-null `residual_reason`.
`fix_signoff.sh` stops and exits 2.

| Condition | `residual_reason` | What to do |
|-----------|-------------------|-----------|
| DRC stuck or timeout | `drc_stuck_tooling_out_of_v1_scope` / `drc_timeout_tooling_out_of_v1_scope` | KLayout polygon-op hang, outside v1 scope. Accept GDS+LVS+RCX pass as evidence. |
| Non-antenna DRC class | `non-antenna DRC class not handled in v1: ...` | Operator review of the specific category. |
| All antenna strategies exhausted | `antenna: all real-fix strategies exhausted` | No further config lever available; consider manual routing intervention or structural RTL change. |
| LVS KLayout C++ crash (`sort_circuit` / `gen_log_entry` SIGSEGV) | `klayout_cpp_crash_needs_upgrade (>=0.30.10)` | Upgrade KLayout. |
| LVS rule-deck mismatch (non-macro) | `lvs mismatch with no auto-fix in v1; ...` | Operator review of the `.lylvs` rule deck. |

---

## Real-fixes-only policy

The fix loop applies only genuine layout changes:

- More antenna diode insertion (raise ORFS `MAX_REPAIR_ANTENNAS_ITER_GRT`/`_DRT`,
  default 5 → 10; the `ANTENNA_X1` diode is auto-discovered from the LEF, so
  `CORE_ANTENNACELL` is **not** set — it is a no-op env var ORFS does not read)
- Placement density/utilization reduction (`CORE_UTILIZATION`)
- LVS macro CDL (operator-provided combined CDL)

It **never** relaxes the DRC rule deck. The 400:1 antenna-ratio variant of
`FreePDK45.lydrc` (used in the 2026-05-30 campaign wave) is retired. Re-install the
honest 300:1 deck with `tools/install_nangate45_drc.sh`.

---

## Quick start

```bash
# One-shot: attempt all real fixes for both DRC and LVS, up to 3 iterations each
bash r2g-rtl2gds/scripts/flow/fix_signoff.sh design_cases/my_design nangate45

# DRC only, up to 5 iterations, resuming from the stage named in each strategy
bash r2g-rtl2gds/scripts/flow/fix_signoff.sh design_cases/my_design nangate45 \
  --check drc --max-iters 5 --resume

# Inspect the fix plan without applying anything
python3 r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py design_cases/my_design \
  --check drc | python3 -m json.tool

# Apply one strategy manually
python3 r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py design_cases/my_design \
  --check drc --apply antenna_diode_iters
```

Check results afterwards:

```bash
cat design_cases/my_design/reports/fix_summary.md
cat design_cases/my_design/reports/fix_log.jsonl
```
