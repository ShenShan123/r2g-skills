# Session summary — nangate45 campaign: loop-honesty bug hunt (2026-06-20 → 2026-06-23)

Resumed the nangate45 RTL→GDS signoff campaign and used it as a live bug-finder for the
`r2g-rtl2gds` skill's closed learning loop. Three honesty/correctness bugs found, fixed (TDD),
and committed on branch **`fix/loop-honesty-gates`**; the campaign kept closing the loop
(flow → fix → ingest → learn → A/B → judge) throughout.

## The campaign

- Driver: `tools/nangate45_resume_waves.sh` (PGID 4108133) running
  `engineer_loop.py run` in bounded waves so the loop closes incrementally.
- **Shared-host scaling:** user4's `finesim` pinned ~82/96 cores for days → sized `workers=3 ×
  NUM_CORES=4`. When finesim cleared, scaled to **12 → 20 → 30 workers** via a new live
  `pool.env` hook (re-read each wave, **no restart**, no orphaned ORFS trees). Diagnosed the
  throughput bottleneck as single-threaded klayout DRC (much of it the known `stuck` FEOL hang),
  so the lever is WORKERS, not NUM_CORES; capped `NUM_CORES` so worst-case all-burst stays < 96.
- Loop health stayed green across every transition: **H3 honesty invariant held** (`count(runs
  fail) == count(orfs-fail-% failure_events)`), `fix_trajectories` grew past 860 with healthy
  negative learning (≈ half `abandoned`), DRC/LVS/**timing** recipes all rode the A/B loop.

## Bugs found + fixed (all on `fix/loop-honesty-gates`)

| Commit | Bug | Root cause | Fix |
| --- | --- | --- | --- |
| `df01923` | Fail-**open** signoff exit gate: DRC `stuck` / LVS `incomplete` marked `clean` | `fix_signoff.sh` exit gate was a denylist `{fail,failed,residual,timeout}` | fail-**closed** allowlist `{clean,clean_beol,skipped}`; reconciled 11 mislabeled designs |
| `f922b02` | A/B planner blind to **successful** recipes (all `ab_trials` sky130hd; nangate45 candidates stuck forever) | `plan_trial` keyed on `run_violations` (post-fix residuals); a recipe that *clears* its symptom leaves none | new Tier-2 `_symptom_designs` resolves subjects from `fix_trajectories`/`fix_events` by `symptom_id` |
| `d29abae` | **FLW-0024** "place density > 1.0" — die too small (dominant `unseen_crash` bucket) | `setup_rtl_designs.py` sized the die from RTL **line count**, not gate count → fixed 50×50 die too small for dense designs | (a) setup uses `CORE_UTILIZATION` everywhere; (b) loop `process_one` detects FLW-0024 → rewrites `DIE_AREA→CORE_UTILIZATION=30` → retries once; honest `place_density_residual` if it still can't fit |

**Common shape:** an allowlist stays correct as the domain grows; a denylist / residual-only
signal silently fails open. Each fix re-labels a known class out of a catch-all so the learner
sees real signatures (mirrors the earlier `route_congestion_residual` re-label).

### Validation
- Suite **698 → 709 passed** (11 new tests; the 2 remaining errors are pre-existing techlib
  golden-baseline setup failures from the corpus rebuild, not these changes).
- A/B fix verified live: `ab_nangate` 0 → 8, the loop self-drained the nangate45 candidates.
- FLW-0024 fix verified end-to-end on real escalated `apb_ram`: *died-at-place / zero layout* →
  resize → reflow → **produced `6_final.gds`**.

## Backlog re-queue (this session's closing action)
- The place `unseen_crash` bucket split into **8 FLW-0024** (cell-area; recoverable now) + **30
  PPL-0024** (pin-perimeter — a *different* die-sizing bug; auto-sizing would make it worse).
- **Re-queued the 8 FLW-0024 designs** to `pending` so the campaign drains them through the new
  loop recovery (escalated 143 → 135; pending → 392). The PPL-0024 designs were correctly left
  escalated.

## Campaign schedule — HALTED after wave 3 (2026-06-23)

The campaign was **stopped after wave 3** by request. Wave 3 (40 designs + its end-of-wave A/B
drain of the `period_relax` timing recipe on subjects `iccad2015_unit16_in1` / `unit18_in1`,
k=2 arms) was **allowed to finish**; **wave 4 was prevented** from starting by killing only the
driver loop process (`tools/nangate45_resume_waves.sh`, PID 4108133) while leaving the wave-3
flows running (they reparented to init). No ORFS tree was orphaned/killed.

State at halt:

| ledger state | count |
| --- | --- |
| pending (deferred to next run) | **392** |
| clean | 221 |
| escalated | 135 |
| in-flight (wave 3, finishing) | 8 |

- The **8 re-queued FLW-0024 designs** (`dma_controller`, `ifft_core`, `delay_lattice_rb`,
  `tracker_pool`, `fifo_basic`, `adder_tree`, `diffeq2`, `fpu_floating_multiplication`) sit in the
  392 `pending` — they were NOT reached before the halt and will be recovered by the FLW-0024
  loop fix on the **first wave of the next run** (fresh process imports the fixed `engineer_loop`).
- The `period_relax` A/B verdict from wave 3's drain lands in `ab_trials` when its arms finish
  (both subjects are `stuck`-DRC designs, so an honest `inconclusive` is the likely outcome).

**Resume schedule (next run):** re-launch the wave driver — it picks up the 392 `pending` from the
same ledger and reads `pool.env` for sizing:

```
# size to free cores; pool.env (workers/NUM_CORES/WAVE_MAX) is re-read each wave
setsid nohup bash tools/nangate45_resume_waves.sh > tools/_nangate45_resume_logs/driver.out 2>&1 &
```

Before resuming, **checkpoint the learned store** (`git commit` the live `knowledge.sqlite` /
`heuristics.json` as a `data(knowledge):` commit) — safe to do now that the loop is idle.

## Open / next targets (characterized, not yet fixed)
1. **PPL-0024 (~30):** pin-perimeter under-sizing at `3_2_place_iop` — needs a die-enlarge-by-pins
   lever (the sky130 `mk_*_project` fix exists; wire the equivalent into nangate45 setup).
2. **Synth `unseen_crash` (~34):** missing `#include` headers (`Can't open include file`) +
   `SYNTH_MEMORY_MAX_BITS` exceeded — need header harvesting / cap-raise, not the FLW-0024 lever.

## Artifacts
- Skill changes: `r2g-rtl2gds/scripts/flow/fix_signoff.sh`, `knowledge/ab_runner.py`,
  `scripts/loop/engineer_loop.py`, `tools/setup_rtl_designs.py`; tests
  `test_fix_signoff_clean_gate.py`, `test_ab_fixhist_subjects.py`, `test_flw0024_recovery.py`,
  `test_setup_sizing.py`; `references/failure-patterns.md` (3 new sub-variants);
  refactor-plan tripwire addenda.
- Operator tooling (uncommitted, scratch): `tools/_prove_nangate45_ab.py`,
  `tools/_prove_flw0024_recovery.py`, `tools/nangate45_resume_waves.sh` (+ `pool.env` live-tuning).
- Uncommitted by design: live `knowledge.sqlite` / `heuristics.json` (mid-campaign data — commit
  as a `data(knowledge):` checkpoint when the campaign halts).
