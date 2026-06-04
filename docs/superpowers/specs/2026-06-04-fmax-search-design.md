# Fmax Search (loose-first timing characterization) — Design Spec

**Date:** 2026-06-04
**Skill:** `r2g-rtl2gds`
**Status:** Design approved (brainstorming); implementation plan to follow.
**Authors:** user5 + agent team (methodologist `a832f58efd49ea396`, integration architect
`ad08be78d97a77a24`, red-team `af35c406c6b655e02`), synthesized and source-verified by the
lead.

---

## 1. Goal & one-paragraph summary

Add an **automated Fmax characterization tool** to the skill: given a project, find the
**minimum clock period the design can close at** ("loose-first" — start loose, tighten),
using cheap **placement-stage** timing as the search signal rather than full
place-and-route. A **learned per-family slack-deterioration model** corrects the
placement→signoff optimism *proactively*, so the reported number is a **predicted-signoff
Fmax** (still a `UNVERIFIED` proxy until confirmed); a full signoff-quality confirmation flow
is **opt-in** (`--verify`) and feeds its result back to tighten the model. The tool is an
*optional, agent-invoked* step that runs *before* the normal backend to recommend a period —
it does **not** replace the existing step-8 `check_timing` gate.

This is a **characterization/estimation** feature, not a closure-guarantee feature. It
answers "how fast can this design plausibly go?" quickly and honestly.

## 2. Key decisions (locked)

| # | Decision | Value |
|---|----------|-------|
| D1 | Mode | Find best Fmax (loose-first / minimum closing period). |
| D2 | Probe stage | Stop at **placement**; reference signal = post-place setup slack. |
| D3 | Reported number | **Proxy** (post-place), labeled `UNVERIFIED`; optimism gap disclosed. Full signoff verify is opt-in. |
| D4 | Scope | **Ambitious** — attempt all designs, with escape hatches + honest labels instead of refusals. |
| D5 | Search method | **Root-find + confirm grid** (floorplan early-look → 2–3 fixed-point iterations → 2–3-wide parallel confirm). |
| D6 | Knowledge prior | **Included in v1** — fix `clock_period_ns` ingestion + add a per-family `min_closing_period` aggregate to seed the search. |
| D7 | Guardband | **Learnable per-family staged slack-deterioration model** (floorplan→place→finish), applied *proactively*; replaces the static `M`/`D`. Backfilled from ~681 historical nangate45 runs; other platforms learn forward. Reports a **predicted-signoff Fmax**, not a raw-optimistic one. |

## 3. Source-verified facts this design rests on

All verified against the live ORFS checkout at
`/proj/workarea/user5/OpenROAD-flow-scripts` and the skill scripts. These are load-bearing;
if a future ORFS bump changes them, this design must be revisited.

1. **The clock period is baked into synthesis.** `flow/scripts/variables.mk:201` derives
   `ABC_CLOCK_PERIOD_IN_PS` by `sed`-ing `set clk_period` out of the SDC; it is a Yosys
   dependency (`variables.mk:160`). ABC does delay-driven tech mapping (`-D`). → **The
   netlist is period-specific; each probe period requires its own synthesis.**
2. **Place never reads the project `constraint.sdc`.** The SDC chain is
   `constraint.sdc → 1_2_yosys.sdc (Makefile:269) → 1_synth.sdc → 2_floorplan.sdc`; every
   placement step loads `2_floorplan.sdc` (`global_place.tcl:4`, `resize.tcl:4`,
   `detail_place.tcl:4`) through `load.tcl:35`, which only ever reads a results-dir SDC. →
   **"Synthesize once, sweep placement period on the shared netlist" is timing-invalid**
   (it would re-measure the original period on the wrong netlist). The shared-netlist
   optimization is **rejected**.
3. **There is no usable post-synth WNS.** `1_synth.json` carries no `*__timing__setup__ws`
   key (the `synth-report` target that would emit one is not on the `ORFS_STAGES=synth`
   path). The earliest trustworthy worst-slack is **`floorplan__timing__setup__ws` in
   `2_1_floorplan.json`**. → The cheap pre-narrowing signal is **post-floorplan**, ~51 s.
4. **Post-place setup slack is readable** at `logs/.../3_5_place_dp.json` →
   `detailedplace__timing__setup__ws` / `__tns` (fallback `3_4_place_resized.json` →
   `placeopt__timing__setup__ws` / `__tns`). These are the direct analogue of the
   `finish__timing__setup__ws` keys `extract_ppa.py` already reads. `run_orfs.sh` copies
   the logs back to `<variant>/backend/RUN_*/logs/`, so they are readable without touching
   the ORFS tree.
5. **`run_orfs.sh` already supports stopping after placement** via
   `ORFS_STAGES="synth floorplan place"`; the stage loop honors it and stops. **No edit to
   `run_orfs.sh` is required** for the probe.
6. **`FLOW_VARIANT` is derived from the project-dir basename** and isolates each run's ORFS
   results/logs (`run_orfs.sh:14-23,59`). Unique basename ⇒ safe parallel probes; the hard
   rule forbids two configs sharing `DESIGN_NAME`+`FLOW_VARIANT` concurrently.
7. **Post-place timing is optimistic** vs signoff, and **placement (not routing) is where
   slack is lost.** Placement uses `estimate_parasitics -placement` (wirelength-based RC,
   not routed) and CTS never calls `set_propagated_clock` (clock skew/insertion delay is
   invisible until route). Corpus measured gaps over 614 designs (loose-period corpus —
   treat as a *floor* for the aggressive edge, not typical):
   `floorplan_ws − place_ws` (= `d_fp_pl`) median +0.114 ns, **p90 +0.41 ns** — the big one;
   `place_ws − finish_ws` (= `d_pl_fin`) **median −0.006 ns** (route's `repair_timing` /
   `recover_power` often *improve* setup), p90 +0.05 ns — tiny. Additivity holds exactly in
   the mean (`d_fp_fin = d_fp_pl + d_pl_fin`). **Consequence:** the place proxy is already a
   good signoff predictor *once past placement*; the dominant correction is floorplan→place,
   applied at bracketing — see the deterioration model (§5.1).
8. **Knowledge store: schema-ready, data-missing — but backfillable.** `runs.clock_period_ns`
   exists (`knowledge/schema.sql:18`), queryable by `(design_family, platform)`, but is
   **NULL for all 750 runs** because `ingest_run.py:298` reads it from a `config.mk`
   `CLOCK_PERIOD` key instead of the SDC's `set clk_period`. `wns_ns`/`timing_tier` *are*
   populated. Per-stage slacks (floorplan/place) are **not** stored yet.
9. **Per-stage slack is preserved in history and backfillable.** `run_orfs.sh:350-351`
   copies the *entire* ORFS logs dir to `<project>/backend/RUN_*/logs/`, so past runs retain
   `2_1_floorplan.json` and `3_5_place_dp.json`. Spot-check across 722 projects:
   **682 (94%) have both stage JSONs, 681 key-readable** — so the deterioration model can
   learn from history immediately. **Caveat: 681 are nangate45, only 1 sky130hd** → the model
   is **nangate45-only at launch**; other platforms learn forward. The `1e+39` unconstrained
   sentinel must be filtered (as `check_timing.py:40` does).

## 4. Architecture

```
                       ┌──────────────────────────────────────────────┐
  Tier 0  (free)       │ Seed T_ref + bracket [T_lo0, T_hi0]:          │
                       │   knowledge neighbors (k=5)  →  else           │
                       │   T_ref = nominal SDC period, bracket ±50%     │
                       │   (Tier-1 early-look corrects a bad seed)      │
                       └──────────────────────────────────────────────┘
                                          │  T_ref = aggressive end
                                          ▼
  Tier 1  (~51s)       ┌──────────────────────────────────────────────┐
  floorplan early-look │ synth+floorplan @ T_ref → floorplan_ws        │
                       │ Fmax_fp = T_ref − floorplan_ws                │
                       │ |Fmax_fp − T_ref| > 50%?  → restart @ Fmax_fp │
                       │                            (skip placement)   │
                       └──────────────────────────────────────────────┘
                                          │  bracket OK
                                          ▼
  Tier 2  (root-find)  ┌──────────────────────────────────────────────┐
  2–3 iterations       │ finish probe to place → place_ws              │
  (each re-synths)     │ T_ref ← (T_ref − place_ws) + guardband        │
                       │ repeat until |ΔT_ref| < tol                   │
                       └──────────────────────────────────────────────┘
                                          │  converged T*
                                          ▼
  Confirm  (parallel)  ┌──────────────────────────────────────────────┐
  2–3 place probes     │ grid around T*; pick looser PASS edge         │
                       └──────────────────────────────────────────────┘
                                          ▼
  Report               reports/fmax_search.json
                       Fmax_predicted_signoff (model-corrected) + raw proxy
  Optional --verify    one full signoff flow @ winner; hold-gated;
                       back off ≤2 notches on miss; feed (fp,pl,fin)
                       triple back to tighten the per-family model
```

The bracket and the place-stage closure test both apply a **learned per-family
slack-deterioration model** (§5.1) so the search targets the *predicted-signoff*-closing
period, not the raw-optimistic place-closing period.

**One probe (the primitive):** clone `design_cases/<base>` → `<base>_fmax_p<NNN>`
(NNN = period×10): **symlink `rtl/`** (read-only, large), **copy** `constraints/config.mk`
and `constraints/constraint.sdc`, rewrite the single line `set clk_period P`. Run
`ORFS_STAGES="synth floorplan place" run_orfs.sh <variant> <platform> <variant>`. Read the
proxy slack from the copied-back `3_5_place_dp.json`. Each probe re-synthesizes (required,
fact #1).

## 5. Search numerics & defaults

| Parameter | Default | Rationale |
|-----------|---------|-----------|
| Reference stage / metric | `3_5_place_dp` → `detailedplace__timing__setup__ws`/`__tns` (fallback `3_4`→`placeopt__*`) | Fact #4. |
| Pre-narrow signal | `2_1_floorplan` → `floorplan__timing__setup__ws` | Fact #3. |
| Deterioration model | learnable per-family `d_fp_pl`, `d_pl_fin` (p90, `max(ns, %·period)`); see §5.1 | Replaces static `M`/`D`; applied proactively. |
| Closure guardband (place) | learned **`d_pl_fin`** (cold-start `max(0.10 ns, 1%·period)`) | Predicted place→signoff loss; tiny because route ≈ neutral (fact #7). |
| Bracket discount (floorplan) | learned **`d_fp_fin = d_fp_pl + d_pl_fin`** (cold-start `max(0.55 ns, 5.5%·period)`) | Predicted floorplan→signoff loss; placement is the dominant gap. |
| Closure rule | `place_setup_ws ≥ d_pl_fin` **and** `setup_tns ≥ 0` **and** place completed | Closes ⇒ `finish_ws ≈ place_ws − d_pl_fin ≥ 0` by construction. |
| Hold at probe | **ignored** | Fixed post-route; not Fmax-informative. |
| Diverged/timed-out probe | **`inconclusive`**, not `fail` | A placer hang ≠ "too tight"; never drives the bracket. |
| `ws > 1e30` | **config error** (`inconclusive`), not huge slack | Matches `check_timing.py:40`. |
| Fixed-point iterations | 2–3, stop when `|ΔT_ref| < max(0.1 ns, 2% of period)` | Slack ~linear & self-correcting. |
| Floorplan early-look prune | restart if `|Fmax_fp − T_ref| > 50%` | Avoid paying for placement on a bad seed. |
| Confirm grid | 2–3 parallel probes around `T*`; report looser PASS edge | Robustness + uses the host. |
| Tier-0 neighbors (primary) | k = 5 by `(family, platform)`; center = median achieved period, half-width = `max(p90−p10, 10%·center)` | Data-driven seed (enabled by D6). |
| Tier-0 fallback (no neighbors) | `T_ref` = the design's **nominal SDC period**, bracket ±50%; the Tier-1 floorplan early-look corrects a bad seed within ≤1 restart | Robust and self-correcting without a separate pre-synth estimator. |
| Tier-0 optional refinement | a platform `ref_gate_delay × logic_depth` first guess for a tighter seed | **Not required for v1**; the early-look makes a precise seed unnecessary. |
| Frozen knobs | `CORE_UTILIZATION`, `PLACE_DENSITY*`, `ABC_SPEED`, repair flags snapshotted from baseline; `clk_period` is the **only** variable | Well-defined Fmax (red-team R6). |
| `PLACE_DENSITY_LB_ADDON` | family default, **asserted ≥ 0.10, never co-tuned** | Hard rule. |
| `PLACE_FAST` | OFF by default; **whole-search** escape-hatch ON for hang-prone designs (never per-probe; label conservative lower-bound) | Preserve timing-driven placement we measure; reconcile big-design hangs. |
| Concurrency | adapt to size: small/med ≤ ~4 parallel; ≥100K-cell or macro → serialize/≤2 with raised `PROBE_TIMEOUT` | Avoid host thrash (red-team R5). |

## 5.1 Learnable staged slack-deterioration model

The static guardband is replaced by a **per-`(family, platform)` model** of how setup slack
erodes down the flow, learned from the corpus and applied *proactively* so the search targets
the predicted-signoff-closing period. The instrumentable chain is **floorplan → place →
finish** (synth has no WNS, fact #3); "synth" is represented by floorplan, the first STA.

**Two learnable primitives** (positive = optimism eroded downstream; in ns *and* %-of-period):

```
d_fp_pl  = floorplan_ws − place_ws     # placement RC + detail-place — the DOMINANT gap
d_pl_fin = place_ws    − finish_ws     # routing RC + CTS skew — tiny, often negative
d_fp_fin = d_fp_pl + d_pl_fin          # derived by additivity, never fit independently
```

**Estimator = p90** (conservative: a *safe* predicted-Fmax must over-estimate erosion;
under-estimating declares closure at a period that fails signoff). Applied as
`max(ns_p90, pct_p90 · T)` so it is correct across period scales.

| | cold-start default (ns / %) | learned from |
|---|---|---|
| `d_fp_pl` | 0.45 / 4.5% | corpus p90 +0.41/4.1%, tight-subset +0.43/4.3% |
| `d_pl_fin` | 0.10 / 1.0% | corpus p95 (tiny + CTS-skew un-instrumented → extra insurance) |
| `d_fp_fin` | 0.55 / 5.5% | = sum |

**Sample-gate tiers** (avoid noisy small-n estimates): family `< 5` and platform `< 20` →
**static default**; platform `≥ 20`, family `< 8` → **per-platform p90**; family `≥ 8` →
**per-family p90**. Rolling p90 over the last 50 samples; `N_min_family = 8` matches the
existing learn-loop threshold; same `(platform, family)` keying and `is_success` gate.

**Proactive application (signs verified):**
1. **Tier-1 bracket center** from the floorplan probe at synth-target `T_ref`:
   `T_signoff_pred = (T_ref − floorplan_ws) + d_fp_fin(T_ref)` — looser, to pre-absorb the
   predicted erosion. Bracket `[T_signoff_pred − guard_lo, T_signoff_pred + guard_hi]`.
2. **Tier-2 closure test** at placement: a period closes iff `place_ws ≥ d_pl_fin(T)` and
   `place_tns ≥ 0`. The root-find then converges to `place_ws ≈ d_pl_fin` — the
   *predicted-signoff*-closing period.
3. **Reported numbers:** `Fmax_place_proxy` (raw, `place_ws ≈ 0`, the un-corrected floor) and
   **`Fmax_predicted_signoff`** (`place_ws ≈ d_pl_fin`, the recommended output).

**Online self-correction.** Every `--verify` full flow yields a real
`(floorplan_ws, place_ws, finish_ws)` triple → appended to the family's window; p90
recomputed. Hard clamps: `d_* ≥ 0` (never predict *negative* erosion — one lucky verify must
not let the model tighten Fmax unsafely) and `d_fp_pl ≤ 0.5 · T` (sanity ceiling). A single
verify **records** a sample but does not change the active estimator until the family crosses
`N_min_family = 8` (anti-overcorrection).

**Where it matters (corpus verdict, fact #7):** `d_fp_pl` (placement) is ~80% of the total
erosion and is the load-bearing correction — it positions the Tier-1 bracket. `d_pl_fin`
(routing) is ≈ 0 and acts as a small near-constant insurance term at closure; if a deep-clock
design's route-stage skew ever bites, the online loop widens it automatically with no code
change.

## 6. Honesty labels (every reported Fmax carries one)

- `Fmax_predicted_signoff @ synth-target=<T_ref>` — the **recommended** output;
  model-corrected (`place_ws ≈ d_pl_fin`), still a proxy until `--verify`.
- `Fmax_place_proxy @ synth-target=<T_ref>, place-only` — the raw, un-corrected place-closing
  number (`place_ws ≈ 0`); place-RC + ideal clock, shown as the optimistic floor.
- `+CTS-skew-unmodeled` — always appended to proxy results (CTS not propagated, fact #7).
- `Fmax_verified` — **only** after `--verify`'s full flow closes setup **and** hold **and**
  routes.
- `PLACE_FAST-lower-bound` — if the search ran in PLACE_FAST mode (conservative).
- `deterioration: learned(family=<f>, n=<k>, q=p90) | platform(n=<k>) | default-static` —
  provenance of the model term used (for the lineage view).
- `knn-bracket` / `nominal-seed` / `no_synth_prior` — Tier-0/Tier-1 bracket provenance.

## 7. Failure handling (red-team-driven)

- **Placer divergence / timeout** → `inconclusive` (not `fail`); detected via the existing
  hang signatures in `run_orfs.sh:293-327` and the per-probe timeout. A whole-search that is
  all-inconclusive is reported as such — **no fabricated Fmax**.
- **Loose end fails** (even the loosest probe misses the `d_pl_fin` guardband): widen
  loose-ward (×1.25, ×1.5) before suspecting the netlist; if still failing, **one** rescue
  re-synth at the looser target, rebuild the bracket, restart Tier 2. Hard cap: one rescue
  re-synth.
- **Tight end passes** (even the tightest probe closes): extend tight-ward on fresh probes
  (each re-synths at the tighter target).
- **PLACE_FAST mode** is whole-search, never mixed with non-PLACE_FAST probes (proxy must be
  consistent across probes).
- **Intractable designs** (BOOM/arm_core class, `reports/intractable.json`): attempted under
  ambitious scope with raised timeouts + serialization, but a probe that never reaches
  placement is reported `inconclusive` with the honest reason — the tool never claims an
  Fmax it could not measure.

## 8. Components

| File | New/Changed | Purpose |
|------|-------------|---------|
| `r2g-rtl2gds/scripts/reports/fmax_search.py` | **NEW** | Orchestrator: Tier-0 seed → Tier-1 early-look → Tier-2 root-find → confirm grid → label → `reports/fmax_search.json` → cleanup. Optional `--verify`. Multi-probe control loop with its own state; no existing script owns it. |
| `r2g-rtl2gds/scripts/extract/extract_ppa.py` | **CHANGED** | Add `--stage {floorplan,place}`: read `2_1_floorplan.json`→`floorplan__*` and `3_5_place_dp.json`→`detailedplace__*` (fallback `3_4`→`placeopt__*`) into `summary.timing.*`; treat `>1e30` as config-error. Also emit a `summary.timing_staged = {floorplan_setup_ws, place_setup_ws, finish_setup_ws}` sub-key so ingest stays a pure JSON-artifact reader. Single source of truth for the key mapping. |
| `r2g-rtl2gds/knowledge/schema.sql` | **CHANGED** | Add `floorplan_setup_ws`, `place_setup_ws`, `finish_setup_ws` REAL columns to `runs` (after line 26). No index/query breakage (`SELECT *`, keyed by name). |
| `r2g-rtl2gds/knowledge/knowledge_db.py` | **CHANGED** | Add the same three columns to `_RUNS_ADDED_COLUMNS` (after line 45) so `_migrate_add_columns` `ALTER TABLE`s the **live** DB (schema.sql's `CREATE TABLE IF NOT EXISTS` never reaches an existing DB). |
| `r2g-rtl2gds/knowledge/ingest_run.py` | **CHANGED** | (a) Read `set clk_period` from `constraints/constraint.sdc` (reuse `check_timing.py` `read_clock_period`) into `clock_period_ns`, fixing the NULL-for-all-runs gap (fact #8). (b) Populate the three staged-slack columns from `summary.timing_staged` (near line 310). (c) Add a `--backfill` path that re-scans each `backend/RUN_*/logs/{2_1_floorplan,3_5_place_dp}.json`, filters the `1e+39` sentinel, and `UPDATE runs` keyed by `_compute_run_id` — recovers ~681 historical nangate45 triples (fact #9). |
| `r2g-rtl2gds/knowledge/learn_heuristics.py` | **CHANGED** | In `_family_platform_entry` (~line 65): add a per-`(family, platform)` `min_closing_period = min(clock_period_ns − wns_ns)` seed **and** the `slack_deterioration` quantiles (`d_fp_pl`, `d_pl_fin`, `d_fp_fin` p90 in ns and %, with sample count `n`), over `is_success` rows, filtering the sentinel. |
| `r2g-rtl2gds/knowledge/suggest_config.py` / `query_knowledge.py` | **CHANGED (thin)** | Expose `min_closing_period` (+spread) as the Tier-0 seed and `slack_deterioration` as the model; both ride the existing `get_family_heuristics` return dict (already surfaced unchanged). Degrade to cold-start defaults when `n < N_min`. |
| `r2g-rtl2gds/SKILL.md` | **CHANGED** | New optional "Fmax Search" step + env-knob note (`ORFS_STAGES`, usage, "proxy is optimistic; `--verify` for signoff-true"). |
| `r2g-rtl2gds/references/orfs-playbook.md` | **CHANGED** | "Fmax Search (loose-first)" section: probe cmd, proxy keys, root-find algorithm, variant cloning, guardband/derate, escape hatches, honest labels. |
| `r2g-rtl2gds/references/lessons-learned.md` | **CHANGED** | Note the place→signoff optimism gap + the archetypes where the proxy lies (congestion/route-limited, macro/CTS-skew, hold cliffs); cross-ref failure-patterns. |
| `r2g-rtl2gds/tests/test_fmax_search.py` | **NEW** | Unit tests on fixtures (see §10). |
| `r2g-rtl2gds/tests/` ingest/learn/migration tests | **CHANGED/NEW** | Cover the `clock_period_ns` ingest fix, the schema migration (live `ALTER TABLE`), staged-slack ingest + `--backfill`, the `min_closing_period` + `slack_deterioration` aggregates, and the online-update min-n / `d_*≥0` clamps. |

(Dashboard panel for `fmax_search.json` is a **follow-up**, not v1-blocking.)

## 9. Data flow

**Search:** `project → fmax_search.py → [Tier-0 seed + Tier-1 deterioration model from knowledge store] → clone variant → run_orfs.sh (ORFS_STAGES="synth floorplan place") → 2_1_floorplan.json (early-look) / 3_5_place_dp.json (place) → extract_ppa --stage → pass/fail/inconclusive (vs learned d_pl_fin) → root-find/confirm → winner → reports/fmax_search.json (Fmax_predicted_signoff + raw proxy + provenance)`.

**Opt `--verify`:** full `run_orfs` + `check_timing` at winner, hold-gated, back-off ≤2.

**Learning loop (closes the cycle):** every run's `ppa.json` carries `summary.timing_staged` (from `extract_ppa --stage`) → `ingest_run.py` writes `{floorplan,place,finish}_setup_ws` → `learn_heuristics.py` rolls per-family `slack_deterioration` p90 + `min_closing_period` → `suggest_config`/`get_family_heuristics` surface them back to the next `fmax_search.py` invocation. Seeded at launch by the `--backfill` over ~681 historical nangate45 runs.

## 10. Testing strategy

- **Unit (fast, fixture-based, the bulk):**
  - Proxy parse: `detailedplace__*` present; `3_4` fallback; `floorplan__*`;
    `>1e30`→config-error; missing keys→graceful.
  - Deterioration model: `d_fp_pl`/`d_pl_fin`/`d_fp_fin` p90 estimator (ns & %, `max()`
    floor); sample-gate tiers (default→platform→family); proactive-formula signs (bracket
    discount, closure test); online update with the min-n gate and `d_*≥0` / `≤0.5·T` clamps.
  - Closure classification (pass/fail/inconclusive) against a learned `d_pl_fin`.
  - Root-find convergence on a mocked `slack(period)` oracle (monotone & noisy-near-edge);
    floorplan early-look prune trigger; loose-end-fails / tight-end-passes fallbacks.
  - Variant name/period encoding; SDC `set clk_period` rewrite; **assertions**: unique
    `FLOW_VARIANT`, `PLACE_DENSITY_LB_ADDON ≥ 0.10`, frozen-knob snapshot.
  - Tier-0 seed selection (knn vs nominal-seed vs default) on a mock knowledge DB.
  - Knowledge: `clock_period_ns` populated from a sample `constraint.sdc`; live-DB column
    migration; staged-slack ingest + `--backfill` from fixture logs (incl. `1e+39` filter);
    `min_closing_period` and `slack_deterioration` aggregate correctness.
- **Integration (heavy, opt-in marker like the existing golden-regression gate):** one small
  nangate45 design end-to-end (root-find → proxy report; `--verify` → confirmed), asserting
  honest labels and that no probe violates the hard rules.

## 11. Out of scope (v1) / follow-ups

- Dashboard "Fmax sweep" panel (wire `fmax_search.json` into the generator).
- Multi-clock / CDC Fmax (escalate to user, per CLAUDE.md).
- Co-tuning PD knobs (util/ABC/density) as part of the search — explicitly excluded;
  `clk_period` is the only variable.
- Probing at CTS stage — rejected (CTS adds cost but no setup fidelity; fact #7).
- Non-nangate45 deterioration model — ships with cold-start defaults and learns **forward**
  (only nangate45 is backfillable from history, fact #9); the per-platform tier kicks in as
  other platforms accrue `--verify` triples.
- Optional `ref_gate_delay × logic_depth` Tier-0 seed — the floorplan early-look makes a
  precise pre-synth seed unnecessary (§5).

## 12. Risks & mitigations (carried from the red-team)

| Risk | Mitigation |
|------|------------|
| Proxy optimistic on congestion/route-limited & macro/skew designs | Learned per-family `d_pl_fin` guardband (widens automatically as verify data accrues); mandatory honest label + `predicted-signoff` correction; `--verify` for the real number. |
| Placement intractable on big designs (the "cheap probe" is false there) | Per-probe timeout → `inconclusive`; serialize; PLACE_FAST escape-hatch; never claim an unmeasured Fmax. |
| `FLOW_VARIANT` collision | Unique `<base>_fmax_p<NNN>` per period, asserted pre-launch. |
| Placer divergence from density tuning | Density frozen, asserted ≥ 0.10, never co-tuned. |
| Host thrash from parallel heavy placements | Size-adaptive concurrency caps. |
| Dishonest "Fmax" claim | Label taxonomy (§6); proxy ≠ verified; gap disclosed; lineage provenance. |

---

*Implementation plan: `docs/superpowers/plans/` (writing-plans, next). Per project
convention, the skill scripts/references are the source of truth; this spec records the
design rationale and the source-verified ORFS facts it depends on.*
