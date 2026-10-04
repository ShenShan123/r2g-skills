# Sky130HD Recipe Gap and Source Audit

## Purpose and Frozen Scope

This is a development audit for expanding the R2G repair catalog, not a formal
Experiment 2 result.  It uses the frozen Sky130HD, 100 MHz, Default ORFS task.
An outcome is usable for promotion only when its RTL closure, source commit,
toolchain, protected task, strict signoff evidence, and A/B provenance are
bound and reproducible.

The audit snapshot is R2G `b31caab` and the canonical
`r2g-skills/signoff-loop/knowledge/knowledge.sqlite` store on 2026-08-23.
Historical observations may guide acquisition, but are not automatically
current evidence: older runs can use a different task, tool installation, or
source closure.

## What the Existing Evidence Actually Says

The canonical Sky130HD failure journal contains the following raw historical
signatures.  The counts include repeated attempts and are therefore a source
of leads, not an estimate of formal-test prevalence.

| Historical signature | Events | Distinct recorded RTL families | Audit interpretation |
|---|---:|---:|---|
| Route-stage failure | 61 | 28 | Largest source of natural leads; first separate true capacity/timeouts from ordinary route violations. |
| Pin-placement `FLW-0024` | 10 | 3 | Small but reproducible family; there is now one promoted pin-side remedy, so seek only a distinct pin mechanism. |
| Other place-stage failure | 8 | 6 | Requires signature-level classification before proposing a generic utilization action. |
| Synthesis-stage failure | 5 | 2 | Mostly input/memory qualification; not a physical-signoff Recipe unless the same legal source closure reaches a bounded backend recovery. |
| PDN `PDN-0185` | 2 | 1 | A real catalog gap, but insufficient independent evidence for promotion. |
| Detailed-place `DPL-0036` | 1 | 1 | Keep as a targeted acquisition lead, not a general class. |
| Global-route `GRT-0116` | 1 | 1 | Keep as a targeted acquisition lead, not a general class. |

The current production lifecycle rows show four Sky130HD strategies with at
least one promoted scope: `density_relief`, `pin_side_rebalance`,
`route_relief`, and `setup_slack_margin`.  This does **not** mean the catalog
covers all DRC, pin, route, or timing failures.  Each lifecycle row is scoped
by symptom, design class, platform, and strategy.  Existing shadow, parked,
and candidate rows are explicitly not live evidence.

A second audit deduplicated retained probe results by
`(repo_url, commit, top_module, terminal_signature)` rather than counting A/B
arms or retries.  It found 12 historical setup-timing subjects, 9 route-timeout
subjects, 4 `m3.2` DRC subjects, 5 source-closure failures, and one combined
`m2.2`/route-residual subject.  This confirms broad timing as the only
high-recurrence physical gap that can be investigated without a footprint
change.  The `m3.2` class already has a promoted pin-side mechanism; route
timeouts require a separately registered area tradeoff; and source-closure
failures belong to acquisition qualification rather than signoff repair.

### Baseline Normalization Versus a Recipe

Some older successful actions are now part of the deterministic project
materialization policy.  Current `mk_sky130_project.py` already sets
`ABC_AREA=1`, chooses a pin/PDN/cell-aware initial floorplan, preserves a
needed synthesis-memory setting, and installs the feedthrough-buffer hook.
Those changes improve the **baseline pipeline** and should remain frozen for
every method; they must not be reintroduced as a repair Recipe by deliberately
starting from an obsolete configuration.  Likewise, a PPL die enlargement is
a floorplan-feasibility recovery, not comparable to a fixed-area DRC or timing
repair unless an explicit area budget is pre-registered.

The recipe program therefore contains only adaptive, bounded actions that are
not already part of the current default configuration.  Every new natural
failure is first replayed with the current materializer before it is counted as
a catalog gap.

On this audit date, the development probe was also aligned to that frozen
baseline (`CORE_UTILIZATION=20`, `PLACE_DENSITY_LB_ADDON=0.20`, and
`ABC_AREA=1`). Historical probe records that start from `ABC_AREA=0` remain
useful mechanism observations, but cannot demonstrate a new adaptive gain.

The latest development iterations further narrow the gaps:

| Gap | Existing result | Status for next work |
|---|---|---|
| Broad setup timing | `ABC_AREA=1` improved several crypto subjects but failed to close them or introduced regression; the current promoted timing scope is narrow. | High priority: obtain two natural, medium-sized, reproducible timing subjects and test a new bounded effect only after diagnosis. |
| Route/congestion beyond the demonstrated scope | Lower utilization can clear some route aborts but changes the effective footprint and has failed to transfer to capacity-limited designs. | Separate area--performance track only.  Under the fixed-task policy, classify timeout/capacity and do not seek a promotion claim. |
| DRC geometry beyond `m3.2` | Several padding/routing-layer attempts on residual `m2.2` and `m3.2` cases were no-ops or regressions. | Medium priority only for a non-footprint, distinct, reproducible rule class; do not retry equivalent effects under new names. |
| PDN/floorplan | `PDN-0185` is insufficient width for the PDN straps in the available core. | Fixed-footprint exclusion: record as an admission/canary mechanism, not a Recipe gap. |
| Antenna | No qualified natural Sky130HD pair is currently bound. | Medium priority: screen for true antenna findings; platform or deck absence is never a design symptom. |
| LVS mismatch | The available top-pin mismatch is not safely repairable through the public physical-configuration action space. | Do not force into a Recipe.  Treat as ineligible unless a bounded, non-RTL, non-deck remedy is demonstrated. |

Before new promotion work, reconcile the static action catalog with the
lifecycle database.  In particular, `pin_perimeter_floor` has historical
development evidence but is not currently a promoted Sky130HD lifecycle row
in the canonical store.  Its archived A/B trial predates the current evidence
schema: the stored trial has `target=null`, the run rows have no bound clock
period, and its failed A arms have no complete global-state vector.  The old
result is therefore a useful reproduction lead, **not** importable promotion
evidence.  Since the current materializer now handles pin-aware sizing, it is
classified as a baseline-feasibility regression lead rather than a priority
signoff Recipe; revalidate it only if an eligible current-baseline PPL failure
reappears under an explicit area policy.

## RTL Source Order

The source should be selected by evidence quality, not by how quickly a new
repository can be cloned.

1. **Source-bound historical failures.** Reuse projects with retained source
   manifest, baseline logs, strict reports, and a known natural signature.
   Re-run a fresh baseline under the frozen task before treating the old
   failure as current.
2. **Experiment-1 qualified RTL union.** These candidates already have a
   repository, commit, top, compilation closure, and synth-only evidence.
   Select by underrepresented structural features rather than at random.
3. **Existing repair-needed pools and prior Expander output.** These provide
   breadth, but each candidate must be requalified because earlier pools used
   older commits, tool pins, or experimental budgets.
4. **Targeted Expander acquisition.** Use the uncovered symptom and a desired
   structural predictor to guide search: wide arithmetic/crypto/FIR/CORDIC
   datapaths for timing; bus fabrics, packet pipelines, matrix accelerators,
   or wide mux networks for congestion; and high-port-count interconnects for
   pin placement.  Search is a candidate generator, never a failure labeler.
5. **Public benchmark and regression repositories.** Use them to add family
   diversity or reproduce a clearly documented physical mechanism.  Functional
   RTL bug suites are generally poor primary sources because their labels do
   not predict an ORFS/signoff failure.

The canonical journal has one `PDN-0185`, one `GRT-0116`, and one `DPL-0036`
lead, but their recorded `/proj/workarea/...` project paths and source manifests
are no longer available in this workspace.  They are therefore **not**
source-bound historical subjects under item 1.  Preserve their signatures as
targeted acquisition terms, but rebuild any candidate from a public URL and
exact commit before assigning a natural failure label or spending an A/B vote.

The public ORFS `PDN-0185` discussion identifies its mechanism as insufficient
core width for the registered PDN straps; the documented remedy is to change
core/die sizing.  It is therefore an area-policy case, not a high-value
fixed-footprint Recipe gap.  Similarly, HighTide documents a `GRT-0116` case
caused by an electrically incorrect generated SRAM LEF; that is an input
collateral qualification failure, not evidence for a generic routing action.
These signatures remain useful for canaries and classifier checks, but must be
excluded from positive fixed-task promotion unless the task policy explicitly
allows the corresponding footprint change or establishes that the LEF is valid.
References: https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/discussions/3042 ;
https://github.com/VLSIDA/HighTide/blob/main/CLAUDE.md .

The public sources have distinct roles and must not be mixed together in the
evidence ledger:

| Source class | Appropriate use | Excluded use |
| --- | --- | --- |
| ORFS and OpenLane regression designs | Toolchain canaries, reproducible clean controls, and documented failure-mechanism references. | Natural promotion votes after importing their already-tuned design configuration. |
| HighTide designs | Diverse, pinned RTL families and cross-platform feasibility references.  Its published flow is explicitly tuned while preserving a clean result, so retain only the upstream RTL closure when testing R2G's default policy. | Treating a HighTide platform configuration or its iterative tuning history as a new R2G Recipe. |
| RTL-Repo and other repository-level RTL corpora | Discovery breadth for real multi-file RTL repositories.  Every selected candidate still needs a license, exact commit, closure, and Sky130HD qualification. | Assuming a functional RTL benchmark label predicts a physical-design failure. |
| OpenLane intentionally broken regressions and synthetic stress cases | Classifier, fail-closed, and action-safety canaries. | A/B subjects, success-rate denominator, or positive learning evidence. |

This distinction follows the public project descriptions: ORFS exposes a
complete Sky130HD RTL-to-GDS tutorial and per-design configuration surface,
while HighTide explicitly tunes utilization, timing, and placement as part of
its maintenance flow.  They are useful sources of reproducible *mechanisms*,
not unmodified evidence that a newly proposed R2G effect transfers.  Reference
URLs are recorded here for acquisition provenance: OpenROAD Flow Scripts
tutorial, https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts/blob/master/docs/tutorials/FlowTutorial.md ;
HighTide, https://github.com/VLSIDA/HighTide ; RTL-Repo,
https://arxiv.org/abs/2405.17378 .

Artificial stress designs are allowed only for guardrail canaries.  They must
be labelled artificial and cannot supply a promotion vote or paper success.

## Repeatable Acquisition and Validation Funnel

```text
source URL + commit + license
  -> compilation/dependency closure and synth-only qualification
  -> size and structural-feature screen
  -> Default ORFS baseline x2 (third run only on disagreement)
  -> reject environment/input/non-deterministic outcomes
  -> deduplicate by source family and protected-task digest
  -> catalog-covered or catalog-uncovered classification
  -> candidate Recipe screen (at most three non-equivalent effects)
  -> strict A/B: two independent natural families, two repeats per arm
  -> global non-regression + promotion, or archived negative evidence
```

The baseline classifier must record all check results, not merely its first
failure: ORFS stages, route, DRC class vector, LVS, antenna, setup/hold, RCX,
constraint hashes, and toolchain identity.  This prevents a local improvement
from being credited when it creates a more serious regression elsewhere.

### Fixed-Target Clean Versus Publication Clean

This Recipe-development campaign fixes the registered target at 100 MHz.  Its
success predicate is **fixed-target physical strict clean**: completed ORFS,
clean route/DRC/LVS/antenna, non-negative setup and hold, complete RCX, and
run/artifact binding.  It deliberately does not run a per-design Fmax search.
The repository's `signoff_manifest.json` has a stricter *publication* predicate
that additionally requires an Fmax-search winner and a stamped clock period.
Consequently a development probe can have `signoff_gate=pass` and
`fixed-target physical strict clean=true` while
`publication_strict_clean=false`.  This is an intentional scope distinction,
not contradictory signoff evidence; a Recipe promotion must use the former,
and a graph-data publication must satisfy the latter.

Current replay result: the complete Git-bound secworks Chacha family is
fixed-target physical strict clean at 100 MHz under the current materializer
(route/DRC/LVS/antenna all zero, setup WNS +0.227014 ns, hold WNS +0.450907 ns,
RCX complete).  It is therefore a clean control, not a current timing-failure
subject.  The historical failure was observed under an older probe baseline
and cannot contribute positive or negative Recipe evidence.

### Current-Baseline Replay Ledger

| Family | Current result | Role in this iteration |
| --- | --- | --- |
| secworks Chacha | Fixed-target physical strict clean. | Clean control; historical timing observation invalidated by the current baseline. |
| secworks Blake2s | Only setup timing fails (WNS -2.965470 ns); route, DRC, LVS, antenna, hold, and RCX are clean. | Eligible natural broad-timing subject.  The first bounded `backend_aware_synth_retune` screen is a negative result, recorded below; do not relabel the same synthesis-effect combination as a fresh candidate. |
| secworks SHA-256 Stream | Fixed-target physical strict clean: route/DRC/LVS/antenna are zero, setup WNS +0.391975 ns, hold WNS +0.467271 ns, RCX complete. | Clean control; the historical candidate is not a current timing-failure subject. |
| AstraCore Matrix accelerator | Fixed-target physical strict clean: route/DRC/LVS/antenna are zero, setup WNS +0.597352 ns, hold WNS +0.434066 ns, RCX complete. | Clean control; its older timing observation is not reproducible under the current Git-bound task. |
| ACE2 attention score core | Fixed-target physical strict clean: route/DRC/LVS/antenna are zero, setup WNS +1.454370 ns, hold WNS +0.485204 ns, RCX complete. | Clean control; the historical severe timing observation is not reproducible under the current Git-bound task. |
| ACE2 absolute RoPE score core | Terminal Git-bound fixed-target replay: setup WNS -3.121820 ns; hold WNS +0.466461 ns; route/DRC/LVS/antenna are clean and RCX is complete. | Eligible third independent natural broad-timing subject. It is distinct from the clean ACE2 attention-score control despite sharing the upstream repository and commit. |
| CORDIC sin/cos | Fixed-target physical strict clean: route/DRC/LVS/antenna are zero, setup WNS +6.478720 ns, hold WNS +0.340208 ns, RCX complete. | Clean control; it does not supply a timing-family validation subject. |
| EMACzero ICMP echo | Fixed-target physical strict clean: route/DRC/LVS/antenna are zero, setup WNS +4.444900 ns, hold WNS +0.452432 ns, RCX complete. | Clean control; the high-pin routing lead is not a current failure under the registered task. |
| CORDIC core | Fixed-target physical strict clean: route/DRC/LVS/antenna are zero, setup WNS +6.248990 ns, hold WNS +0.471589 ns, RCX complete. | Clean control; the independent arithmetic implementation is not a timing-failure subject. |
| TSA systolic-memory top | Fixed-target physical strict clean: route/DRC/LVS/antenna are zero, setup WNS +1.132700 ns, hold WNS +0.475609 ns, RCX complete. | Clean control; the largest targeted source did not reproduce a capacity or timing failure. |
| Cascade SoC | Synth abort: largest inferred memory is 65,536 bits and Default ORFS rejects it at `SYNTH_MEMORY_MAX_BITS=4096`. | Ineligible for flip-flop memory recovery: the registered safety limit is 16,384 bits; a real RAM macro is required. Record as macro-required capacity evidence, not as a Recipe failure. |
| crypto-accelerator-chip | Approximately -60 ns setup deficit before a useful physical closure.  The run was intentionally stopped during floorplan. | Structural-timing-infeasible under the registered 100 MHz task; excluded from Recipe evidence and retained only as an admission-screen example. |
| caravel-sha256-accelerator | Fixed-target physical strict clean: route/DRC/LVS/antenna are zero, setup WNS +0.609754 ns, hold WNS +0.263179 ns. | Clean control.  Its thousands of transient detailed-route violations were not a valid terminal failure label. |
| skaarler SHA-256 hardware accelerator | Terminal Git-bound fixed-target replay: setup WNS -2.008050 ns; hold WNS +0.432527 ns; route/DRC/LVS/antenna are clean and RCX is complete. | Eligible independent natural broad-timing subject. The hierarchy screen improved WNS but did not close timing; the ABC timing-mapping screen regressed WNS. Neither is learner-positive evidence. |

An admission screen is necessary before a full candidate sweep.  A design with
a timing deficit far outside the bounded physical-action envelope, or with a
capacity failure that cannot reach a stable signoff result, is a task-selection
outcome rather than a failed repair.  Record it as `structural_timing_infeasible`
or `capacity_infeasible`, retain its logs, and exclude it from both positive and
negative Recipe evidence.  This prevents a finite physical-action catalog from
being penalized for work that requires RTL pipelining, architecture changes, or
an unregistered task relaxation.

### What the Source Audit Rules Out

This round materially changes the source-selection decision. Six targeted,
Git-bound candidates from five independent repositories (Matrix, ACE2,
CORDIC-sincos, EMACzero, CORDIC-core, and TSA) all reached fixed-target
physical strict clean at 100 MHz. Together with the already clean Chacha,
SHA-256 Stream, and Caravel controls, they establish that a medium/large cell
count, a wide interface, or an old Expander risk label is not sufficient
evidence of a current repair task.

The broader historical pool is useful only after a provenance and task audit:
many old `pdn`, `pin`, and `drc` directories deliberately fixed a die/core
area, excluded pin sides, or used a non-100-MHz target to create a diagnostic
condition. Those are valid mechanism canaries, but are excluded from this
natural-failure Recipe campaign because their condition is operator-created.

### Expander Tranche 2: Capacity Admission Rejection

The first six-design Expander screening tranche is a discovery-only source
filter, not promotion evidence: each selected source comes from a certified
snapshot and must still be recloned at its recorded Git commit before a
candidate can enter A/B.  Its first result, EMACzero `eth_mac`, stopped during
synthesis because its largest inferred memory is 180,224 bits while the frozen
no-macro task permits at most 4,096 bits.  The result is therefore
`SYNTH_MEMORY_CAPACITY` / `capacity_infeasible`, not a physical repair task and
not a generic flow failure.  It is excluded from replay, Recipe learning, and
promotion; the valid path would require an explicit RAM-macro task, which is
outside this fixed no-macro campaign.

This result exposed a probe bookkeeping defect: the wrapper previously emitted
the generic `FLOW_EXECUTION_FAILED` signature for this known ORFS synthesis
guard.  The local probe and cohort runner now classify the guard explicitly and
exclude it from automatic failure replay.  The regression test covers the
exact `Synthesized memory size ... exceeds SYNTH_MEMORY_MAX_BITS` form.

The same tranche also exposed a second admission-only condition: a source whose
explicit RTL list overlaps a source-level include can stop Yosys with
`Re-definition of module`. This is now classified as
`SYNTH_MODULE_REDEFINITION` / input-qualification failure rather than as an
unclassified physical-flow error. It must be repaired in the acquired
compilation closure before it can be considered for any signoff Recipe.

### Expander Tranche 3: Bounded Cell-Count Admission

The next Expander tranche was run only to test whether source selection could
produce a current, natural fixed-task repair subject. The runner now performs
`synth + floorplan` before full P&R and accepts only **100--100,000**
elaborated cells. Both limits are qualification policy, not adaptive repair
actions: below 100 cells is outside the Experiment-1/2 useful-design floor and
above 100,000 cells belongs to the separately reported large-design track.

| Source/top | Elaborated cells | Terminal classification | Evidence use |
| --- | ---: | --- | --- |
| fpga-ptp `net_acq_axi_slave` | 137,133 | `scale_ineligible` | Excluded before placement; no learner/replay/A-B evidence. |
| OpenEye `fpga_core` | not reached | `execution_interrupted` during Yosys hierarchy expansion, without a run terminal record | Excluded; no physical failure label is inferred. |
| low-latency-ethernet `tcp_entry` | 98 | `scale_ineligible` | Excluded below the frozen useful-design floor. |
| zynq-ethernet-switch `ethernet_switch` | 10,999 | fixed-target physical strict clean | Clean control: DRC/LVS/route/antenna zero, setup +3.404660 ns, hold +0.474673 ns, RCX complete. |
| cascade-soc `cascade_core` | 20,891 | fixed-target physical strict clean | Clean control: DRC/LVS/route/antenna zero, setup +1.044960 ns, hold +0.442408 ns, RCX complete. |

The two clean results are deliberately retained as source-diverse controls even
though both showed transient detailed-route violations. A completed flow and
strict signoff resolved them to zero, so neither may be relabelled as route
failures or used to manufacture a Recipe. The two scale exclusions and the
interrupted execution are likewise retained only as acquisition/admission
evidence. This five-source tranche supplies no new natural repair challenge
and no promotion vote.

### Expander Tranche 4: Constraint-Coverage Admission

The next four source-diverse candidates exposed a stricter qualification
requirement than cell count alone. The initial automatic clock selector creates
one 100 MHz clock. A finite WNS for that clock is not a valid timing result if
other sequential endpoints remain outside the SDC. The probe now parses the
ORFS floorplan `check_setup` block and rejects a candidate when the
unclocked-register-pin count is nonzero. A missing `check_setup` block fails
closed. Unconstrained endpoints that correspond only to top-level ports missing
input/output delays are recorded as an I/O-model limitation, not rejected:
this fixed task has no external interface timing model and must not mistake
ordinary output endpoints for unclocked sequential logic.

| Source/top | Elaborated cells | Constraint coverage | Terminal classification | Evidence use |
| --- | ---: | --- | --- | --- |
| ethernet-physical-layer `pcs_rx` | 46,630 | 7,302 unclocked register/latch pins; 7,634 unconstrained endpoints | `constraint_ineligible` | Excluded before terminal signoff; a one-clock WNS would not be a valid result. |
| AGR FPGA IP `agr_spi_bridge` | 295 | 0 unclocked pins; 30 endpoints explained by absent output delays | core-timing covered; I/O model absent | Its earlier legacy full flow is reclassified only after current report collection; never use the old preflight alone as promotion evidence. |
| MAC accelerator `mac_accumulator` | 981 | 0 unclocked pins; 34 endpoints explained by absent output delays | core-timing covered; I/O model absent | Eligible for a fresh full physical run under the bounded task. |
| FPGA hardware-security vault `master_locksmith_enhanced` | not reached | no `check_setup` observation; Yosys module redefinition | input qualification failure | Excluded; repair requires an unambiguous source closure, not a physical Recipe. |

This finding fixed two runner-level evidence holes. First, a candidate with a
finite WNS on a selected clock could otherwise be labelled strict-clean while
thousands of registers were untested. Second, pre-coverage probe records were
previously treated as implicitly admitted after the new gate was introduced.
Current records must carry an explicit `constraint_coverage.status=complete`;
legacy records are re-run through the bounded synth--floorplan preflight. The
corresponding code commits are `0e4d9ce`, `79559d5`, `a15e153`, and `b31caab`.
The final rule deliberately distinguishes unclocked sequential logic from
unconstrained top-level I/O: only the former invalidates the internal timing
claim under this fixed task. Only the
`pcs_rx` and module-redefinition subjects are excluded by this tranche; no
legacy or I/O-limited result supplies Recipe replay, learner evidence, A/B
votes, or promotion claims before a current terminal run is bound.

### Expander Tranche 5: Current-Rule Full-Flow Controls

Three additional source-diverse candidates were materialized and run end to
end only after the final constraint-coverage rule was in place. All three had
zero unclocked register/latch pins. Their unconstrained endpoint counts match
top-level outputs without an external I/O timing model, so they are recorded
as scope metadata rather than treated as a false timing failure.

| Source/top | Elaborated cells | Terminal fixed-target result | Evidence use |
| --- | ---: | --- | --- |
| AGR FPGA IP `agr_fxp_accumulator` | 792 | strict clean: route/DRC/LVS/antenna zero; setup +7.082100 ns; hold +0.507277 ns; RCX complete | Clean control only. |
| RMII PHY `rmii_phy_if` | 224 | strict clean: route/DRC/LVS/antenna zero; setup +8.761990 ns; hold +0.067496 ns; RCX complete | Clean control only. |
| hardware-security-vault `key_derivation` | 10,887 | strict clean: route/DRC/LVS/antenna zero; setup +4.146330 ns; hold +0.443363 ns; RCX complete | Clean control only. |

`key_derivation` is especially useful as a classification check: its detailed
routing started with thousands of transient violations and required antenna
diode insertion, yet terminal route, antenna, full DRC, LVS, and timing all
closed cleanly. Intermediate router counters are therefore not a natural
repair label and must never trigger Recipe generation. The Tranche 5 summary
is 3/3 fixed-target physical strict-clean, 0 repair challenges, and 0
environment/input/coverage failures.

Together with the re-collected `agr_spi_bridge` and the fresh full rerun of
`mac_accumulator`, this adds five current-rule source-diverse clean controls.
`mac_accumulator` completed with route/DRC/LVS/antenna zero, setup +5.430870
ns, hold +0.480773 ns, and complete RCX. `pcs_rx` remains the only Tranche 4
candidate excluded for true internal coverage incompleteness;
`master_locksmith_enhanced` remains an input-closure failure. None of these
controls is a Recipe success, A/B vote, or promotion input.

The completed six-design tranche yielded two capacity exclusions, one
input-closure exclusion, and two fixed-target physical clean controls. The
remaining DES-pipelined lead (`des_top`) reached terminal route/LVS/antenna/
timing/RCX clean status but retained 16 full-deck `m3.2` violations. Its snapshot
declares repository `sarkar22/des-pipelined-verilog`, commit
`b6f3ded994896196e81a9621d66efbae86b4c03c`, and a four-file closure. That exact
repository and source digest have now been rebuilt locally for an independent
Git-bound baseline replay. Until that replay reaches the same terminal
signature, DES is a source lead, not Recipe or A/B evidence.

The Git-bound replay subsequently reproduced the same 16 `m3.2` findings with
route/LVS/antenna/timing/RCX clean. Geometry-aware diagnosis found all 16 at
the right die edge, so the already-promoted, policy-legal
`pin_side_rebalance` effect (`PLACE_PINS_ARGS=-exclude right:*`) was screened
as exactly one configuration delta. It reached fixed-target physical strict
clean with DRC 0, route 0, LVS 0, antenna 0, setup WNS +6.263200 ns, hold WNS
+0.404928 ns, and RCX complete. This is cross-family coverage evidence for an
existing strategy, not a new Recipe, an A/B vote, or a lifecycle transition.

Consequently, the next source tranche must be selected by a two-step gate:

1. **Natural current-task admission:** exact source commit and complete RTL
   closure, unchanged Default ORFS, Sky130HD at 100 MHz, and a repeated terminal
   strict failure.
2. **Near-boundary triage:** retain only failures whose deficit/mechanism lies
   inside the registered action envelope. A severe architectural timing deficit,
   a capacity timeout, or an RTL/LVS logic mismatch is recorded but not used to
   manufacture a physical Recipe.

If this gate yields too few candidates, the scientifically correct remedy is a
separate, pre-registered **boundary-task development track** (for example a
slightly tighter but still fixed timing target) rather than silently injecting
stress edits into the 100 MHz task. Recipes learned there must retain their
task-scope key and cannot be presented as 100 MHz natural-failure evidence
until independently validated at 100 MHz.

The current timing diagnosis also ranks `utilization_reduce` as automatically
applicable.  That is not eligible for positive evidence in this fixed-area
campaign: lowering `CORE_UTILIZATION` enlarges the effective floorplan.  It
must either be blocked by a registered area invariant or handled as a separately
reported area--timing tradeoff with explicit operator approval.  The present
screen uses only `backend_aware_synth_retune`, whose declared effect is confined
to the synthesis configuration.  The executable contract is
`sky130hd_100mhz_fixed_task_repair_action_policy.json`; every future diagnose,
sandbox, and A/B command for this campaign must set
`R2G_REPAIR_ACTION_POLICY_FILE` to that file.

### Screened Negative Evidence: Blake2s Timing

The first isolated screen used the exact Git-bound secworks Blake2s source at
commit `dbecf482a6670fe0872621a8b25bf5b48efd9051`. Both runs retained the
same protected-task digest
`87b279787bc312a047d6a87d096ba1e39e885d809a9c5b1977a39158fd42374b`:
Sky130HD, the frozen 100 MHz SDC, complete source closure, and unchanged
floorplan fields (`CORE_UTILIZATION=20`, `PLACE_DENSITY_LB_ADDON=.20`). The
candidate changed only the registered synthesis effect: `ABC_AREA=1 -> 0` and
`SYNTH_HIERARCHICAL=0`.

The full candidate run completed ORFS and strict measurements: route and DRC
had zero violations, LVS was clean, antenna had zero violations, hold WNS was
+0.444105 ns, and RCX was complete. It nevertheless failed the registered task
because setup WNS changed from the natural baseline's -2.965470 ns to
-3.511650 ns, a 0.546180 ns regression. Its strict gate was therefore
`pass_with_caveats`, not fixed-target physical strict clean. This is valid
negative evidence for the effect fingerprint `{ABC_AREA: 0,
SYNTH_HIERARCHICAL: 0}` on a broad-timing subject; it is not a candidate, A/B
vote, promotion, or proof that every timing action is ineffective.

Accordingly, do not retry this identical synthesis combination under a new
strategy name. Continue only with a genuinely distinct, policy-legal effect
after at least one additional independent natural setup-failure replay has
been classified.

### Current Natural Timing Subjects: Skaarler SHA-256 and ACE2 RoPE

The independent Git-bound repository
`skaarler2005/sha256-hardware-accelerator` at commit
`c67b70378270c27b347c21f47a7f523c1198c2c4` was replayed from the exact four-file
closure (`compression.v`, `constant_k.v`, `message_expand.v`, and
`top_sha256.v`) under the frozen Sky130HD/100 MHz default task.  ORFS completed
in 2445.723 s and strict DRC/LVS then completed normally.  The terminal result
is a cleanly isolated setup failure: setup WNS is -2.008050 ns while hold WNS
is +0.432527 ns, route/DRC/antenna violations are all zero, LVS has zero
mismatches, and RCX is complete.  This supersedes its historical WNS record
and gives the campaign a second independent, current natural timing family
alongside Blake2s.

The natural Skaarler result does not promote any Recipe by itself.  It was used
to screen the distinct, policy-legal one-knob effect
`SYNTH_HIERARCHICAL=1`; all area, clock, source, and check-set fields remain
bound.  A terminal screen that still fails creates neither a learner update nor
negative evidence until the exact effect is repeated on the independent
Blake2s family.  A terminal screen that closes both subjects is only the entry
condition for the subsequent repeated strict A/B campaign.

That hierarchy-preserving synthesis screen has now completed in its own
workspace.  It improved Skaarler setup WNS from -2.008050 ns to -1.393290 ns
(+0.614760 ns), while route, DRC, LVS, antenna, hold, and RCX remained clean.
This is an encouraging mechanism observation, but it is **not** strict clean:
it cannot be called a win, emitted as positive learner evidence, or promoted.
The resulting longer ORFS runtime (3753.057 s versus 2445.723 s) is also
recorded rather than hidden.  The only justified next screen is a distinct
single effect or a bounded combination whose second effect addresses the
remaining setup deficit without relaxing clock, floorplan, or checks.

The second one-knob screen, `ABC_AREA=0`, completed in a separate verified
workspace.  It retained zero route/DRC/antenna violations, clean LVS, positive
hold slack, and complete RCX, but setup WNS regressed from -2.008050 ns to
-3.215180 ns.  It is a terminal non-promoting result for the exact
`{ABC_AREA: 0}` effect on this subject.  It must not be renamed or counted as
positive evidence, and it does not establish a general ranking rule beyond
this effect and task scope.

The Git-bound ACE2 absolute RoPE score core now provides a third natural
setup-only subject: WNS -3.121820 ns, hold WNS +0.466461 ns, and all
route/DRC/LVS/antenna/RCX checks clean.  ACE2 RoPE, Skaarler, and Blake2s come
from three independent repositories.  This does show that the currently tested
timing actions have not yet produced a terminal strict-clean closure across a
meaningful natural subject set; it is not proof that every legal timing action
is ineffective.

### Cross-Family Safety Screen: Hierarchy Preservation on Blake2s

The same single effect, `SYNTH_HIERARCHICAL=1`, was replayed on the independent
Blake2s subject.  Setup improved from -2.965470 ns to -2.176530 ns, but the
flow introduced 70 `m3.2` DRC violations.  Route, LVS, antenna, hold, and RCX
otherwise remained clean.  The geometry diagnostic located all 70 violations
on the right die edge, where the existing `pin_side_rebalance` mechanism could
address the induced pin condition.  This is nevertheless a global regression:
the hierarchy effect is neither a win nor positive evidence.  Combining it
with an existing pin-side action on this one subject would be a mechanism
investigation only, not evidence for a new general timing Recipe, because the
Skaarler hierarchy run had no corresponding pin failure and still did not
close setup timing.

### Invalid Execution Record: Blake2s Setup-Margin Screen

The first attempt to screen the distinct single effect
`SETUP_SLACK_MARGIN=0.2` is deliberately excluded from all Recipe evidence.
Two invocations used the same materialized project and therefore the same
ORFS workspace tag (`repair_probe_blake2s_setup_margin_setup_margin_0p2`).
The first run stopped at placement when its temporary log had been moved by
the competing invocation (`mv: cannot stat ...3_1_place_gp_skip_io.tmp.log`);
the second returned before a complete terminal flow record. Neither outcome
measures the candidate effect. The project is retained for forensic
provenance, marked `invalid_concurrent_workspace_attempt`, and will not be
ingested as a failure, a negative result, or an A/B arm. Any retry must use a
new project, task ID, and ORFS workspace, after confirming that no flow owns
that workspace.

A first isolated retry (`blake2s_setup_margin_0p2_rerun1`) is also excluded:
the flow was launched through a non-persistent sandbox background process and
was reaped before reaching a terminal record.  It neither shared an ORFS
workspace nor produced a candidate outcome, but it still fails the campaign's
execution-integrity requirement.  The next retry will use a persistent tmux
session and a fresh workspace; only its terminal report may be classified.

The persistent rerun (`blake2s_setup_margin_0p2_rerun2`) is also excluded.
It began in a unique workspace, but a still-running child from the first,
contaminated workspace was discovered during recovery.  The campaign then
terminated all Blake processes to restore single-owner execution before the
rerun reached a terminal report.  Its intermediate routing values are not
used, even though they were unfavorable.  This is an
`invalid_orphan_cleanup_attempt`, not screened negative evidence or a
candidate A/B arm.  A valid retry may start only after a host-level process
check confirms that no old Blake flow remains.

The clean, single-owner rerun (`blake2s_setup_margin_0p2_rerun3`) passed that
preflight and was deliberately stopped at the registered **fast-screen**
boundary rather than run through unrelated DRC/LVS work.  Immediately after
floorplan, 509 setup endpoints remained and the candidate's in-flow WNS was
-5.521 ns, already 2.556 ns worse than the -2.965470 ns baseline.  The
single-effect candidate is therefore `screened_out_early_nonclosure`.  This
is a valid decision to decline A/B planning, but not terminal negative
evidence: no signoff result was generated and it must not enter the learner.

## Priority Rule

For each candidate failure family, score four fields before spending a full
flow: recurrence across independent families, absence of an applicable
promoted effect, likelihood that a legal configuration/Tcl action can address
the mechanism, and expected runtime.  Prioritize high-recurrence,
catalog-uncovered, bounded-action, medium-cost subjects.  A large timeout-only
design belongs in the capacity track unless it can be shown to be a routable
congestion problem under the ordinary budget.

For a selected gap, propose no more than three candidates in one diagnostic
round: two distinct single effects and one bounded combination only when the
diagnosis requires both.  Every candidate must declare its normalized effect
fingerprint, preconditions, protected fields, rollback, stop condition, and
expected stage.  Equivalent effects share negative evidence and cannot be
retried merely under a new strategy name.

## Promotion and Publication Rule

A fast-screen closure creates a candidate only.  Production promotion requires
two independent natural RTL families, two repeats per A/B arm, distinct
arm-owned run IDs, exact Recipe/version/hash validation, complete provenance,
strict DRC/LVS/route/antenna/timing/RCX evidence, and no protected-task or
global-result regression.  Failed, partial, timeout, no-op, or inconclusive
trials remain in the evidence ledger and never become positive learning data.

The queued Git-bound SHA-256 Stream, AstraCore Matrix, ACE2 attention, and
CORDIC replays are all strict clean.  Blake2s, Skaarler SHA-256, and ACE2 RoPE
are current natural setup-only subjects.  The hierarchy and ABC timing-mapping
screens show partial improvement or regression, but no strict-clean closure;
therefore no timing candidate is eligible for A/B yet.  The next execution
queue is: (1) complete an effect-fingerprint and source-gap audit before
spending more full timing flows; (2) materialize remaining exact Git-bound
Expander leads before claiming a new route, pin, DRC, PDN, or antenna family;
(3) replay historical route leads to identify a mechanism distinct from
utilization relief; (4) classify residual DRC by exact rule and geometry
before another action is proposed; and (5) use targeted Expander acquisition
only for still-missing physical-failure families.  If no policy-legal action
achieves terminal closure, record that limitation of the current action set
rather than manufacture a promotion trial.

## Iteration Outcome and Stop Condition

The current expansion pass is complete. It added one evidence-safety repair
(the internal sequential constraint-coverage gate) and validated five new
current-rule clean controls across Tranches 4 and 5, but it produced **zero**
new stable natural repair challenges and therefore **zero** new candidates,
A/B trials, or promotions. This is a useful negative result: under the frozen
Sky130HD/100 MHz/default-ORFS/no-macro/fixed-footprint task, broad acquisition
of medium-sized clocked RTL is not an efficient way to create additional
repair evidence.

The only recurring current physical gap remains broad setup timing in
Blake2s, Skaarler SHA-256, and ACE2 RoPE. Every pre-registered non-equivalent
effect available in the frozen action contract has either failed to close it,
regressed a global check, or been screened out before signoff. Consequently
the gap is presently **not fillable by the registered fixed-task action set**;
running more random clean controls would not change that conclusion. The next
scientific step is to pre-register either (a) a genuinely new bounded timing
effect and then test it on these existing independent families, or (b) a
separate tighter boundary-task development track. Neither should be silently
mixed into the 100 MHz evidence ledger.
