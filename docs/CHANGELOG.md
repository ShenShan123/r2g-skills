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

## 2026-07-06 — nangate45 RTL→Graph verification round 2; 4 more extraction fixes + wide-coverage corner-case infra

Re-verified the graph-dataset pipeline on **nangate45** and built the corner-case verification
infrastructure the earlier rounds lacked. Branch `feat/rtl2graph-integration`, commit `031a12f`
(+ docs `17dca99`); full record in
`docs/superpowers/plans/rtl2graph-integration-audit-2026-07-05.md` (2026-07-06 round-2 addendum).

- **Baseline honest:** `verify_graph_dataset --batch` green 85/85 (84–87) × 10 nangate45 designs;
  wirelength INDEPENDENTLY cross-checked vs OpenROAD `net.getWire().getLength()` over **32,005
  aes_core nets → 0 real mismatches** (only a benign 0.07 µm/IO-net end-extension delta);
  NetType/mask/log1p-label all correct, power/ground excluded. The pipeline is genuinely correct
  on nangate45.
- **Four defects fixed** (all in paths real nangate45 files never reach → invisible to the raw-file
  verifier; behavior-neutral on nangate45; failure-patterns.md #15–#18): (15) liberty
  `ff_bank`/`latch_bank` multibit-sequential undetected (`is_sequential` false; asap7 ships 27 such
  libs) — currently *inert* (field unconsumed), fixed defensively; (16) `compute_feature_stats` had
  NO honesty gate (X-side of the #6 irdrop lesson) — a raw/truncated feature CSV summarized "ok",
  now flagged "invalid"; (17) `netlist_graph` tie-off constant in a concat (`{1'b0, sig}`) leaked a
  phantom net `b0`; (18) latent parity: `run_labels.sh` exports `R2G_PLATFORM`, `edges_iopin_net`
  rstrips a continuation DIRECTION.
- **New corner-case infra:** `tests/fixtures/corner_synth.py` (a hand-computable synthetic
  nangate45-style design) driven through the REAL workers → labels → PyG builder by
  `tests/test_corner_case_pipeline.py` (asserts vs hand-derived truth across **all five graph views
  b–f**) + `tests/test_corner_case_units.py` (focused corners incl. the congestion demand-key
  transpose guard under an asymmetric grid). Complementarity: the raw-file cross-check proves the
  extractors match the tools on inputs you HAVE; the fixture proves they handle inputs you might GET.
- Full extract/graph/techlib test surface: 298 passed. Datasets built after the 2026-07-06 round-1
  regeneration remain valid (these fixes don't change nangate45 output).

---

## 2026-07-05 — RTL2Graph integrated as the PyG graph-dataset stage; 5 extraction defects found + fixed

Audited the operator-provided `RTL2Graph/` pipeline against OpenDB/OpenROAD ground truth
(cordic nangate45 + aes_core sky130hd) before integrating it, then shipped it as skill
stage 13d (`scripts/flow/run_graphs.sh` → `<project>/dataset/{b..f}_graph.pt` +
`netlist_graph.pt` + `graph_manifest.json`). Branch `feat/rtl2graph-integration`,
commits `4d8e032`/`6b09000`/`69c10e2`; full record:
`docs/superpowers/plans/rtl2graph-integration-audit-2026-07-05.md`.

- **RTL2Graph's `feature_test_v3`/`label_test` = stale ancestors** of the skill's extract
  stages (still carried the sky130 quote-bug, nangate-only `num_layer`, dead fakeram
  keys) — not ported; the skill's stages are the substrate.
- **Five silent-wrong-value defects found; four lived in the skill's own extractors**
  (fixed + regression-tested; detail in failure-patterns.md "Dataset-Extraction
  Silent-Value Defects"): (1) timing labels lost on EVERY register (escaped-vs-unescaped
  name join; aes_core sky130hd 5/2476 → 2476/2476 labeled); (2) sky130 DEF `RECT` patch
  groups parsed as route points (wirelength ~350× inflated on 1283/30k nets, congestion
  "utilization" 11×); (3) DEF PIN direction inverted in `num_drivers`/`num_sinks` (+
  `connects_macro_flag` implemented, was hardwired 0); (4) driver `max_capacitance`
  summed into `sum_pin_cap_fF` (62.5 fF vs true 3.19 fF); (5) RTL2Graph c–f variants
  misaligned `edge_attr`/`edge_type`/`edge_y` with `edge_index` (171/3001 sampled
  pin-edges aligned; the consolidated port scores 3001/3001).
- Port equivalence-proven vs the originals (node tensors + edge sets exact, all five
  variants, identical inputs); five ~700-line near-duplicate scripts consolidated into
  `scripts/extract/graph/`. torch/PyG/pandas are graph-stage-only deps — `run_graphs.sh`
  probes `R2G_GRAPH_PYTHON` and SKIPs cleanly without them.
- **Operator action:** label/feature CSVs generated before 2026-07-05 carry the old,
  wrong values — regenerate before training. Suite: 964 passed / 16 skipped.

## 2026-06-16 — Gate B FIRED on the live store: A/B loop's first end-to-end verdict + density_relief

Ran the deferred compute-bound **Tier −1 Gate B** for real on the live `knowledge.sqlite`
(branch `feat/paper-absorption`). The corpus showed the exact Gate A signature — 69 `fail` +
41 `partial` runs but `recipe_status` empty and `ab_trials=0` — i.e. the A/B loop had never
fired in production.

- **Second blocker found + fixed.** `ab_runner.plan_trial` selected A/B subjects only from
  `run_violations` (the POST-fix snapshot), so a *successfully-fixed* symptom (antenna) had no
  rows there and its winning recipe could never be A/B'd (`plan_trial→None`, verified — same
  fixture-vs-production trap as Gate A itself). Fixed with a Tier-2 fallback to the recipe's
  `heuristics.symptoms[sid].evidence_designs` (pre-fix exhibitors), resolved to on-disk dirs.
- **New sky130 DRC strategy `density_relief`.** The 9 pending sky130hd DRC-fail designs were
  genuine metal/via **spacing** residuals (`m3.2`/`via.4*`/`via_OFFGRID`) that v1 left as
  "non-antenna DRC not handled." `diagnose_signoff_fix._routing_drc_strategies` now lowers
  `CORE_UTILIZATION` (by 8, floor 8, rerun_from floorplan) — a real layout change; the deck is
  never relaxed. Cleared **all 9** (34/20/10/6/4/84 → 0) → 9 newly fully-signed-off sky130
  designs.
- **Loop fired end-to-end.** Learner derived the recipe → Gate A enqueued (`recipe_status` 0→2,
  `learner_diff`) → `engineer_loop ab-drain` ran arm A (`--exclude`, stays dirty) vs arm B
  (`--rank-first`, clears) → **`ab_trials` 0→2, both `win`** → `logic/small density_relief`
  recipe `candidate → promoted` (`ab_trial:2`). Honesty re-validated: `fail`-rows ==
  `orfs-fail`-events 69/69; no `is_success` flip.
- Tests 592 → **597** (1 plan_trial fallback + 4 density_relief). Source: this session;
  details in `references/engineer-loop.md` "Tier −1 Gate B — FIRED", `knowledge/README.md`
  invariant 20, `references/signoff-fixing.md` catalog.

---

## 2026-06-04 — Fmax search (loose-first): place-stage proxy + learnable deterioration model
*(spec `ca422ee`, plan `9048cf4` + impl log `f3c3b99`; code commits `cc5376d`→`42c233a` on `feat/fmax-search`)*

Automated **loose-first Fmax characterization** for the skill — finds the minimum clock period a
design closes at, using cheap **placement-stage** timing as the search signal instead of running a
full flow per candidate period.

- **The proxy + search.** Each probe runs only `ORFS_STAGES="synth floorplan place"` and reads
  post-place setup slack (`detailedplace__timing__setup__ws`, `3_5_place_dp.json`). The search is a
  floorplan early-look prune → fixed-point root-find → confirm grid (~3–5 probes vs ~20 blind), over
  cloned `<base>_fmax_p<NNNN>` variants (unique `FLOW_VARIANT`; only the SDC `clk_period` line is
  rewritten, density frozen ≥ 0.10). New pure module `scripts/reports/fmax_model.py` (no-I/O,
  fully unit-tested with injected probes) + orchestrator `scripts/reports/fmax_search.py`.
- **Honest reporting.** Output (`reports/fmax_search.json`) is a **predicted-signoff proxy
  (UNVERIFIED)** — post-place timing is optimistic vs signoff — corrected by a learned per-family
  slack-deterioration model and tagged `+CTS-skew-unmodeled`. `--verify` runs one full signoff flow
  at the winner and records the verified `(floorplan, place, finish)` triple back so the model
  self-corrects online. Does **not** replace the step-8 `check_timing` gate.
- **Knowledge-store changes.** Three per-stage setup-slack columns (`floorplan/place/finish_setup_ws`);
  `ingest_run.py` now reads the clock period from the **SDC** (`set clk_period`) not `config.mk`
  (it was NULL for all 750 runs) and persists staged slacks, with a `--backfill` path for historical
  runs from preserved `backend/RUN_*/logs/`; `learn_heuristics.py` emits per-family `closing_period`
  + `slack_deterioration` (p90); `query_knowledge.py` accessors feed the seed + model selection.
- **Two fixes beyond the plan.** (1) `knowledge_db.ensure_schema` latent ordering bug — a
  `CREATE INDEX` on a not-yet-migrated column aborted bootstrap on a legacy DB; now runs statements
  one-by-one and defers/retries such indexes after the ALTER migration (production path unchanged).
  (2) `ingest_run.py --backfill` works standalone (`project` made optional).
- **Live validation.** Backfilled **750 runs** (`clock_period_ns` 750/750, `place_setup_ws` 634/750)
  and re-learned `slack_deterioration` for **47/48** family·platform entries; real corpus confirms
  the premise — **placement is the dominant gap** (`d_fp_pl` p90 ≈ 0.38 ns) while **routing is ≈
  neutral** (`d_pl_fin` p90 ≈ −0.01 ns). `runs.sqlite`/`heuristics.json` are gitignored (local-only).

Tests: **357 passed, 8 skipped** (54 new fmax + knowledge tests). Implemented via a dependency-gated
multi-agent workflow (14 subagents, strict TDD). Skill changes also recorded in `SKILL.md` (step 5a),
`references/orfs-playbook.md` ("Fmax Search (loose-first)"), `references/lessons-learned.md`, and
`knowledge/README.md`.

---

## 2026-06-04 — OpenSpace absorption: live learning loop + observability + payoff eval
*(plan: `docs/plans/openspace-absorption-2026-06-03.md`; skill commits `356d517`→`5dd99ee`; PR [#3](https://github.com/ShenShan123/agent-r2g/pull/3))*

Three wins from the OpenSpace study, implemented against `knowledge/`. The self-improvement loop was
**dead** — `heuristics.json` was `"families": {}` because `_is_success` required `orfs_status=='pass'`,
which **0 of 750** runs had (747 `partial`); meanwhile 607 LVS-clean / 681 DRC-clean(+beol) /
699 RCX-complete sat unused.

- **Win 1 — learning loop repaired (the unlock).** Added a shared `knowledge_db.is_success` (strict
  6-stage pass OR signoff-positive: ≥1 positive clean signal, no failed signoff; `symmetric_matcher`
  gated to a `fail` verdict; absence-of-data is *not* success), imported by `learn_heuristics.py` +
  `monitor_health.py`. Re-running `learn_heuristics.py` against the existing DB — **no re-ingest** —
  took `heuristics.json` from **0 → 48 learned family/platform pairs** (631 runs learnable). The fix
  is in the learner, not `ingest`, so `orfs_status` stays a faithful record of the stage log. Added
  the `PLACE_DENSITY_LB_ADDON ≥ 0.10` floor the hard rules named but `suggest_config` never enforced;
  the `bus_heavy` CU→15 clamp still overrides learned medians. `families.json` curated conservatively
  with anchored `^prefix_` patterns; the fuzzy/Jaccard fallback rejected.
- **Win 2 — read-only observability.** `scripts/reports/build_lineage_view.py` is a deterministic
  `mode=ro` projection over `runs.sqlite` + `config_lineage` + `heuristics.json` → two dashboard
  panels (the diagnostic that would have screamed "747/750 partial, heuristics empty"). Reuses
  `is_success` so the health strip and the learner can't disagree; never wired into `suggest_config`;
  lineage is a loose single-parent diff chain, not a true DAG.
- **Win 3 — payoff A/B harness.** `knowledge/eval_heuristics.py` (`emit` paired naive/learned arms via
  a new `suggest_config --no-learned`; `summarize` → deterministic `eval_summary.json`) + frozen
  `eval_set.json` (non-bus families only — `bus_heavy` clamp would mask the difference) + nullable
  `eval_arm` column. Cost is **wall-clock** (`stage_log.jsonl` records only `elapsed_s`;
  CPU-hours/peak-RAM are not instrumented) — recorded as `cost_metric`, never fabricated. A `win`
  requires a *usable* signed-off learned arm; cheaper-but-both-fail is `inconclusive`. The multi-hour
  A/B *run* is operator-driven.

Tests: **331 passed, 8 skipped** (~30 new). Each win went implementer → spec review → code-quality
review → empirical controller verification. Skill changes also recorded in
`references/lessons-learned.md` ("Dead Learning Loop — Repaired 2026-06-04") and `knowledge/README.md`.

---

## 2026-06-03 — LVS failure-cause analysis + residual campaign (corrected, re-ingested)
*(doc: `docs/lvs-failure-analysis-2026-06-03.md`, merged; skill commit `7129d9b`)*

> **Post-residual-campaign correction.** The previous version of this entry was built from a stale
> knowledge store: 9 designs it labelled `skipped` were already `clean` on disk, several
> `crash`/`incomplete` rows had moved, and `clean_algorithmic` was hiding a real defect. A
> five-domain subagent campaign drove every persistent residual to ground truth, re-ingested the
> corpus, and updated the headline numbers below. Skill changes recorded in
> `references/failure-patterns.md` and `references/signoff-fixing.md`.

Corpus-wide analysis grounded in `knowledge/runs.sqlite` + each design's `reports/lvs.json`.
**607/674 designs with LVS data are clean (90%).** Of the 18 `fail` verdicts, **only TWO are
genuine layout defects** (both `wb2axip`) — the other 16, plus all `crash`/`incomplete`, are
KLayout-0.30.7 tooling limits, not layout defects.

**Status distribution (per project, latest run):**

| Status | Count | Meaning |
|--------|------:|---------|
| `clean` | 607 | Netlists match |
| `incomplete` | 44 | No verdict under the cap — mostly a comparer bug, not slowness (see below) |
| `fail` | 18 | Comparer reached a "don't match" verdict (sub-classified below) |
| `unknown` | 3 | spi_master (CDL parse error) + 2 ChipTop BOOMs (intractable) |
| `crash` | 2 | KLayout SIGSEGV that did not survive retries (`usbf_device`, `wb2axip_axixclk`) |

**What changed from the stale snapshot:** `skipped` 17→0 (9 were already clean; the rest
re-ran); `crash` 7→2 (retry — see §1 below); `clean_algorithmic` 7→0 (dead legacy label —
re-extracting folds them into `fail`+sub-class, and one was a real defect); `fail` 9→18 (now
includes ex-crash and ex-clean_algorithmic symmetric residuals, correctly labelled).

**The 18 `fail` verdicts, sub-classified** by `extract_lvs.py::classify_lvs_mismatch`
(balance-based, not zero-delta; refined 2026-06-03):

| mismatch_class | Count | Layout correct? |
|----------------|------:|-----------------|
| `symmetric_matcher` | 15 | Yes — KLayout-0.30.7 can't disambiguate symmetric structures |
| `real_connectivity` | 2 | No — `wb2axip_axi2axilite` (1 net open), `wb2axip_axilsingle` (16 bus opens; was mislabeled `clean_algorithmic`) |
| (no lvsdb) | 1 | `iccad2015_unit08_in1` — pre-patch deck, no db written |

**Root causes, ranked:**

1. **KLayout-0.30.7 SIGSEGV** (`sort_circuit`/`gen_log_entry`, during compare after extraction
   succeeds) — non-deterministic; a surviving run gives the true verdict. `run_lvs.sh` now
   retries automatically (`LVS_CRASH_RETRIES`, default 4; auto-1 for >150K cells). 6 of 7 crash
   designs resolved (3 → `clean`, 3 → `fail`/symmetric). `threads(1)`, `verbose(false)`,
   tcmalloc, and `flat` mode don't fix it; KLayout ≥0.30.10 would fix at source.
2. **KLayout-0.30.7 symmetric-matcher limit** — dominant `fail` cause; 15 of 18. Layout
   correct; matcher can't fingerprint topologically identical instances (parallel NAND/XOR/parity
   trees, crypto mixing rounds, register files, replicated bit-slices). Raising comparer budget
   (`max_depth`/`max_branch_complexity`) does NOT help (re-confirmed). `same_nets!` seeding
   can clear a localized one — validated on `verilog_ethernet_axis_baser_rx_64` → clean
   (operator-only; doesn't generalize).
3. **Genuine connectivity defects (2)** — both `wb2axip`; described in the mismatch table.
4. **`incomplete` (44) — mostly a comparer bug, not honest slowness.** Three distinct causes:
   comparer SIGSEGV (e.g. `usbf_device` crashes at ~750 s at 23K cells — *smaller* than
   `aes_core` which finishes → structure-driven, not size), comparer internal assertion
   `dbNetlistCompareCore.cc:1003`, and honest extraction timeout (super-linear: ~2700 s @ 51K,
   ~10200 s @ 62K — the old 3600 s cap SIGTERM'd ≥50K designs mid-extraction). `run_lvs.sh`
   timeout tiers raised: >50K→14400 s, >100K→21600 s, >250K→28800 s, base 5400 s. Memory never
   binds (peak ≤1.65 GB @ 242K). ChipTop 5–9M-instance BOOMs die mid-geometry → intractable.
5. **CDL parse error (`unknown`, 1)** — `spi_master_single_cs`: KLayout mis-tokenizes an
   escaped-bracket negative-index instance name (`Xr_CS_Inactive_Count\[-1\]$_DFFE_PN0P_`).
   Not a layout defect.

**Why most LVS failures are not back-end-flow-fixable:**
SIGSEGV is now auto-retried; symmetric-matcher and incomplete/comparer-crash require a newer
KLayout (≥0.30.10); real-connectivity defects are genuine bugs. `diagnose_signoff_fix.py`
reports these as honest, specifically-labelled residuals rather than spawning doomed re-runs.

This file (`CHANGELOG.md`) is relocated from the repo root into `docs/` alongside the other
curated history; references to it from within `docs/` resolve unchanged.

---

## 2026-06-02 — nangate45 antenna DRC made genuinely fixable (tech-model + diode-forced repair)
*(skill: `scripts/flow/antenna_lef_patch.py`, `tools/install_nangate45_antenna.sh`, `tools/batch_antenna_fix.sh`, `diagnose_signoff_fix.py`)*

**Overturns the 2026-06-01 "Finding B: nangate45 antennas have no viable real fix" conclusion.**
The inertness of OpenROAD `repair_antennas` on nangate45 had **three** root causes in the stock
LEFs (prior attempts fixed at most one → concluded "unfixable"):
1. tech LEF has zero antenna ratios (no threshold → `check_antennas` finds 0);
2. the SC LEF ORFS uses (`*.macro.mod.lef`) has `ANTENNAGATEAREA` **stripped** from std-cell pins
   (full model is in the sibling `*.macro.lef`) — without gate areas there is no ratio even at
   ratio 1 (the non-obvious cause);
3. the `ANTENNA_X1` diode has `ANTENNADIFFAREA 0.0`, which OpenROAD rejects (RepairAntennas.cpp).

**Fix:** `tools/install_nangate45_antenna.sh` (reversible/idempotent; patcher `antenna_lef_patch.py`)
adds `ANTENNAAREARATIO 300` per routing layer (**matches** the signoff deck — not a relaxation),
merges per-pin gate areas from `.macro.lef`, and gives the diode a usable `ANTENNADIFFAREA`. With
the model installed OpenROAD's per-net PAR equals KLayout's ratio to the decimal (stream_register
488.80 vs 489.17).

**Key principle — diodes, not jumpers.** OpenROAD's default repair uses jumpers (PAR drops, it
reports clean) but the FreePDK45 deck sums the whole net's per-layer metal and credits only
**diodes**, so it keeps flagging. The new `antenna_diode_repair` strategy (`diagnose_signoff_fix.py`,
nangate45) forces diode insertion: `SKIP_ANTENNA_REPAIR=1` + `MAX_REPAIR_ANTENNAS_ITER_DRT=10`,
rerun from route. `DIODE_FORCED_REPAIR_PLATFORMS` replaces the old `ANTENNA_REPAIR_INERT_PLATFORMS`.

**Validated:** stream_register 489:1 → CLEAN (1 diode), riscv_alu4b 7→0 (2 diodes); LVS stays
clean (the `.lylvs` rule flattens the physical-only `ANTENNA_X1`). Deck never relaxed. New: 12
patcher tests + updated diagnoser tests; full suite 286 passed. `tools/batch_antenna_fix.sh`
clears the pure-antenna nangate45 fails in bulk.

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
