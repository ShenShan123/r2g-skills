# Changelog

Curated, reverse-chronological history of the `agent-with-OpenROAD` / `r2g-rtl2gds`
project. This file consolidates the campaign reports, signoff findings, and
design plans/specs that previously lived as standalone files under `docs/`.

> **Source-merge note (2026-06-01):** This file consolidates 27 dated documents
> from `docs/` — campaign logs, wave findings, batch reports, the
> `superpowers/{plans,specs}/` design docs (2026-03-28 → 2026-05-31), and the
> `signoff_snapshot_2026-05-27.json` data file — which were then deleted. Each
> entry below notes the file(s) it came from. The only doc kept as a live
> standalone file is the **2026-06-01 signoff-fixer** on-the-fly campaign log
> (`docs/campaign_signoff_fixer_2026-06-01.md`), which is still being appended to.

Status legend used throughout: **clean** (no violations), **stuck** (KLayout
polygon-op deadloop; GDS still valid), **clean_algorithmic** (LVS comparator
graph-isomorphism false-fail), **clean_beol** (BEOL-only DRC; FEOL/ANTENNA
skipped as library-pre-verified).

---

## 2026-06-02 — DRC band finish + honest LVS mismatch classification
*(on-the-fly log: `docs/campaign_signoff_fixer_2026-06-01.md` "Phase 2 continued"; skill commit `11cebfb`)*

Converted the 10 tractable `stuck` DRC designs (228K–406K) to `clean_beol` (the
361K–406K ones need ~60–70 min each — the prior 2400s wall was too short, not a
hang). **DRC stuck 17 → 7** (only the verified-intractable ≥465K METAL-hang tier
remains, incl. 3× BOOM). Corpus DRC honest-verdict coverage **99.0% (675/682)**.
Host reality: 1.1 TB / 96 cores ⇒ the historical `jobs 3` RAM caution is obsolete;
bound batch parallelism by KLayout per-design single-thread + memory bandwidth.

Triaged all 11 LVS `fail`/`failed`: the population is **overwhelmingly
KLayout-0.30.7 tooling limitation, not real layout defects** (mirrors the DRC
FEOL-hang story). cordic recovered to `clean` (stale cross-platform log);
core_usb_host_top reclassified `crash` (SIGSEGV); the rest are **symmetric-matcher
residuals** (mis-paired interchangeable instances in symmetric logic) plus one
real connectivity defect (wb2axip_axi2axilite). **Empirically disproved** that
raising the comparer budget (`max_depth`/`max_branch_complexity`) fixes them — it
only removes the "Maximum depth exhausted" warning, not the mismatches.

Skill (`11cebfb`): `extract_lvs.py` adds a conservative `mismatch_class`
{symmetric_matcher | real_connectivity | generic}; `diagnose_signoff_fix.py`
emits precise honest residuals (`lvs_symmetric_matcher_residual` /
`lvs_real_connectivity_mismatch`) and never spawns a doomed re-run for symmetric
fails; `FreePDK45.lylvs` comparer budget is env-tunable (defaults restored,
documented as a non-lever). 6 new tests; no rule-deck relaxation anywhere.

---

## 2026-05-31 — DRC/LVS violation-fixing ability (plan + spec)
*(from `superpowers/plans/2026-05-31-drc-lvs-fixer.md`,
`superpowers/specs/2026-05-31-drc-lvs-fixer-design.md`)*

Added a **real-layout-fix** signoff fixer to the skill (the on-the-fly
validation of which is the kept `campaign_signoff_fixer_2026-06-01.md`). Policy:
**real fixes only — never relax the rule deck** (explicitly reversing the
2026-05-30 antenna 300→400 masking).

**Architecture — three isolated units:**
- `scripts/reports/diagnose_signoff_fix.py` — pure/testable `build_plan(drc, lvs,
  config) → fix-plan`; `--apply <strategy>` writes an idempotent marked block into
  `constraints/config.mk`.
- `scripts/flow/fix_signoff.sh` — loop driver: diagnose → apply → `FROM_STAGE`
  re-run → re-check → compare, ≤3 iters with early-exit on no improvement;
  appends `reports/fix_log.jsonl` per iteration and writes `reports/fix_summary.md`.
  Exit 0 cleaned / 2 residual / 1 driver error.
- Honest 300:1 deck restored in both the skill asset and ORFS install +
  `tools/install_nangate45_drc.sh` (verifies the ratio on install).

Corpus baseline at spec time (~726 designs): DRC 402 clean / 9 fail (all antenna)
/ 271 stuck; LVS 603 clean / 10 fail+1 failed / 52 unknown / 7 clean_algorithmic.

**Amendments (2026-06-01, post-implementation), folded in from the spec:**
- *Catalog correction (`42d0e0b`):* dropped `CORE_ANTENNACELL` (not an ORFS env
  var — the diode is auto-discovered from the LEF) and removed
  `antenna_route_effort` (invalid flag; would reduce routing). Shipped catalog =
  **2** real strategies: `antenna_diode_iters` (`MAX_REPAIR_ANTENNAS_ITER_GRT/_DRT
  =10`, rerun route) and `antenna_density_relief` (`CORE_UTILIZATION` −5, rerun
  floorplan).
- *Phase-0/1 findings (`bd2b67b`, `4d15d76`):* on **nangate45 the antenna catalog
  has no working real fix** — `repair_antennas` is inert (no tech-LEF antenna
  rules + `ANTENNADIFFAREA 0.0` diode) and density relief is counterproductive
  (fifo_basic 14→16). The diagnoser now returns nangate45 antenna fails as an
  **immediate honest residual** (empty strategy list + `residual_reason`).
- *New status `clean_beol` (`76c81b9`):* the BEOL-only DRC fallback disables FEOL
  **and** ANTENNA, so a 0-violation BEOL-only run is emitted as the qualified
  `clean_beol` (not plain `clean`), mirroring LVS `clean_algorithmic`. Status enum
  is now `fail | residual | clean | clean_beol | skipped | stuck | timeout |
  unknown`. Validated on real ORFS (DMA_Controller 7.7s, ip_demux 34s →
  `clean_beol`); ≥~470K-instance designs instead hang on the BEOL `CONTACT.*` op
  and stay honest `stuck`.

## 2026-05-30 — Wave campaign final: +49 newly-clean designs
*(from `campaign_2026-05-30_final.md`)*

Closed the multi-day signoff sweep launched 2026-05-27. Net **49 newly-clean
designs** added over the 2026-05-27 baseline (582 LVS / 381 DRC clean):

| Source | Count | Mechanism |
|--------|------:|-----------|
| Wave A DRC re-runs | 1 | Riscy_SoC_rtl_cpu_csrs flipped unknown→clean |
| Wave B small-LVS retries | 2 | KLayout Signal-11 transient recovery |
| Wave Cm medium-LVS | 19 | ICCAD2015 family + poly1305, koios, FIR |
| Wave Cl large-LVS | 0 | All 21 hit the 4h LVS_TIMEOUT (need 8h+) |
| F1 antenna DRC fix | 20 | `FreePDK45.lydrc` antenna ratio 300→400 |
| F2/F3 LVS reclassification | 7 | comparator algorithmic limit, not real bugs |

Projected corpus state: **LVS 610/682 (89.4%)**, **DRC 402/682 (58.9%)**,
RCX 681/682.

**Platform-level skill-asset fixes applied:**
- `assets/platforms/nangate45/drc/FreePDK45.lydrc` — antenna ratio 300→400 on
  all 10 metal layers (cleared 20/29 antenna designs; 5 retained a hard
  residual-7, 4 partially improved).
- `assets/platforms/nangate45/lvs/FreePDK45.lylvs` — `begin/rescue` + explicit
  `report_lvs(..., true)` after `compare` so an lvsdb is written on mismatch
  (lvsdb production 0/21 → 12/21).
- LVS reclassification policy: 7 `instance_pairing_failure` designs →
  `clean_algorithmic` (iscas85_c1355/c499, vtr_common_bram/1r2w,
  wb2axip_axilsingle, axis_baser_tx_64, axil_crossbar_wr).

**Documented residual blockers:** ~231 KLayout polygon-op `stuck`; 5 KLayout
0.30.7 `gen_log_entry` SIGSEGV designs (need KLayout ≥0.30.10); ~30 large-LVS
4h-timeout designs; 1 genuine real-fail (`vlsi_axi_slave`, missing DLL_X1).

> Note: the 300→400 antenna relaxation was later re-examined and reverted to an
> honest 300:1 deck — see the kept `campaign_signoff_fixer_2026-06-01.md`.

## 2026-05-30 — Extract `techlib` restructure (plan + spec)
*(from `superpowers/plans/2026-05-30-extract-techlib-restructure.md`,
`superpowers/specs/2026-05-30-extract-techlib-restructure-design.md`)*

Behavior-neutral refactor consolidating every per-platform concern in
`scripts/extract/` (tap cells, supply voltage, cell-name→id, routing layers,
liberty parse) into one shared `scripts/extract/techlib/` package imported by
both the label and feature workers; `resolve_platform_paths.sh` became a thin
shim over `python3 -m techlib.resolve`. ORFS platforms only (nangate45,
sky130hd/hs, asap7, gf180, ihp-sg13g2); no generic-PDK abstraction.

**Gate:** byte-for-byte identical CSVs on `aes_core` (nangate45) + `cordic`
(sky130hd), covering both cell-type strategies, both layer schemes, and two
voltages. Established that `feature_test_v3/` is the pre-refactor *ancestor* of
`features/` (do not merge — the skill supersets it; v3 collapses `num_layer`/
`cell_type_id` off-nangate).

**Post-restructure correctness fixes (out of the byte-neutral scope):**
- `363a8b2` — sky130 quoted liberty cell-name tokens never matched DEF master
  keys, collapsing area/power/cell_type to 0/UNKNOWN on every sky130 cell.
- `c9d284f` (2026-05-31) — asap7/gf180 block-form `leakage_power () { value : X }`
  (gf180 quotes it) + asap7 INVBUF missing trailing `;` on `area` zeroed
  power/area; fixed in `techlib.liberty` (scalar form still wins, so
  nangate45/sky130/ihp stay byte-unchanged). Verified power>0: gf180 229/229,
  asap7 42/42.

## 2026-05-29 — Feature-extraction stage (plan + spec)
*(from `superpowers/plans/2026-05-29-feature-extraction-stage.md`,
`superpowers/specs/2026-05-29-feature-extraction-stage-design.md`)*

Added the **X (feature) side** of the ML dataset as a post-flow stage
(`scripts/flow/run_features.sh`), mirroring `run_labels.sh`. Eight fail-soft
workers emit a typed graph from the same `6_final.def` so rows join the label
CSVs row-for-row:

- `metadata.csv` (one row/design), `nodes_gate.csv`, `nodes_net.csv`,
  `nodes_iopin.csv`, `nodes_pin.csv`, `edges_gate_pin.csv`, `edges_pin_net.csv`,
  `edges_iopin_net.csv`, plus `reports/features_stats.json`.
- `graph_id` joins to labels' `Design`; `inst_name`/`net_name` join nodes↔edges
  and to labels' `Cell`/`Net`.

Light refactor of the untracked `feature_test_v2/py/` workers into
`scripts/extract/features/`: re-rooted paths, injected platform liberty/LEF,
translated comments to English, parameterized the nangate-specific constants
(cell-type map, layer regex, taps, V_nom) **as no-ops on nangate45**, and
deduped DEF/SDC helpers. Gated by a **byte-for-byte golden regression** against
`feature_test_v2/output/ac97_top/`. Stdlib only; corpus aggregation / knowledge
ingest / dashboard deferred.

## 2026-05-28 — Label-extraction stage (plan + spec)
*(from `superpowers/plans/2026-05-28-label-extraction-stage.md`,
`superpowers/specs/2026-05-28-label-extraction-stage-design.md`)*

Added the **Y (label) side** dataset stage (`scripts/flow/run_labels.sh`),
patterned on `run_rcx.sh`. Four fail-soft workers emit per-cell/per-net
regression-target CSVs + `reports/labels_stats.json`:

| Metric | Worker | Label transform |
|--------|--------|-----------------|
| Congestion | `extract_congestion.py` | `label = sqrt(cell_congestion)` |
| Wirelength | `extract_wirelength.py` | `label = log1p(len_um)`; `mask_wl = NetType==SIGNAL` |
| Timing | `extract_timing.tcl` | `label = log(1+path_delay)`, `path_delay = clk_period − worst_slack` |
| IR drop | `extract_irdrop.tcl` | `label = log(1 + ir_drop/P95)` |

New `resolve_platform_paths.sh` resolves liberty/LEF/voltage via an ORFS
`make --eval` dump (glob fallback) so all six ORFS platforms work, not just
nangate45. Migrated the four previously-untracked `extract_label/` scripts in,
generalizing layer parsing (`TYPE ROUTING`, not `metal*`) and liberty loading.
Stdlib only; corpus aggregation / knowledge ingest / dashboard deferred.

## 2026-05-28 — Wave campaign session reports
*(from `campaign_2026-05-28_progress.md`, `campaign_session_2026-05-28_final.md`)*

Mid-campaign snapshots of the 682-design DRC/LVS sweep. The session added **22
newly-clean** (1 DRC + 21 LVS), completed all 40 Wave-A DRC re-runs (confirming
the "DRC unknown" bucket is almost entirely the known KLayout polygon-op `stuck`
pattern — only Riscy_SoC_rtl_cpu_csrs flipped clean), and drained the medium-LVS
wave (ICCAD2015 family dominant). Large-LVS (300K–1M cells) all hit the 4h
LVS_TIMEOUT. Projected LVS ceiling ~91% once Cm/Cl drained; DRC ceiling ~56%
without a fix to the polygon-op hang. Recurring skill recommendations recorded:
global LVS lockfile, cache `drc.json` status=fail, dedupe wave partitioning.

## 2026-05-28 — Wave F2 LVS diagnosis (verbose lvsdb)
*(from `wave_f2_lvs_diagnosis_results.md`)*

Patched `FreePDK45.lylvs` to emit an lvsdb even on mismatch, then re-ran the 17
Wave-E + 4 Signal-11 designs: lvsdb production 0 → **12** (full per-net mismatch
detail). Pattern distribution from the 12 lvsdbs:
- **instance_pairing_failure (7)** — equal cell counts both sides; KLayout's
  bipartite matcher can't break symmetric subgraphs (NAND chains, register
  arrays). Comparator false-fails → reclassify `clean_algorithmic`.
- **paired_celltype_mismatch (3)** — incl. NAND2_X1↔NAND2_X2 drive-strength ECO
  drift between late routing fixes and `write_cdl`.
- **circuit_celltype_mismatch (1, REAL)** — `vlsi_axi_slave`: CDL has 19 DLL_X1,
  GDS 18 (`MEMORY[30][0]$_DLATCH_N_` dropped, likely by `repair_design`).
- **lay_has_extra_nets (1)** — `wb2axip_axi2axilite`, 2 floating nets.
- **SIGSEGV in `gen_log_entry` (5)** — KLayout 0.30.7 C++ crash a Ruby
  `begin/rescue` can't catch; durable fix is KLayout ≥0.30.10.

## 2026-05-27 — Signoff snapshot report + frozen data
*(from `signoff_2026-05-27.md` and the `signoff_snapshot_2026-05-27.json` data file,
snapshot timestamp 2026-05-28T02:14Z)*

First full-corpus signoff baseline after LVS rule deployment + backfill:
**LVS 582/682 (85.3%)**, **DRC 381/682 (55.9%)**, **RCX 681/682 (99.85%)**.
LVS jumped 0→85% because upstream ORFS ships an empty nangate45 `lvs/` dir; the
skill now bundles a working `FreePDK45.lylvs` (commit `c5770d5`, adapted from
laurentc2/FreePDK45_for_KLayout). The single missing RCX is `boom_smallseboom`
(intractable at route). Campaign commits: `6415399` (`_restage_for_signoff.sh`),
`c5770d5` (LVS rule + installer), and others.

Frozen distributions from the snapshot JSON (682 designs with PPA):

| Check | Breakdown |
|-------|-----------|
| DRC | clean 381, stuck 232, unknown 37, fail 29, missing 3 |
| LVS | clean 582, unknown 72, fail 16, failed 1, missing 11 |
| RCX | complete 681, missing 1 |

DRC `stuck`-by-rule: `FreePDK45.lydrc:131` ×117, `:91` ×93, `:121` ×20, `:58` ×1,
`:361` ×1 (the KLayout polygon-op deadloop). DRC `fail` (29) are all metal-antenna
(worst: eth_demux 231, eth_arb_mux 161, PicoRV32 fifo_basic 98). LVS `unknown`
top entries are the large axis/ethernet designs (240K–242K cells) that exceeded
the 1h cap. The JSON also carried the full `lvs_fail_designs` (16),
`drc_violation_designs` (29), and `lvs_unknown_top20` lists.

## 2026-05-27 — Wave D & E platform-blocker findings
*(from `wave_d_findings_2026-05-27.md`, `wave_e_findings_2026-05-27.md`)*

- **Wave D (antenna DRC, 30 designs):** OpenROAD `repair_antennas` reports 0
  violations, but KLayout `FreePDK45.lydrc` uses stricter geometric antenna
  ratios than the LEF-encoded values and still flags 7–231. Verified a full
  re-route of `Canakari_Verilog_bittiming2` leaves pre==post==7. Concluded a
  platform-rule artifact, not a per-design fix. *(Later superseded: the F1
  300→400 ratio relaxation cleared 20/29; see the 2026-05-30 entry.)*
- **Wave E (LVS real-fails, 17 designs):** all hit `ERROR : Netlists don't
  match` with no lvsdb, so no per-net detail. Identified the KLayout 0.30.7
  SIGSEGV in `NetlistCrossReference::sort_circuit`→`gen_log_entry` as the shared
  root cause for both the clean-exit mismatches and the 5 Signal-11 crashers.
  Motivated the Wave F2 lvsdb-on-failure patch.

## 2026-04-26/27 — batch2rtl campaign (BOOM / Faraday / Gaisler)
*(from `batch2rtl_report.md`, `batch2rtl_pass2.md`, `faraday_viability.md`)*

Brought the `batch2rtl/` vendor sets into the flow:
- **Faraday DMA** — full flow + RCX (DRC stuck); RTL fixup `int`→`int_w` (SV
  reserved keyword as a wire name) hardened `validate_config.py`.
- **Faraday RISC** — viable with behavioral SRAM stubs (87,680 bits across 8
  cuts; largest 16K < the 32K ABC ceiling); dual-clock SDC handled with
  `set_clock_groups -asynchronous`. Corrected the earlier "intractable" verdict
  (which assumed MB-class SRAMs that aren't in the actual RTL).
- **BOOM SmallSEBoom** — ABC blowup escaped via `SYNTH_HIERARCHICAL=1 +
  ABC_AREA=1` (43-min synth vs prior 4h timeout).
- **Faraday DSP** — not viable behaviorally (EEPROM 2 Mb, ECM32kx24 786 Kb need
  a fakeram tiler that doesn't exist); added `fix_synopsys_port_widths.py`.
- **Gaisler leon2** — hard skip (VHDL; local Yosys lacks GHDL/Verific).

## 2026-04-13 → 2026-04-20 — ORFS 495-design batch (passes 1–4)
*(from `batch_orfs_completion_report.md`, `batch_orfs_retry_report.md`,
`batch_pass3_report.md`, `batch_pass4_report.md`)*

Drove all 495 `rtl_designs/` designs through the full ORFS backend, iterating
failure-fix passes:

| Pass | Date | Cumulative ORFS pass | Rate |
|------|------|---------------------:|-----:|
| 1 | 04-13 | 402 | 81.2% |
| 2 | 04-14 | 461 | 93.1% |
| 3 | 04-19 | 476 | 96.2% |
| 4 | 04-19/20 | up to ~492–494 | up to ~99.4% |

- **Pass 1** catalogued 93 failures into 6 root-cause buckets (place-density,
  memory-inference, timeout, missing include, PDN strap, misc). Built
  `setup_rtl_designs.py` and `batch_orfs_only.sh` (per-case locking unblocked
  8× parallelism for shared-`DESIGN_NAME` ICCAD designs).
- **Pass 2** added `fix_orfs_failures.py` (root-cause classifier + config
  rewriter) rescuing 59/93; per-FLOW_VARIANT isolation of the ORFS design dir
  fixed a `config.mk`-clobber concurrency bug.
- **Pass 3** added route-stage resume (7/7), wrong-top-module detection (2/3),
  and recovered 6 missing `\`include` headers from upstream repos.
- **Pass 4** key insight: "no progress markers" in `global_place.tcl`'s
  timing-driven resizer is **CPU-bound work, not a hang** — never cancel <2h for
  >500K-instance designs. Place budget scales with cell count (14400s≈200K,
  28800s≈1.1M, 57600s≈1.25M). Permanent gaps: `koios_lenet` (HLS megadesign),
  `clog2_test` (zero-logic), `arm_core` (resizer doesn't converge ≤16h).
  Confirmed nangate45 ships no LVS rule (LVS auto-skipped) and KLayout DRC times
  out on ethernet-scale FEOL.

## 2026-04-11/12 — Knowledge store + skill-improvement plans
*(from `superpowers/plans/2026-04-11-knowledge-store.md`,
`2026-04-12-openspace-inspired-knowledge-evolution.md`,
`2026-04-11-r2g-rtl2gds-skill-improvements.md`)*

- **Knowledge store (Phase 2):** a `knowledge/runs.sqlite` populated by
  `ingest_run.py` from the per-flow JSON artifacts, with `learn_heuristics.py`
  (empirical per-family bounds for `suggest_config.py`) and `mine_rules.py`
  (failure-signature review queue). No deterministic script replaced; SQLite
  version DAG deferred to Phase 3.
- **OpenSpace-inspired evolution:** four further `knowledge/` modules — config
  lineage table, health monitor, BM25 semantic failure search, and an execution
  analyzer that turns failed runs into config fix proposals. Stdlib-only BM25.
- **Tiered timing gate:** `check_timing.py` reads `ppa.json` and classifies on
  the worse of WNS/TNS tiers (clean/minor/moderate/severe/unconstrained) — auto
  -fix minor (bump clock by |WNS|+1ns, re-run), stop-and-ask for moderate+.

## 2026-03-28 / 2026-03-30 — Foundational skill-fix plans
*(from `superpowers/plans/2026-03-28-fix-skill-scripts-and-layout-quality.md`,
`2026-03-30-improve-pd-success-and-quality.md`)*

- **2026-03-28:** fixed 3 extraction/diagnosis bugs — `extract_lvs.py`
  false-clean (KLayout lvsdb is `#%lvsdb-klayout` text not XML; log uses the
  contraction "don't match"), `extract_ppa.py` reading timing/power from
  `6_report.json` instead of regex on flow.log, and `build_diagnosis.py` false
  positives; documented antenna/hold/IR-drop/unconstrained failure patterns.
- **2026-03-30:** four-tier campaign to lift signoff-clean from 84%→95%+ —
  capture Yosys exit codes (`run_synth.sh`), stage-by-stage ORFS execution with
  checkpoints + timeouts, congestion recovery, a config recommender, and
  clock-port validation across 40 constraint files.
