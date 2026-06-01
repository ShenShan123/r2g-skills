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

### Residual antennas root cause (2026-06-01) — STALE NO-DIODE ARTIFACTS

Inspecting `fifo_basic`'s preserved backend (`RUN_2026-05-19`):
- `grt_antennas.log` / `drt_antennas.log` are **empty**; final DEF has **0** `ANTENNA_X1`
  placements; both route logs carry `[WARNING GRT-0246] No diode with LEF class CORE
  ANTENNACELL found.` → **OpenROAD inserted zero diodes**; antenna "repair" was a near-no-op,
  leaving the residual antennas the corpus shows.
- BUT a headless probe of the *current* install (`read_lef` tech + `macro.mod.lef`) finds
  `ANTENNA_X1 type=CORE_ANTENNACELL` (1 master). The diode-class LEF (`.macro.mod.lef`,
  `CLASS CORE ANTENNACELL`) is **stock ORFS** (in ORFS git, not a local mod) and is the
  configured `SC_LEF`.
- Conclusion: the 2026-05-19 corpus runs predate an OpenROAD rebuild that now recognizes the
  antenna diode. **The residual antennas are stale; a fresh re-route should actually insert
  diodes and clear them.** The fixer's value is to trigger that fresh, diode-enabled re-route
  (S1 also raises repair iters). To be confirmed empirically on `fifo_basic` next.
- **Skill implication:** the dominant nangate45 antenna "fail" population is an artifact of
  old no-diode runs, not unfixable layout. The real fix is a fresh diode-enabled route —
  which is precisely what `fix_signoff.sh` does. (No rule relaxation needed or used.)

### LVS "unknown" population (52) characterized (2026-06-01)

Sampled all 52: every one has `6_lvs.log` + `lvs_run.log` but **no `6_lvs.lvsdb`** and no
match/mismatch verdict → `extract_lvs.py` returns `unknown`.
- **6 are hard KLayout crashes** (signal 11 / SIGSEGV / Ruby-interpreter backtrace +
  `~/.klayout/klayout_crash.log`), e.g. `fifo_basic`, `verilog_axi_axi_fifo_wr`,
  `wb2axip_aximwr2wbsp`, `secworks_sha256_…_axi4_slave`. Not fixable without KLayout upgrade.
- **46 "other"** extract devices OK then die at `FreePDK45.lylvs:246` (netlist build) with no
  verdict — killed/crashed mid-LVS (no signal string captured).

**Skill-improvement finding (to apply):** `extract_lvs.py` classifies crashers as `unknown`,
so the diagnoser emits `lvs_resolve_unknown` and would **re-run a job that just re-crashes**.
Fix: detect the crash signature (signal 11 / `klayout_crash.log` / ruby backtrace, and the
"extracted-but-no-verdict/no-lvsdb" pattern) and classify as `klayout_cpp_crash` residual so
the fixer does NOT waste an expensive re-run. → tracked as improvement #1.

### fifo_basic fixer smoke test (2026-06-01) — THREE findings

Ran `fix_signoff.sh fifo_basic --check drc` end-to-end (full re-route + DRC, 28 min). The
driver worked (applied S1, re-routed, re-DRC'd, compared, early-exited) but surfaced 3 bugs/
truths. Final: 98→98 "no_improvement", residual.

**Finding A — DRC count inflated ~7×.** `6_drc_count.rpt` (ORFS `grep -c "<value>"`) = 98, but
the true KLayout `<item>` count = **14** (METAL4=3, METAL5=3, METAL6=8). `extract_drc.py`
carries the inflated value as `total_violations`. So fifo_basic really has **14** antenna
violations at 300:1, not 98; the whole corpus's antenna counts are ~7× inflated. → fix #2.

**Finding B — the nangate45 antenna repair flow is fundamentally inert (root cause).**
On the fresh routed ODB: OpenROAD `check_antennas` reports **0 net / 0 pin violations**;
`repair_antennas ANTENNA_X1` → `ERROR GRT-0244: Diode ANTENNA_X1/A ANTENNADIFFAREA is zero`;
0 `ANTENNA_X1` placed; `GRT-0246 No diode found` recurs. Confirmed in the LEF:
`ANTENNA_X1` pin A has `ANTENNADIFFAREA 0.0` (`# unknown`), and the nangate45 **tech LEF has
no antenna rules** (`grep -ci ANTENNA` = 0). So:
  - OpenROAD detects 0 antenna violations (no rules) → `repair_antennas` inserts nothing even
    in principle;
  - the only diode cell has zero diffusion area → unusable even if violations were detected.
  → **`repair_antennas` (strategy S1) can NEVER fix nangate45 antennas.** These violations are
  visible only to KLayout's `FreePDK45.lydrc` (300:1). The 2026-05-30 400:1 relaxation was
  effectively dragging KLayout toward OpenROAD's "0 antennas" view (masking). Under real-fixes-
  only, the tractable lever is **layout relief** (density/area/reroute) — strategy S2 — or an
  honest residual. (Enabling OpenROAD antenna repair would require inventing tech-LEF antenna
  rules + a non-zero-diffarea diode — i.e. fabricating data — which is out of "real fixes"
  scope.) → drives strategy reorder + scope note.

**Finding C — early-exit abandoned escalation.** The driver stopped after S1's no_improvement
and never tried S2 (density relief). `no_improvement` should advance to the NEXT strategy, not
abandon the check. → fix #3.

### Stuck-DRC probe (2–3 designs)
_Deferred — antenna findings took priority; will probe after the fixer escalation fix._

## Skill improvements identified (from runs)

1. **extract_lvs.py crash classification** — mark signal-11/crash-log/no-verdict LVS as a
   crash residual, not `unknown`, so the fixer skips a doomed re-run. (LVS-unknown = 6 hard
   crashes + 46 extracted-but-no-verdict.) [pending]
2. **extract_drc.py true count** — `total_violations` should be the KLayout `<item>` count
   (sum of category counts), not ORFS's inflated `grep -c "<value>"` count.rpt (~7× over). Keep
   the raw marker count as a separate field. Corpus-wide honesty fix. [applying]
3. **fix_signoff.sh escalate** — on `no_improvement`, advance to the NEXT strategy instead of
   abandoning the check; terminate only on clean / STOP (strategies exhausted) / max-iters /
   run_orfs-fail. [applying]
4. **nangate45 antenna catalog = density relief only** — `repair_antennas` (diode_iters) is
   inert on nangate45 (Finding B: no tech-LEF antenna rules + zero-diffarea ANTENNA_X1). Skip
   it so the fixer doesn't waste a ~28-min re-route; go straight to the tractable layout lever.
   Keep diode_iters for platforms with a working diode. [applying]
5. **Document nangate45 antenna reality** in failure-patterns + signoff-fixing (Finding B).
   [pending docs]

## Phase 1 (known-fail set) — pending

## Phase 2 (large_rtl_designs) — pending
