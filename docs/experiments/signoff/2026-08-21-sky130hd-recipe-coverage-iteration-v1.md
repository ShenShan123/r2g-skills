# Sky130HD Recipe Coverage Iteration V1

## Objective

Expand the bounded Sky130HD repair catalog with new, causally validated physical-design
actions before formal Experiment 2. The first milestone is at least three new promoted
effect fingerprints spanning at least three of these categories: placement/pin/PDN,
routing/congestion, DRC geometry, setup/hold timing, and antenna.

This is development work, not formal evaluation. Formal Experiment 2 results must not be
inspected or used to create, tune, rank, or promote a Recipe.

## Frozen Task Boundary

| Item | Rule |
|---|---|
| Platform | Sky130HD only |
| Target | 100 MHz / 10 ns unless a case is explicitly labelled a mechanism canary |
| Baseline | Frozen Default ORFS configuration for that subject |
| Local environment | `R2G_ENV_FILE=/home/yangao/r2g-skills/r2g-skills/signoff-loop/references/env.local.sh`; generated local pins are not inherited by Git worktrees |
| Allowed effects | Reviewed ORFS configuration edits and bounded safe Tcl parameters |
| Prohibited effects | RTL changes, clock relaxation, signoff-deck changes, disabling checks, or changing the protected task |
| Natural evidence | Reproducible failures from unmodified eligible RTL at the frozen task |
| Artificial evidence | Mechanism canary only; cannot independently promote a Recipe or support a paper claim |
| Large designs | 50k-100k mapped cells enter the slow validation queue; 100k or repeated timeout enters the capacity track |

## Candidate Procedure

For each reproducible failure, one diagnostic round may test at most three candidates:

1. one direct single-effect action;
2. one distinct single-effect action;
3. one bounded combination only when the causal diagnosis requires both effects.

Every candidate must declare its normalized effect fingerprint, expected affected stage,
preconditions, protected fields, rollback, and stop condition. A no-op, out-of-domain edit,
or protected-task change is rejected before execution.

## Evidence Gates

### Fast Screen

- Use one development subject and rerun from the earliest affected stage when digest-bound
  lineage is available.
- Set the candidate timeout to 1.5 times the corresponding baseline-stage runtime, with a
  20-minute minimum and 90-minute maximum.
- Require a real configuration or Tcl delta, target-symptom improvement, strict signoff at
  the protected target, and no regression in route, DRC classes/count, LVS, timing, RCX,
  constraints, or platform capability.
- A fast-screen pass remains `candidate`; it is never promotion evidence by itself.

### Strict Promotion

- Use two independent natural RTL families, not aliases or two tops from one family.
- Run two repeats per A/B arm with distinct, arm-owned run IDs.
- Hold RTL closure, source commit, platform, clock, floorplan objective, signoff checks,
  toolchain, and all non-Recipe configuration constant.
- Require provenance-complete arm ownership and complete global signoff evidence.
- Promote only when both applicable subjects provide decisive globally non-regressive
  evidence. Record failures and inconclusive trials as negative evidence; never rewrite
  them as wins.

## Initial Coverage Matrix

| Category | Existing catalog evidence | Natural 100 MHz development evidence | Independent validation evidence | Initial status |
|---|---|---|---|---|
| Placement/pin/PDN | `pin_side_rebalance`, `pdn_die_floor` | Historical cases require natural-vs-stress audit | Not yet bound | Gap audit |
| Routing/congestion | `route_relief` | `pcs_rx`; two capacity-limited systolic designs | No second demonstrated success | Existing action; seek distinct effect |
| DRC geometry | `density_relief`, `pin_side_rebalance` | GCD, JESD, SDRAM and Expander-derived m3.2 cases | Multiple families available | Existing actions; seek distinct rule/effect |
| Setup/hold timing | `setup_slack_margin` | One severe 100 MHz setup failure | Missing second natural family | Expander target needed |
| Antenna | Sky130 antenna iteration/density actions | No qualified natural Sky130HD pair yet | Missing | Expander target needed |
| LVS | Unknown-net action is parked | No safe configuration-repair pair identified | Missing | Do not force; true logic mismatch is ineligible |

## Acquisition Rule

Historical projects are audited first. If a category lacks one development subject or a
second independent validation family, Expander must perform targeted acquisition for that
failure mechanism. Acquisition may increase the candidate pool but cannot weaken the
100 MHz task, natural-evidence rule, source provenance, or strict-signoff gate.

## Publication Rule

Candidate work stays on `dev/sky130hd-recipe-coverage-v1` with an isolated knowledge
database. Failed candidates publish only negative evidence. A P0 safety fix may be
committed independently. A new Recipe and its evidence may reach `main` only after the
strict promotion gate and focused plus full regression tests pass.

## Iteration 01 Evidence Log

| Candidate effect | Natural development subject | Result | Lifecycle decision |
|---|---|---|---|
| `ROUTING_LAYER_ADJUSTMENT=0.10` | `footprint_nt35510` | No change: route remained at 1 violation and full DRC remained at 2 `m2.2` violations | Rejected; negative evidence only |
| `CELL_PAD_IN_SITES_DETAIL_PLACEMENT=1` | `footprint_nt35510` | No change: route remained at 1 violation and full DRC remained at 2 `m2.2` violations | Rejected; diagnostic round stopped after two ineffective single effects |
| `pin_perimeter_floor` (`DIE_AREA` sized from the PPL-0024 requested perimeter) | `pin_perimeter_axil_arb2` | Cleared PPL, route and DRC, with timing and RCX clean; fresh Netgen found a top-level pin-short/pin-match LVS failure that the place-aborted control could not expose | Subject is inconclusive/ineligible; neither positive nor negative Recipe evidence |
| `pin_perimeter_floor` | `eth_mac_1g` from `alexforencich/verilog-ethernet` | PPL cleared; route, full DRC, LVS, antenna, setup/hold timing, and RCX all clean at the protected 10 ns target | Fast-screen pass; candidate only |
| `pin_perimeter_floor` | `axi_interconnect` from `alexforencich/verilog-axi` | PPL cleared; route, full DRC, LVS, antenna, setup/hold timing, and RCX all clean at the protected 10 ns target | Fast-screen pass; candidate only |
| `MAX_REPAIR_TIMING_ITER=10` | `sha256_stream` | Exact no-change at setup WNS `-0.00451017`; all non-timing checks remained clean | Rejected; negative evidence only |
| `MAX_REPAIR_TIMING_ITER=10` | `biriscv_core` | Setup WNS improved to `-2.82188 ns` but remained violated; the 267,482-cell layout then exceeded practical full-DRC capacity and LVS was not verifiable in the isolated checker environment | Rejected for non-closure; capacity/infrastructure result is not Recipe evidence |
| `CELL_PAD_IN_SITES_GLOBAL_PLACEMENT=1` | `agr_fxp_accumulator` | `m3.2` improved from 20 to 16 while route, LVS, timing, antenna, and RCX stayed clean | Rejected as partial-only; no A/B or promotion |
| `ABC_AREA=1` | `chacha_core` | Setup WNS improved from `-2.11567 ns` to `+0.135852 ns`; route, full DRC, LVS, antenna, hold timing, and RCX were clean at 10 ns | Fast-screen pass; candidate only |
| `ABC_AREA=1` | `sha256_stream` | Setup WNS improved from `-0.00451017 ns` to `+0.411196 ns`; route, full DRC, LVS, antenna, hold timing, and RCX were clean at 10 ns | Fast-screen pass on a second independent RTL family; strict A/B required |
| `DETAILED_ROUTE_END_ITERATION=96` | `footprint_nt35510` | Exact residual: route stayed at 1 violation and full DRC stayed at 2 `m2.2` violations; LVS, timing, antenna, and RCX remained clean | Rejected; third and final candidate in this diagnostic round |
| `ROUTING_LAYER_ADJUSTMENT=0.70` | `usbf_device` | The detailed router failed to converge within the 3,600-second candidate budget; the probe terminated with `ROUTE_TIMEOUT` and produced no signoff-eligible result | Rejected; runtime-budget failure and negative evidence for congested USB-class subjects |
| `ABC_AREA=0` control | `jesd204b` at 172 MHz | Setup WNS was `-0.48651 ns`; the historical `ABC_AREA=1` realization improves this to `-0.0160414 ns` but still does not close timing | Improvement-only mechanism canary; not 100 MHz evidence and not a promotion vote |
| `CELL_PAD_IN_SITES_DETAIL_PLACEMENT=1` | `siliconcompiler/gcd` | Exact no-change: full DRC remained at 6 `m3.2`; all non-DRC checks remained clean | Rejected; negative evidence only |
| `CELL_PAD_IN_SITES_GLOBAL_PLACEMENT=1` | `siliconcompiler/gcd` | Regressed full DRC from 6 to 8 `m3.2`; all non-DRC checks remained clean | Rejected; global-regression evidence |
| `ROUTING_LAYER_ADJUSTMENT=0.70` | `siliconcompiler/gcd` | Exact no-change: full DRC remained at 6 `m3.2`; all non-DRC checks remained clean | Rejected; third and final candidate in the GCD diagnostic round |
| `ABC_AREA=1` | `ace2_absolute_rope_score_core` | Setup WNS improved from approximately `-5.07195 ns` to `-3.05727 ns`, but remained severe; route, full DRC, LVS, antenna, hold timing, and RCX stayed clean | Rejected for non-closure; no A/B or production catalog entry |

The first PPL measurement initially produced `lvs=unknown` because the isolated Git
worktree did not contain the generated local environment pin and inherited a stale
`PDK_ROOT`.  Re-running checker-only signoff with the frozen `R2G_ENV_FILE` executed Magic
and Netgen and exposed the real LVS mismatch.  The unknown result is infrastructure noise;
the fresh mismatch is the candidate's authoritative global verdict.

## A/B Causality Findings

The first strict A/B materialization was stopped before producing a trial. Both arms copied
the live-fixed subject's bare `DIE_AREA` and `CORE_AREA`; the existing reset removed only the
marked signoff-fix block. The nominal control was therefore already treated. A second run was
also stopped: after baseline restoration, the planner attempted to recover the PPL perimeter
from the newest backend log, but that log was the clean retry and no longer contained
PPL-0024. Arm B consequently received no intervention.

These are P0 experiment-integrity defects, not negative Recipe evidence. A third defect was
found after the corrected run: the lifecycle promoted a Recipe after only one independent
subject (`ab_corpus:1w0l`), despite the frozen two-family promotion rule. The local fix now:

1. restores direct backend actions from the structured `config_delta.before` snapshot;
2. requires that the copied subject still matches `config_delta.after`, otherwise planning
   fails closed;
3. stamps a semantic baseline fingerprint that is identical across arm-local path changes;
4. carries and replays the exact allowlisted `config_delta.after` effect in arm B instead of
   depending on transient historical error text;
5. counts independent `runs.design_family` values instead of project aliases and requires at
   least two independent wins before automatic promotion.

Strict A/B v3 used eight arms: two independent repositories, A/B, and two repeats. Within each
subject all four arms had one identical semantic baseline fingerprint, every B arm replayed the
exact recorded effect, and all eight run IDs were distinct and arm-owned. Both A repeats failed
PPL on each subject; all four B repeats passed route, full DRC, LVS, antenna, setup/hold timing,
and RCX at 10 ns. The two provenance-complete trials were decisive wins with no global veto,
and the current judge resolves the lifecycle to `promoted` with `ab_corpus:2w0l`.

The source probe manifests bind `axi_interconnect` to
`alexforencich/verilog-axi@516bd5dadc3365b7f9e225d2af8fe0b8d804fe53` and `eth_mac_1g`
to `alexforencich/verilog-ethernet@77320a9471d19c7dd383914bc049e02d9f4f1ffb`.
Every frozen RTL file SHA-256 was rechecked against the corresponding local Git commit. The
probe materializer now also mirrors this source identity into `metadata.json` and labels it as
fully bound only after verifying the checkout's origin URL, resolved commit, and every frozen
file against its Git blob. A plain snapshot carrying caller-supplied URL/commit strings remains
explicitly unverified, preventing declared provenance from being mistaken for proof.

Regression verification passed after the integrity fixes: the complete signoff-loop collection
contains 1,214 tests (`1,213 passed, 1 skipped`) and the probe-tool suite contains six passing
tests. The affected A/B, loop, PPL, and probe suites pass together (`133 passed`). The full suite
was executed one test file per process to avoid an unrelated monolithic pytest process being
killed under concurrent EDA memory pressure. Environment-sensitive platform tests were bound to
the frozen `env.local.sh`; every shard then passed.

The timing screens motivated a narrowly scoped `abc_area_mapping` candidate for Sky130HD, an
explicit `ABC_AREA=0`, a finite negative routed setup WNS, and a clean route. Its only mutation
was `ABC_AREA=1`; the registered clock and SDC remained byte-identical. A local implementation
was kept behind `requires_ab_promotion` while its evidence was tested, then removed when the
two-family strict-promotion gate could not be met.

The two initial 100 MHz screens did not by themselves satisfy that promotion rule. SHA-256 is the
`minor / crypto-medium` timing symptom while Chacha is `severe / crypto-large`; these are distinct
lifecycle keys and cannot be pooled as two votes for one Recipe row. A JESD mechanism canary
confirmed that the effect can materially improve timing without closing it, but it uses a 172 MHz
target and therefore cannot fill the frozen 100 MHz evidence gap. A second independent 100 MHz
severe family, ACE2, also improved but did not close. `abc_area_mapping` was therefore removed
from the production catalog; its development results remain negative/limiting evidence and it
contributes no live auto-apply capability.

Before the timing trial was launched, the A/B harness audit found a fourth integrity defect:
a timing control arm merely excluded the target strategy, allowing the next catalog strategy to
mutate arm A. The control is now measurement-only (`--max-iters 0`), while arm B alone forces the
target strategy; both arms still receive the same checker-only global signoff pass. The expanded
focused suite passes as part of the `133 passed` affected-test run above.

## Iteration 01 Verdict

This iteration promotes one new Sky130HD effect fingerprint, `pin_perimeter_floor`, with two
independent RTL-family wins and two repeats per arm. It therefore reaches `1/3` of the initial
catalog-expansion target and covers one of the required three categories. No routing, DRC, or
timing candidate from this iteration met strict closure and independent-evidence requirements;
those candidates remain negative or limiting development evidence and are absent from the
production catalog. The branch is suitable for review as one validated Recipe plus A/B and
provenance safety fixes, but it does not claim that the broader three-Recipe iteration target is
complete. The next iteration must use targeted Expander acquisition for natural Sky130HD timing,
antenna, and routing/DRC families rather than promoting partial improvements.
