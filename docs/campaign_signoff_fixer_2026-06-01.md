# Signoff-Fixer Campaign — 2026-06-01 (on-the-fly log)

Validating the new DRC/LVS violation-fixing ability (`fix_signoff.sh` +
`diagnose_signoff_fix.py`) on the corpus, and improving the skill from what the runs reveal.
Policy: **real layout fixes only** (no rule-deck relaxation). Spec/plan:
`docs/superpowers/{specs,plans}/2026-05-31-drc-lvs-fixer*.md`.

## Ability (shipped, branch `all_tech_feat_label_extract`)

| commit | what |
|--------|------|
| `e9166d2` | honest 300:1 nangate45 antenna deck + `tools/install_nangate45_drc.sh` |
| `37439c5`→`26d133e` | `diagnose_signoff_fix.py` (pure plan + CLI), hardened |
| `b51312d`→`d76daed` | `fix_signoff.sh` iterative driver, hardened (run_orfs-fail detect, extract, tab-parse) |
| `42d0e0b` | antenna catalog corrected to real ORFS knobs (2 strategies) |
| `ce35f0a` | docs: `references/signoff-fixing.md`, SKILL.md, failure-patterns |

Antenna catalog (real fixes): **S1 `antenna_diode_iters`** (`MAX_REPAIR_ANTENNAS_ITER_GRT/_DRT=10`,
default 5; rerun route) → **S2 `antenna_density_relief`** (lower `CORE_UTILIZATION` −5; rerun
floorplan). LVS: triage `unknown`, macro-CDL (operator), honest residual on KLayout C++ crash.

## Key sequencing finding (2026-06-01)

After `install_nangate45_drc.sh` flips the deck 400:1→300:1, existing `reports/drc.json` are
**stale** (measured at the old ratio). `fix_signoff.sh` diagnoses the *current* report, so a
re-DRC must refresh the baseline before fixing — otherwise the loop compares a 400:1 "before"
against a 300:1 "after" and mis-fires `no_improvement`. **Convention: run `run_drc.sh` + extract,
THEN `fix_signoff.sh`.** (Candidate skill improvement: a `--recheck-first` flag to force an
initial DRC; deferred pending Phase-1 evidence.)

## Honest-baseline reference (from `/tmp/wave_f1_results.tsv`, the 29 antenna designs)

`pre` = count at honest 300:1; `post` = count at masked 400:1. Under the restored 300:1 deck
the *full* `pre` population reopens as antenna-fail. The 9 that survived even 400:1 (post>0)
are the hard "residual-7" set + the two ethernet designs.

Hard residual set (post>0 @ 400:1): fifo_basic(98→7), cv32e40p stream_register(7→7),
pyocd stream_register(7→7), iccad2017_unit18_F(7→7), iccad2017_unit2_G(7→7),
riscv_alu4b(14→7), microcontroller_cpu(7→7), eth_arb_mux(161→133), eth_demux(231→147).

## Run log

| design | check | baseline (300:1) | after fix | strategy path | verdict | notes |
|--------|-------|------------------|-----------|---------------|---------|-------|
| PicoRV32_…_fifo_basic | drc | _re-DRC running_ | | | | honest-baseline smoke test |

## Phase 0 findings

- **Residual-7 root cause:** _TBD — inspect drt_antennas.log + antenna_diodes_count._
- **Stuck-DRC probe (2–3 designs):** _TBD — decide scope._

## Phase 1 (known-fail set) — pending

## Phase 2 (large_rtl_designs) — pending
