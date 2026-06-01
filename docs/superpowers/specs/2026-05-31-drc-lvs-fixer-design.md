# DRC/LVS Violation-Fixing Ability — Design Spec

- **Date:** 2026-05-31
- **Branch:** `all_tech_feat_label_extract`
- **Status:** Draft (awaiting user review)
- **Skill:** `r2g-rtl2gds`

## 1. Context & Problem

The `r2g-rtl2gds` skill runs RTL→GDS with signoff (DRC, LVS, RCX). For DRC/LVS it
currently **detects, classifies, and documents** violations:

- `scripts/flow/run_drc.sh` + `scripts/extract/extract_drc.py` → `reports/drc.json`
- `scripts/flow/run_lvs.sh` + `scripts/extract/extract_lvs.py` → `reports/lvs.json`
- Manual fix advice lives in `references/failure-patterns.md`.

There is **no automated fixer** — unlike timing (`check_timing.py` auto-fix) and batch
backend failures (`tools/fix_orfs_failures.py`). When DRC/LVS violations appear, a human
or agent reads `failure-patterns.md` and hand-edits configs. The prior 2026-05-30 campaign
"fixed" antenna DRC by **relaxing the rule deck** (`FreePDK45.lydrc` antenna ratio
300→400), which *masks* violations rather than fixing the layout.

**Corpus baseline (design_cases, ~726 designs):**

| Check | clean | fail | other |
|-------|-------|------|-------|
| DRC   | 402   | **9** (all antenna: 7×7-viol, eth_arb_mux=133, eth_demux=147) | 271 `stuck` (KLayout polygon-op hang — tooling, not a violation) |
| LVS   | 603   | **10** `fail` + 1 `failed` | 52 `unknown`, 7 `clean_algorithmic` |

The DRC `stuck` population (271) is a KLayout runtime hang on `FreePDK45.lydrc` boolean
ops, **not** a design-rule violation. Several LVS fails are KLayout **C++ SIGSEGV**
(`db::NetlistCrossReference::sort_circuit`) requiring a KLayout ≥0.30.10 upgrade — not
fixable by us.

## 2. Goals / Non-Goals

**Goals**
- Add a **real-layout-fix** DRC/LVS fixing ability to the skill: diagnose a violation,
  apply a genuine layout/config fix, re-run the minimal flow stages, re-check, iterate.
- Auto-iterate unattended (≤3 iters/design, early-exit on no improvement), logging
  on-the-fly and summarizing after.
- Honest measurement: restore the strict 300:1 antenna deck; report residuals truthfully
  when the layout genuinely cannot be cleared.
- Validate on the known-fail set first, then expand.

**Non-Goals (this version)**
- **No rule-deck relaxation** as a fix mechanism (explicitly rejected by the user).
- **No** generic spacing/width/min-area or IR-drop/timing fixing (no current design
  exhibits non-antenna DRC fails; timing already handled by `check_timing.py`).
- **No** attempt to fix the 271 DRC-`stuck` hangs in v1 — but a **bounded Phase-0
  investigation** (2-3 designs) will decide whether a tractable workaround exists.
- **No** KLayout upgrade / C++-crash workaround — report as residual.

## 3. Locked Decisions

1. **Real layout fixes only** — never relax the rule deck; report residuals honestly.
2. **Stuck DRC:** investigate a few, then decide scope (out of v1 fixer otherwise).
3. **Validation order:** known-fail set first, then expand to large designs / broad re-run.
4. **Autonomy:** auto-iterate up to N, summarize after.
5. **Honest deck:** revert `FreePDK45.lydrc` to original 300:1 (`.orig-300ratio` backup);
   ~20 designs "cleared" by 400:1 will reopen as fail — that is the honest baseline.
6. **Strategy scope:** antenna DRC repair + LVS triage only (defer spacing/width/IR).
7. **Iteration budget:** `--max-iters` default 3, early-exit when an iteration does not
   reduce the violation count.

## 4. Grounding Facts (verified 2026-05-31)

- ORFS exposes real antenna repair: `MAX_REPAIR_ANTENNAS_ITER_GRT`/`_DRT` (default **5**),
  `SKIP_ANTENNA_REPAIR*`, and a diode cell. Repair runs `repair_antennas` in
  `global_route.tcl` + `detail_route.tcl`, reporting to `grt_antennas.log`/`drt_antennas.log`
  and metric `antenna_diodes_count`.
- nangate45 **ships an antenna diode**: `MACRO ANTENNA_X1` (in `NangateOpenCellLibrary.macro.lef`),
  **not** in `DONT_USE_CELLS`, but **not wired** as `CORE_ANTENNACELL`. So diode insertion
  is available but may be under-used by default.
- `run_orfs.sh` supports `FROM_STAGE=<stage>` to resume without `clean_all` — enables
  re-routing after a config edit instead of redoing synthesis.
- Antenna repair iters already = 5, yet 7 designs plateau at exactly 7 violations →
  **root cause of the residual-7 must be investigated** (Phase 0) to order strategies.

## 5. Architecture

Three isolated units + skill integration. Each unit has one purpose, a defined interface,
and is independently testable.

### 5.1 Diagnoser (pure / testable)
`r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py`

- **Input:** `<project-dir>` (reads `reports/drc.json`, `reports/lvs.json`,
  `constraints/config.mk`, and optionally ORFS `drt_antennas.log` if present).
- **Output (stdout JSON):** a **fix plan**:
  ```json
  {
    "check": "drc|lvs",
    "status": "fail|clean|residual|skipped|unknown",
    "violation_count": 7,
    "dominant_category": "METAL7_ANTENNA",
    "strategies": [
      {"id": "antenna_diode_iters", "rationale": "...",
       "config_edits": {"CORE_ANTENNACELL": "ANTENNA_X1",
                        "MAX_REPAIR_ANTENNAS_ITER_GRT": "10",
                        "MAX_REPAIR_ANTENNAS_ITER_DRT": "10"},
       "rerun_from": "route", "recheck": "drc"},
      {"id": "antenna_route_effort", "...": "..."},
      {"id": "antenna_density_relief", "...": "..."}
    ],
    "residual_reason": null
  }
  ```
- **`--apply <strategy-id>`:** writes that strategy's `config_edits` into a marked,
  idempotent block in `constraints/config.mk`:
  ```
  # >>> r2g signoff-fix (auto) >>>
  export CORE_ANTENNACELL = ANTENNA_X1
  ...
  # <<< r2g signoff-fix (auto) <<<
  ```
  Re-applying replaces the block (no duplication). The core classification + plan
  generation is a pure function `build_plan(drc, lvs, config) -> dict` so unit tests feed
  synthetic JSON.

### 5.2 Loop driver (orchestration)
`r2g-rtl2gds/scripts/flow/fix_signoff.sh <project-dir> [platform] [--check drc|lvs|both] [--max-iters N]`

Per check, loop ≤N:
1. Ensure `reports/{drc,lvs}.json` current (run extract if missing/stale).
2. `diagnose_signoff_fix.py` → plan. **Break** if `status` is `clean`, `skipped`,
   `residual` (no untried strategy), or no strategy remains.
3. `diagnose_signoff_fix.py --apply <next-strategy>` → edits `config.mk`.
4. Re-run minimal stages: `FROM_STAGE=<rerun_from> run_orfs.sh …`, then
   `run_drc.sh`/`run_lvs.sh`, then `extract_{drc,lvs}.py`.
5. Compare new count vs. prior; **early-exit if not strictly improved** (record verdict
   `no_improvement` and stop trying further antenna strategies).
6. Append an iteration record to `<project>/reports/fix_log.jsonl` **immediately** (flush)
   so progress survives long runtimes and crashes.

End: write `<project>/reports/fix_summary.md` (per-iteration table: strategy, before→after,
verdict, elapsed) and leave `reports/{drc,lvs}.json` at the final state. Exit code: 0 if
cleaned, 2 if residual remains, 1 on driver error.

### 5.3 Honest baseline
- Restore 300:1 deck: copy `FreePDK45.lydrc.orig-300ratio` → `FreePDK45.lydrc` in the ORFS
  install **and** the skill asset `r2g-rtl2gds/assets/platforms/nangate45/drc/`.
- Add `tools/install_nangate45_drc.sh` to install the honest 300:1 deck (idempotent;
  verifies the ratio is 300 after install). Remove/deprecate any 400:1 default.

### 5.4 Skill integration
- **SKILL.md:** in the signoff section, after DRC/LVS extraction, if `status==fail` and
  platform is fixable, call `fix_signoff.sh`. Document the "real fixes only" policy and the
  300:1 honest deck.
- **references/failure-patterns.md:** cross-reference the fixer from the "Antenna DRC
  Violations" and "LVS Mismatch" sections; note the honest-deck policy and that 400:1
  relaxation is retired.
- **references/signoff-fixing.md (new):** the fixer workflow, strategy catalog, fix-plan
  schema, log/summary formats, and residual taxonomy.
- **CLAUDE.md:** add a "Where to find X" row pointing at `references/signoff-fixing.md`.

## 6. Fix Strategy Catalog (v1)

### DRC — antenna (only real DRC-fail class in corpus)
Ordered, each a genuine layout change, re-checked after a real re-route:

| id | config_edits | rerun_from | notes |
|----|--------------|------------|-------|
| `antenna_diode_iters` | `CORE_ANTENNACELL=ANTENNA_X1`, `MAX_REPAIR_ANTENNAS_ITER_GRT=10`, `MAX_REPAIR_ANTENNAS_ITER_DRT=10` | `route` | wire the diode + give repair more passes |
| `antenna_route_effort` | `DETAILED_ROUTE_ARGS=-droute_end_iteration 10` (merge, not clobber existing) | `route` | more routing freedom to break long metal |
| `antenna_density_relief` | lower `CORE_UTILIZATION`/`PLACE_DENSITY` by a step and/or grow `DIE_AREA` | `floorplan` | spread cells so router can use more layers |

Strategy ordering may be revised by the Phase-0 root-cause finding on the residual-7.

### LVS — triage + known fixes
- `lvs_resolve_unknown`: re-run `extract_lvs.py` / inspect log to convert `unknown`→real
  status (cheap, no re-route).
- `lvs_macro_cdl`: for macro designs, apply combined-CDL `override export CDL_FILE` (per
  `failure-patterns.md` "LVS CDL_FILE Override").
- Known rule-deck mismatch patterns (device-model name, bulk-pin bloat, unused pins) are
  **global** `.lylvs` edits — the diagnoser emits them as **operator recommendations**, not
  auto-applied per-design (they affect every design).
- `lvs_residual`: KLayout C++ SIGSEGV or timeout → honest residual with reason; no fix.

## 7. Data Contracts

- **`reports/fix_log.jsonl`** — one JSON object per iteration:
  `{"iter":1,"check":"drc","strategy":"antenna_diode_iters","before":7,"after":7,
    "verdict":"no_improvement","elapsed_s":1234,"ts":"<passed-in>"}`
  (timestamps passed in by the driver; never generated inside pure code).
- **`reports/fix_summary.md`** — human-readable table + final verdict + residual reason.
- **`reports/{drc,lvs}.json`** — unchanged schema; left at final post-fix state.

## 8. Validation Plan (the "run the designs" work)

**Phase 0 — bounded investigation (before broad runs):**
- Revert deck to 300:1; re-DRC the ~29 antenna designs → honest baseline counts.
- On 1-2 residual-7 designs: inspect `drt_antennas.log` + `antenna_diodes_count` to learn
  why diodes don't clear them; fix strategy order accordingly.
- On 2-3 DRC-`stuck` designs: bounded probe (rule subset / region / KLayout flags) →
  decide whether stuck-handling enters scope. Document the decision.

**Phase 1 — fixer validation on known-fail set:**
- Run `fix_signoff.sh` on the 9 DRC-fail + reopened antenna designs + 10 LVS-fail + 52
  LVS-`unknown`. Iterate the **skill** based on what breaks (the real point of this task).

**Phase 2 — expand:**
- Broader re-run + `large_rtl_designs` (BOOM CPU, Faraday ASIC, Gaisler leon2), honoring
  the hard rules (no concurrent same-DESIGN_NAME/variant; PLACE/ROUTE_FAST for ChipTop;
  no concurrent LVS on >100K-cell designs).

**On-the-fly documentation:** each phase appends to a dated `docs/` campaign log as it runs
(long DRC/LVS runtimes mean we must not batch documentation to the end). Per the repo rule,
skill scripts/references are updated when a bug is fixed, and `docs/superpowers/{specs,plans}`
get dated notes (commit hash + superseded invariants) after each fix.

## 9. Testing

- pytest in `r2g-rtl2gds/tests/` feeding synthetic `drc.json`/`lvs.json` to
  `build_plan(...)`: assert strategy selection/order per category, residual taxonomy, and
  that `--apply` writes the expected idempotent `config.mk` block (and re-apply replaces,
  not duplicates).
- Existing 223-test suite must stay green (behavior-neutral elsewhere).
- Flow-level re-runs are validated by the corpus (Phase 1), not unit tests.

## 10. Risks & Open Questions

- **Residual-7 root cause unknown** (Phase 0 resolves). If diodes structurally cannot clear
  them, the honest outcome for those designs is "residual antenna, GDS+LVS+RCX still valid."
- **Re-route nondeterminism / runtime:** each antenna iteration is a real re-route (minutes
  on small designs, hours on large). `--max-iters 3` + early-exit bound the cost.
- **Honest-deck reclassification:** reverting to 300:1 reopens ~20 designs as fail; this is
  expected and is the correct baseline, but the dashboard/corpus numbers will shift.
- **LVS auto-fix surface is thin:** most real residuals are tool crashes; v1 LVS value is
  mostly triage + honest residual + macro-CDL.

## 11. File Manifest

New:
- `r2g-rtl2gds/scripts/reports/diagnose_signoff_fix.py`
- `r2g-rtl2gds/scripts/flow/fix_signoff.sh`
- `r2g-rtl2gds/references/signoff-fixing.md`
- `tools/install_nangate45_drc.sh`
- `r2g-rtl2gds/tests/test_diagnose_signoff_fix.py`

Modified:
- `r2g-rtl2gds/SKILL.md`, `r2g-rtl2gds/references/failure-patterns.md`, `CLAUDE.md`
- `r2g-rtl2gds/assets/platforms/nangate45/drc/FreePDK45.lydrc` (→ 300:1)

## 12. Amendments (2026-06-01, post-implementation)

The §6 antenna strategy catalog was corrected during a final integration review (both bugs
verified against the live ORFS install; commit `42d0e0b`):

- **`CORE_ANTENNACELL` dropped** — it is not an env var ORFS reads; `repair_antennas`
  auto-discovers the diode from the LEF (`ANTENNA_X1` declares `CLASS CORE ANTENNACELL`).
- **`antenna_route_effort` removed** — `-droute_end_iteration` is not a valid flag (real
  flag `-droute_end_iter`; knob `DETAILED_ROUTE_END_ITERATION` defaults to 64, so the
  specced value 10 would *reduce* routing), and DRT convergence is not an antenna lever.

The shipped antenna catalog is therefore **two** real-fix strategies:
1. `antenna_diode_iters` — `MAX_REPAIR_ANTENNAS_ITER_GRT/_DRT=10` (default 5); rerun `route`.
2. `antenna_density_relief` — lower `CORE_UTILIZATION` by 5 (floor 5); rerun `floorplan`.

Diagnoser/driver hardening from code review (commits `26d133e`, `d76daed`) is detailed in
the plan's Amendments section. Whether two strategies suffice (vs. the residual-7) is the
open question Phase 0 resolves empirically.

## 13. Amendments (2026-06-01, Phase-0/1 empirical findings)

Phase 0 resolved the open question: on **nangate45 the antenna catalog has no working real
fix** — `repair_antennas` (S1) is inert (no tech-LEF antenna rules + `ANTENNADIFFAREA 0.0`
diode) and `antenna_density_relief` (S2) is empirically *counterproductive* (fifo_basic
14→16). The diagnoser therefore returns the nangate45 antenna case as an **immediate honest
residual** (empty strategy list, documented `residual_reason`). Commits `bd2b67b`, `4d15d76`.

**New status `clean_beol` (commit `76c81b9`).** The BEOL-only DRC fallback disables BOTH the
FEOL and ANTENNA rule groups, so a 0-violation BEOL-only run only proves metal/via/cut routing
is clean. `extract_drc.py` now emits the qualified status **`clean_beol`** (not plain `clean`),
mirroring LVS `clean_algorithmic`, so status-based aggregation cannot miscount a partial check
as a full clean. `diagnose_signoff_fix.py` treats `clean_beol` as needing no fix.
- **Superseded invariant:** "0-violation DRC ⇒ status `clean`" no longer holds for BEOL-only
  runs — they are `clean_beol`.
- **Status enum (updated):** `fail | residual | clean | clean_beol | skipped | stuck |
  timeout | unknown`.

**BEOL-only validated end-to-end on real ORFS:** small/medium FEOL-hang designs that hung
for hours now complete in seconds (DMA_Controller 7.7s, ip_demux 34s → `clean_beol`). Large
designs (≥~470K inst) instead **hang on the BEOL CONTACT op** — the polygon-op-no-progress
mode migrates from the FEOL booleans to `CONTACT.1/2` (`cont.width`/`cont.space`) over millions
of contact polygons (eth_mac_1g_fifo + koios_gemm_layer froze 5–8 min at 100% CPU, RSS 7.3GB,
killed). `cont` is library-internal (P&R adds only vias), so a deeper fallback could also skip
`CONTACT.*`; deferred. BEOL-only thus unblocks the small/medium stuck majority; the large tail
stays `stuck`. See `docs/campaign_signoff_fixer_2026-06-01.md` Phase 1.
