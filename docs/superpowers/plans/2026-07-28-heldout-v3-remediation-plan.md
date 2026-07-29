# Held-Out V3 Remediation Plan

Date: 2026-07-28  
Last updated: 2026-07-29  
Target Agent baseline: `ff01d5ccd62fa53e167446bcf33dce9911bda288`

## 1. Objective

Resolve the six production-Agent defects confirmed by the end-to-end held-out,
frontend-development, and reserved frontend-validation cohorts without
weakening strict signoff, globally raising resource limits, or changing the frozen
evaluation cohorts. Production changes should be small enough to review independently
and must be accepted by focused negative controls before either three-platform cohort is
rerun.

## 2. Priority Summary

| ID | Severity | Change | Scope | Primary risk addressed |
|---|---|---|---|---|
| RMD-HO-P0-01 | P0 | Semantic RTL readiness gate | Medium | Untrustworthy graph data |
| RMD-HO-P1-01 | P1 | Authoritative failure capture and bounded memory recovery | Medium | False exclusion and low yield |
| RMD-HO-P1-02 | P1 | Stage-scoped Recipe and A/B eligibility | Medium | Invalid experiments and evidence pollution |
| RMD-HO-P1-03 | P1 | Capability-aware frontend routing and classification | Medium | False source-quality exclusion |
| RMD-HO-P1-04 | P1 | Transactional rollback of rejected live repairs | Medium | Inconsistent active physical state |
| RMD-FE-P1-01 | P1 | Manifest-, package-, and preprocessor-aware closure | Medium | Incomplete autonomous compilation input |
| LIM-HO-01 | Limit | Nangate45 strict-DRC runtime policy | Small | Runtime predictability |

## 3. RMD-HO-P0-01: Add a Semantic RTL Readiness Gate

### Problem

Acquisition can promote RTL that the source repository explicitly declares unfinished and
that synthesis reports as structurally incomplete. Physical signoff alone cannot establish
functional validity, so such a design could produce a misleading graph dataset.

### Proposed change

Add a promotion-time `rtl_readiness` result with evidence from three sources:

1. **Repository health:** detect strong, local declarations such as “not completed,”
   “does not work,” deprecated, or placeholder. Store the matched file, line, and digest.
2. **Structural synthesis integrity:** reject unresolved modules, blackboxes that are not
   declared dependencies, conflicting drivers, and material undriven internal logic.
   Top-level input ports and explicitly approved tie-offs must not be treated as defects.
3. **Available functional evidence:** when a repository supplies a bounded testbench or
   lint target, record whether it ran and passed. Absence is `not_available`, not a
   fabricated pass.

Use combined evidence rather than README keyword matching alone. A strong negative project
status corroborated by structural warnings must be `rejected_semantic_incomplete` or
`manual_review`; it must not auto-promote. Preserve all evidence in the source manifest so
the decision is reproducible.

### Acceptance tests

- The frozen `secworks_sha3` revision does not enter normal promotion.
- A healthy fixture whose README contains words such as “unsupported” only in historical
  notes is not falsely rejected.
- A design with an undriven top-level input but no undriven internal logic remains valid.
- A design with substantial undriven internal state is blocked or sent to manual review.
- No graph can receive `strict_clean` publication status unless `rtl_readiness` passed.

## 4. RMD-HO-P1-01: Preserve the Actual Failure and Recover Memory Cases Safely

### Problem

The acquisition layer copies a successful canonicalization log before considering the
failing synthesis log or `flow.log`. The resulting notes hide the memory-limit signature,
so recoverable cases become permanent `low_value_failure` exclusions.

### Proposed change

#### A. Select failure evidence by stage outcome

- Read the ORFS stage record or terminal command result first.
- Select the log associated with the failed stage, not the first existing filename.
- Prefer the later synthesis log (for example `1_2_yosys.log`) or `flow.log` section that
  contains the terminal exception.
- Store `failure_stage`, source log path, SHA-256 digest, exit code, and extracted signature
  in the candidate status.
- If the failing log cannot be resolved, classify as `diagnostic_incomplete` and retry or
  escalate; do not permanently exclude it as low value.

#### B. Correct memory accounting

- Report largest inferred memory, total inferred bits, memory count, and configured cap.
- Error text must name the observed value and cap separately.
- Reject impossible summaries such as nonzero memories with total inferred bits equal to
  zero.

#### C. Add bounded resource-aware recovery

- Classify a confirmed memory guard as `memory_limit`, not frontend low value.
- Compute a bounded next cap from observed memory size, rounded to a documented tier.
- Estimate standard-cell expansion and classify the retry as normal, high-memory, or
  deferred.
- Permit at most one automatic raised-cap synth-only retry per candidate and cap tier.
- If the estimate exceeds the execution budget, record `deferred_resource_limit` with the
  real required memory; do not call the RTL invalid and do not start physical flow.
- Do not raise the global default for every design.

### Acceptance tests

- Mor1kx, JPEG, and audio are recognized as `memory_limit`.
- Their failure records contain the actual failing-log digest and finite memory statistics.
- A bounded retry succeeds in synth-only at the selected explicit caps.
- A deliberately oversized memory fixture is deferred without OOM or permanent low-value
  exclusion.
- A genuine syntax error remains a syntax/frontend failure and does not trigger a memory
  retry.
- Repeated ingest of the same retry does not multiply evidence or retries.

## 5. RMD-HO-P1-02: Make Recipe Actions and A/B Trials Stage-Scoped

### Problem

The signoff planner accepts `acquire_exclude` because it appears in `fix_events`, although
there is no signoff apply handler for that action. It then constructs physical A/B arms
from synth-qualification workspaces and records meaningless inconclusive trials.

### Proposed change

Introduce an explicit action contract for every learnable strategy:

```text
strategy_id
action_domain: acquisition | synthesis | signoff | graph
handler_id
handler_version
subject_requirements
effect_fingerprint_schema
eligible_evaluator
```

Planning must require all of the following:

1. The strategy has a registered, callable handler.
2. The handler domain matches the candidate symptom and subject stage.
3. The subject satisfies the handler's artifact and provenance requirements.
4. The selected evaluator is valid for that action domain.
5. The planned arm can produce a nonempty, normalized effect fingerprint.

Remove the rule that any historical `fix_events` row is sufficient proof of applicability.
On registry or database read failure, do not schedule an automatic experiment. Acquisition
dispositions such as exclude, defer, retry, or manual review should be evaluated in an
acquisition/synth harness; they must never enter the signoff A/B queue.

Existing rows should be migrated or parked by domain. Do not delete their evidence, but
exclude incompatible historical trials from promotion statistics.

### Acceptance tests

- Ingesting an `acquire_exclude` event creates no signoff A/B arm or `ab_trials` row.
- A registered DRC Recipe still creates divergent signoff arms.
- A strategy present only in historical `fix_events` but absent from the handler registry
  is parked fail-closed.
- A synthesis strategy cannot select a completed signoff project as its subject unless its
  contract explicitly supports that artifact.
- Cross-domain evidence cannot promote, demote, or increase confidence for a Recipe.
- The frozen held-out campaign creates zero physical A/B trials for acquisition failures.

## 6. RMD-HO-P1-03: Make Frontend Selection Capability-Aware

### Problem

The Agent selected a missing Slang plugin for APB4 GPIO, then excluded the source when a
sv2v/Yosys fallback changed how a valid SystemVerilog function was interpreted. It also
excluded valid Verilog-2001 WB DMA because Yosys treated the port identifier `int` as a
keyword. Both original bundles compile with an independent standards-aware frontend, so
`low_value_failure` is not a defensible terminal classification.

### Proposed change

1. Build a preflight frontend-capability record containing parser/plugin availability,
   supported language revisions, executable versions, and a canary for each selectable
   frontend. Never select `slang` when its plugin canary fails.
2. Preserve the original language mode and complete compilation closure in every attempt.
   Record the original and transformed source digests separately.
3. Classify parser/tool incompatibility as `tool_compatibility` or
   `deferred_frontend`, not source-quality failure.
4. Try only registered, bounded alternate frontends whose semantics are documented. A
   local compatibility rewrite must have a normalized effect manifest and pass lint plus
   simulation or equivalence checks appropriate to the change.
5. If no verified frontend path exists, retain the candidate for manual or higher-tier
   processing. Do not auto-promote it, but do not emit `acquire_exclude` learning evidence.

### Acceptance tests

- A missing `slang.so` is detected at preflight and is never selected.
- Original APB4 GPIO is accepted by a registered SystemVerilog-capable path or receives
  `deferred_frontend`; it is not labeled low value.
- Original WB DMA is accepted in IEEE 1364-2001 mode or receives
  `deferred_frontend`; it is not labeled low value.
- A genuine syntax-invalid fixture remains rejected.
- Any transformed source has original/transformed digests and a passing semantic check
  before promotion.
- Frontend deferral creates no positive Recipe evidence and no signoff A/B trial.

## 7. RMD-HO-P1-04: Roll Back Rejected Live Repairs Transactionally

### Problem

Sky130HS SPI Flash correctly rejected a density change that introduced route and LVS
regressions. The config edit was restored, but the active-run pointer and project-level
reports remained bound to the rejected rerun. The next Agent action would therefore see
baseline configuration paired with regressed physical artifacts.

### Proposed change

Treat every live repair as an isolated candidate-run transaction:

1. Before applying it, persist the accepted run identity, config digest, report-manifest
   digest, constraint digest, and active-run pointer.
2. Run the candidate without replacing the accepted project-level evidence bundle.
3. Compare target improvement and global non-regression using immutable candidate and
   baseline manifests.
4. On acceptance, atomically commit config, active-run pointer, and reports to the
   candidate identity.
5. On rejection, atomically restore the accepted pointer and reports while retaining the
   candidate under a rejected-trial namespace for learning and audit.
6. On interruption, reconcile to either the complete old state or complete new state;
   never expose a mixed state.

### Acceptance tests

- Reproduce the Sky130HS case: baseline route 0, candidate route 1. The candidate is
  recorded as `regression`, but the active pointer and reports remain on the baseline.
- Restored config, active DEF/GDS digests, and report run tags all name one accepted run.
- A successful non-regressive repair atomically advances all of those identities.
- Interruption before and after pointer replacement reconciles exactly once.
- Rejected-run evidence remains queryable but cannot become the learner-visible terminal
  production outcome or a graph input.

## 8. LIM-HO-01: Keep Nangate45 Full DRC Strict but Predictable

Nangate45 full-deck DRC completed cleanly for USB CDC, so this is not a capability defect.
Retain full strict DRC and the existing bounded process-group timeout. Record checker wall
time, timeout reason, deck digest, GDS digest, and tool version. A cached result may be
reused only for an exact immutable artifact/deck/tool identity match. Do not substitute
BEOL-only DRC or waive checks to improve the score.

The second cohort reinforces the runtime concern: FTDI Bridge required two full checks,
lasting 4,617 and 4,590 seconds, because a real antenna repair changed the GDS. Both checks
were necessary under strict policy. Optimize checker execution or exact-identity reuse,
not the acceptance rule.

## 9. Implementation and Verification Order

1. Implement and unit-test authoritative synthesis failure capture.
2. Add bounded memory accounting, retry, and resource deferral.
3. Add capability-aware frontend routing and non-destructive deferral.
4. Add action-domain contracts and stop cross-stage A/B planning.
5. Make live-repair acceptance and rollback transactional.
6. Add the semantic RTL readiness gate and publication dependency.
7. Run focused negative controls for all five defects.
8. Rerun both unchanged cohorts on Sky130HD first.
9. If the evidence and lifecycle state are correct, rerun Nangate45 and Sky130HS.
10. Generate new commit-bound reports and compare stage yield, resource cost, and strict
   publication outcomes with this baseline.

## 10. Exit Criteria

This remediation is complete only when:

- the invalid SHA3 diagnostic fixture cannot auto-promote;
- all three memory-bearing V3 fixtures receive truthful, reproducible classifications;
- bounded retries either synthesize successfully or defer for an explicit resource reason;
- APB4 GPIO and WB DMA receive capability-truthful outcomes rather than low-value labels;
- no acquisition disposition enters the signoff A/B database;
- a rejected repair cannot leave its run or reports active;
- USB CDC remains strict-clean on all three platforms;
- FTDI Bridge remains strict-clean on all three platforms and SPI Flash remains
  strict-clean on Nangate45;
- all negative controls still pass;
- no strict-signoff or provenance gate is weakened.

Passing synth-only after a larger memory cap is not, by itself, a successful end-to-end
result. The three recovered designs must still complete Fmax, ORFS, strict signoff, graph
verification, and atomic publication before the held-out E2E rate can be increased.

## 11. Frontend Pilot Remediation Execution

The eight-fixture frontend development cohort converted the earlier recommendations into
an executable 12-gate Pilot. The production worktree was then changed and rerun against
the same frozen inputs.

| Remediation | Status | Verified outcome |
|---|---|---|
| Select the actual failed synthesis log | Implemented | ENET/MMC/AXIS classified as memory limits |
| Preserve repair fields through scoped retry | Implemented | All three reran at 131072 bits and promoted |
| Recognize common source layouts and synthesizable debug tasks | Implemented | Expected top discovery improved to 8/8 |
| Separate acquisition actions from signoff Recipes | Implemented | No new signoff A/B or Recipe lifecycle rows |
| Add explicit missing-frontend defer | Implemented | AHB3-Lite retained as `frontend_tool_unavailable` |
| Add package/submodule-aware discovery closure | Open | AHB3-Lite package still absent from discovered closure |
| Provide a verified SystemVerilog synthesis frontend | Open capability | Installed Yosys has no `slang.so` |

Final Frontend Pilot result: **92/96 gates, 8/8 safe decisions, 7/8 promotions**.

### RMD-FE-P1-01: Manifest-, package-, and preprocessor-aware closure

**Problem.** Module-instantiation traversal cannot discover SystemVerilog packages,
headers, or source ordering declared by FuseSoC and Git submodules. AHB3-Lite discovery
therefore emits the top and four modules but omits `ahb3lite_pkg.sv`. The untouched
validation cohort exposed the corresponding Verilog-preprocessor case: ZipCPU uses
`` `DIVIDE_MODULE`` and `` `MPYOP`` for implementation selection, so autonomous
`zipcore` closure omits `div.v`, `mpyop.v`, and `slowmpy.v`.

**Recommended change.** Parse recognized build manifests (`.core`, ordered file lists, and
submodule metadata) with a structured parser. Extend closure edges to package
imports/includes and preserve source order. Before source-derived module traversal, run a
bounded preprocessor using the selected defines and include directories, then map resolved
module names back to original source files. Every file must remain inside the pinned
repository/submodule provenance boundary and receive a digest. If conditional resolution
remains ambiguous, emit `closure_incomplete` and defer safely.

**Acceptance.** Autonomous discovery of the frozen AHB3-Lite revision emits the package
before dependent modules, records the submodule commit, and matches the authoritative
closure without being given the expected file list. Autonomous discovery of the frozen
ZipCPU `zipcore` revision must likewise include the six independently verified files or
produce a truthful bounded closure defer.

### CAP-FE-01: Verified SystemVerilog synthesis capability

**Problem.** Verilator accepts AHB3-Lite, but the physical-flow synthesis Yosys cannot
load Slang. This is a real tool capability gap, not invalid RTL.

**Recommended change.** Either install and canary a version-compatible Yosys Slang plugin
through `eda-install`, or register another synthesis-capable SystemVerilog path with
semantic/equivalence checks. Do not treat Verilator lint alone as synthesis proof.

**Acceptance.** Preflight declares capability only after a package/array canary passes.
The AHB3-Lite controlled closure then produces a nonempty mapped netlist and graph, or
continues to defer safely if the capability is intentionally outside V1.

### LIM-FE-02: Correct ORFS memory accounting

The ORFS memory report simultaneously states nonzero inferred memories, a 32--40 Kbit
largest instance, and zero total inferred bits. Correct `mem_dump.py` accounting and its
error message, then add a canary where total bits equals the sum of all reported
instances. Until then, use the largest-instance field for the bounded cap decision and do
not use total bits for cost prediction.

### Frontend exit criteria

- The current seven promotions remain byte- and provenance-identical under regression.
- A missing optional frontend always produces an explicit defer, never low-value
  exclusion or signoff Recipe evidence.
- Scoped retry preserves every execution-affecting field and remains bounded to one
  automatic raised-cap attempt.
- The untouched frontend cohort remains registry- and digest-frozen, and its recorded
  primary score is never replaced by a post-fix rerun.

## 12. Frontend Validation-Cohort Result and Revised Order

The reserved cohort has now been executed once without post-result production changes.
Its primary result is **91/96 gates, 8/8 safe decisions, and 8/8 controlled promotions**.
All eight closures passed independent elaboration, synthesis, graph probing, provenance,
and cross-stage isolation. This independently validates the earlier failure-evidence,
retry propagation, truthful defer, and stage-isolation changes.

The five failed cells were:

- ZipCPU `CLOSURE`, caused by unresolved macro-indirect module dependencies.
- CV32E40P `DISCOVERY` and `CLOSURE`, because the registered divider ranked below the
  64-candidate cap.
- Caliptra `DISCOVERY` and `CLOSURE`, for the same exact-submodule/candidate-cap reason.

Only the ZipCPU result is currently a confirmed production closure defect. The latter
four cells expose an ambiguity in what the validation protocol asks repository discovery
to find. Do not increase the candidate cap merely to improve the score. A future registry
must freeze separate requirements for:

1. finding at least one independently eligible, manifest-declared project top; and
2. finding an exact registered submodule when exact-submodule recall is explicitly in
   scope.

The original validation registry and 91/96 report remain immutable historical evidence.

### Revised implementation order

1. Implement preprocessor-aware local dependency closure and add the pinned ZipCPU case
   to the historical regression catalog.
2. Complete manifest/package/header closure for AHB3-Lite without weakening provenance.
3. Keep missing advanced SystemVerilog synthesis capability as an explicit safe defer
   until a canary-proven frontend is installed.
4. Correct ORFS total-memory accounting before using it for resource prediction.
5. Rerun both frontend cohorts unchanged and publish a new commit-bound score.
6. Freeze a new repository-level discovery cohort only after its project-top semantics
   are explicit; do not use the current validation cohort for further fixture tuning.

Frontend remediation is considered converged for V1 when all previously promoted
fixtures remain provenance-identical, macro/package/header closures either synthesize or
defer truthfully, no acquisition action leaks into signoff learning, and the untouched
validation cohort can be rerun without weakening any gate.
