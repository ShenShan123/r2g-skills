# R2G Agent V1 Quality Contract and Convergence Criteria

Status: **Proposed**  
Contract version: **0.1**  
Date: **2026-07-19**  
Repository baseline: `136bb7dd19f9f0a62351fae40b322467ab1a8d35`  
Official V1 platform: **nangate45**

## 1. Purpose

This document defines the supported boundary, safety invariants, fixed validation
matrix, and exit criteria for R2G Agent V1. It is the normative contract for deciding
whether V1 has converged.

The goal is not to prove that the system has no bugs. The goal is to demonstrate that,
within a declared operating domain, the Agent repeatedly converts eligible open-source
RTL into traceable, flow-qualified, verifier-clean graph datasets without false
promotion, stale evidence, or mixed artifacts.

Audit reports identify defects and provide evidence. They do not expand this contract
automatically. A newly discovered condition changes the V1 gate only through the change
control in Section 12.

The terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

### V1 at a Glance

```text
frozen scope and fixtures
+ pinned code/toolchain/schema
+ 3/3 real positive controls published cleanly
+ 10/10 negative controls blocked at the intended gate
+ 14/14 P0 trust regressions passing
+ all applicable upstream tests passing
+ two unchanged clean-workspace repetitions
= R2G Agent V1 converged
```

Anything outside the frozen boundary is recorded for V1.1/V2 unless it proves false
learning or false publication inside V1.

## 2. V1 System Boundary

### 2.1 Supported Inputs

V1 supports newly acquired, open-source digital RTL for which the system can establish:

- a resolvable source repository and immutable source revision;
- a license classified as publish-eligible;
- one unambiguous top module;
- a complete compilation-input closure accepted by the pinned frontend;
- a valid clock definition for sequential designs;
- successful synth-only qualification on nangate45.

The compilation-input manifest MUST include:

- all RTL source files and transitive include headers;
- packages, generated HDL files, and their generation provenance;
- include search order and preprocessor defines;
- top module, top parameters, and language/frontend selection;
- synthesis switches that can change elaboration or the mapped netlist;
- repository URL, source revision, relative path, size, and SHA-256 for every input;
- a normalized manifest digest.

Inputs that depend on unavailable proprietary IP, unresolved generators, unsupported
analog behavior, encrypted sources, or an unsupported HDL frontend are valid V1
rejections. V1 does not promise that every discovered repository becomes a graph.

### 2.2 Supported Platform and Toolchain

Only `nangate45` is an official V1 release platform. Other platforms MAY be exercised
experimentally, but their results do not count toward V1 closure and MUST NOT contribute
cross-platform Recipe evidence unless a later contract explicitly permits it.

Every release-gate execution MUST record one immutable toolchain snapshot containing:

- R2G repository commit and dirty-state flag;
- ORFS repository commit, including submodule revisions;
- OpenROAD, Yosys, KLayout, and all invoked signoff-tool versions;
- Python version and locked graph-package versions;
- PDK/platform file digests and rule-deck digests;
- relevant environment variables and executable paths;
- operating-system identity and CPU resource limits.

The snapshot digest is part of every V1 run and graph-generation identity. A toolchain
change starts a new validation generation; results from different toolchain snapshots
MUST NOT be silently combined.

### 2.3 Supported End-to-End Path

The official V1 path is:

```text
acquire RTL
-> qualify and freeze compilation inputs
-> synth-only
-> deduplicate and promote
-> ORFS synth/floorplan/place/CTS/route/finish
-> DRC/LVS/route/antenna/timing/RCX checks
-> extract features and labels
-> build b/c/d/e/f graphs
-> independently verify
-> atomically publish
```

Every candidate MUST reach exactly one terminal state:

```text
published
rejected
failed_with_reason
needs_human
```

An interrupted or timed-out candidate MAY be resumable, but it is not terminally
successful and MUST NOT be learnable success or publishable data.

## 3. Flow-Qualified Publication Contract

In this document, `r2g_clean` means clean under the pinned open-source flow and rule
decks. It does not claim foundry production signoff.

A graph generation is eligible for the official V1 clean index only when all of the
following are true:

1. The selected backend run completed every required ORFS stage.
2. Route status is clean and the authoritative routing-violation count is zero.
3. DRC status is clean under the pinned V1 rule deck.
4. LVS status is clean against the netlist belonging to the same backend run.
5. Antenna status is clean.
6. Timing status meets the frozen V1 constraint set.
7. RCX completed and the required SPEF is present.
8. DEF, GDS, netlist, reports, features, labels, and graphs are bound to the same run and
   generation through content digests.
9. Every required extractor completed successfully for the current generation.
10. The independent graph verifier passed every check applicable to the declared schema.

Outputs with caveats MAY be retained in a separate research area, but MUST NOT appear in
the V1 clean index and MUST carry an explicit non-clean quality tier. `warn` or manual
override modes never convert a caveated output into `r2g_clean`.

Any missing, unreadable, contradictory, stale, or unbound safety evidence MUST fail
closed.

## 4. Graph Dataset Contract

The official V1 publication unit is one immutable graph generation containing all five
`b/c/d/e/f_graph.pt` variants. `netlist_graph.pt` MAY also be published but is not a
substitute for any required post-layout view.

Each published generation MUST contain a manifest with at least:

- `schema_version` and `generation_id`;
- stable design ID, design name, platform, and flow variant;
- source-revision and compilation-manifest digests;
- toolchain-snapshot and backend-run IDs;
- DEF, GDS, mapped-netlist, SPEF, and signoff-report digests;
- feature- and label-generation IDs and input digests;
- graph kind, graph file digests, node/edge counts, and schema declarations;
- signoff verdict, verifier verdict, publication status, and timestamps.

The target schema identifier for the first frozen release MUST be assigned before this
contract changes to `Frozen`; the recommended value is `r2g.graph.v1`.

Publication MUST be transactional:

1. extract and build into a new staging generation;
2. validate completeness and provenance;
3. run the independent verifier against staging;
4. atomically switch one active-generation pointer;
5. update the corpus index only after the switch succeeds.

A failed generation MUST leave the previously active generation byte-identical. A
manifest with `status=ok` MUST never describe mixed files from multiple generations.

## 5. Agent Decision Contract

Deterministic scripts execute EDA stages and compute structured measurements. The Agent
observes those artifacts, selects bounded actions, and manages evidence. V1 does not
permit an LLM assertion to override a failed deterministic gate.

### 5.1 Observation and Diagnosis

The Agent MAY observe frozen configuration, stage ledgers, tool logs, signoff reports,
artifact manifests, graph-verifier results, and prior run evidence.

Structured terminal evidence is authoritative over intermediate log text. Environment
or toolchain failures MUST be classified separately from RTL/design failures and MUST
NOT generate design-repair Recipes.

### 5.2 Recipe Use

Only a lifecycle-eligible `promoted` Recipe with an exact supported evidence domain MAY
affect live automatic execution. Candidate, shadow, demoted, parked, stale, unreadable,
or provenance-incomplete Recipes MUST fail closed.

Every Recipe MUST have:

- a stable Recipe key and version;
- a normalized effect fingerprint;
- an explicit applicability domain;
- positive and negative evidence;
- a current lifecycle state;
- a bounded retry/no-improvement policy.

Equivalent effects MUST NOT evade negative evidence by changing strategy names.

### 5.3 A/B Validation and Promotion

Every decisive A/B trial MUST be joined to one durable, immutable trial plan containing:

- trial UUID and full Recipe key/version/effect hash;
- subject, design, platform, toolchain snapshot, and baseline generation;
- expected arm paths, roles, run IDs, and objective constraints;
- the complete intended configuration delta.

A and B MUST differ only by the target Recipe effect. Clock target, die/core area,
signoff checks, toolchain, source inputs, and all other objectives MUST remain invariant.
A Recipe cannot win by weakening the task.

The verdict MUST prioritize usable terminal state and global signoff over an intermediate
score. Fixing one symptom while introducing an equal or more severe regression is not a
win. Promotion requires verified evidence from at least two independent subjects under
the same supported evidence domain. Legacy or imported evidence MAY form a candidate
hypothesis but cannot independently promote it.

Trial recording and lifecycle transition MUST be idempotent and recoverable after
interruption. Late evidence for an obsolete Recipe version or ineligible lifecycle state
MUST be retained for audit but MUST NOT change live status.

### 5.4 Bounded Autonomy

All repair and retry loops MUST have recorded iteration, wall-time, and no-improvement
limits. The frozen campaign configuration supplies the exact limits; no loop may be
unbounded. Repeated states, no material progress, or exhausted limits produce
`needs_human` with a structured reason.

V1 does not require the Agent to repair every EDA failure. Correct rejection and honest
escalation are successful Agent outcomes when the bounded action space is exhausted.

## 6. Legacy and Migration Policy

Historical candidates or evidence lacking any required source manifest, Recipe
provenance, run binding, graph schema version, or artifact digest MUST NOT automatically:

- promote a candidate or Recipe;
- count as new verified learning evidence;
- enter the V1 clean dataset index.

Legacy data MAY be retained as `legacy_unverified`, re-expanded/rebuilt under V1, or
processed through an explicitly invoked migration tool. Any operator override MUST be
separately authenticated, journaled, and propagated to downstream manifests. V1 does
not require automatic migration of the full historical corpus.

## 7. Explicitly Out of Scope for V1

The following do not block V1 closure unless they violate an in-scope safety invariant:

- official support for platforms other than nangate45;
- unrestricted cross-platform Recipe transfer;
- complete support for every SystemVerilog, VHDL, analog, or proprietary-IP design;
- guaranteed physical-design success for every discovered RTL repository;
- global PPA-optimal design-space exploration;
- automatic repair of every legacy corpus record;
- unlimited autonomous retries;
- scale or throughput claims beyond the frozen validation matrix;
- operator-dashboard presentation and non-authoritative journal completeness.

These items belong to a versioned V1.1/V2 backlog, not an expanding V1 gate.

## 8. Fixed Validation Matrix

The matrix is executed from clean workspaces. Source repositories are pinned by commit
and cached by archive digest so external network changes cannot alter release results.
Live internet discovery is reported as a canary, not used as a deterministic release
oracle.

### 8.1 Positive Controls

The initial V1 matrix contains these known real-design classes:

1. `picorv32_core`: processor/control logic and full graph verification.
2. `wbuart32_rtl_axiluart`: multi-file sequential RTL and clocked bus logic.
3. `ethmac_rtl_verilog_eth_registers`: multi-file RTL with include/clock dependencies.

Before `Frozen`, each control MUST be assigned an immutable source commit, archive
digest, top module, clock constraint, and expected terminal state in a checked-in
machine-readable test matrix. Replacing a control after freeze is a contract revision.

Each positive control MUST run without manual intervention after toolchain provisioning
and end as `published`, `r2g_clean`, with all five graph variants verifier-clean.

### 8.2 Mandatory Negative Controls

The V1 matrix MUST prove fail-closed behavior for at least:

1. an incomplete source manifest;
2. a legacy candidate with no source manifest;
3. explicit ORFS failure paired with stale clean project reports;
4. a DEF paired with clean reports from another backend run;
5. changed DEF content with preserved size and mtime;
6. a required label extractor failure with stale prior CSVs present;
7. graph-build failure after the first variant is written;
8. A/B arms that exist but belong to another full Recipe key;
9. legacy decisive A/B evidence without verifiable arm ownership;
10. an unsupported graph schema or schema-less legacy graph.

Every negative control MUST demonstrate that the relevant learner, promotion gate, or
publication gate rejects the input. Merely logging a warning is not a pass.

### 8.3 Automated Suites

The following suites are run separately to avoid Python test-package collisions:

```text
r2g-skills/signoff-loop/tests
r2g-skills/rtl-acquire/tests
r2g-skills/def-graph/tests
r2g-skills/eda-install/tests
```

All applicable tests MUST pass. Skips are allowed only when documented as outside the
frozen V1 environment; a skipped V1-required dependency is a gate failure.

### 8.4 P0 Trust Tests

Every P0 defect listed in
`docs/superpowers/plans/2026-07-19-post-consolidation-agent-and-full-pipeline-audit.md`
MUST have a deterministic regression. V1 closure requires all 14 P0 regressions to
pass on the same repository commit and toolchain snapshot. A legacy P0 may be closed by
correct fail-closed behavior; full automatic migration is not required.

P1 findings MAY be deferred only when they cannot violate Sections 3-5. Each deferral
requires an owner, rationale, observable risk, and target version.

## 9. Quantitative Quality Gates

A release-gate execution passes only when all of the following hold:

- automated suites: zero failures in all applicable tests;
- positive controls: `3/3` reach `published` and `r2g_clean`;
- negative controls: `10/10` are rejected by the intended gate;
- P0 trust regressions: `14/14` pass;
- graph variants: `5/5` exist and pass every schema-applicable verifier check for each
  positive control;
- provenance completeness: `100%` of published generations contain every required
  identity and digest;
- terminal-state completeness: `100%` of matrix candidates have exactly one terminal
  state;
- false publication count: `0`;
- false Recipe promotion count: `0`;
- mixed-generation or stale-artifact count: `0`;
- unbounded-loop count: `0`.

Acquisition yield and repair success rate MUST be reported, but are not required to be
100%. A correctly classified rejection is preferable to false success.

## 10. Required Evidence Package

Each gate execution MUST archive:

- repository and toolchain snapshots;
- machine-readable validation matrix and campaign configuration;
- source and compilation manifests;
- per-stage ledgers, logs, resource usage, and terminal states;
- signoff and artifact-provenance manifests;
- graph-generation manifests and file digests;
- verifier output;
- isolated knowledge-store snapshot before and after the campaign;
- a summary mapping every gate to pass/fail evidence.

The production knowledge store MUST NOT be used as mutable test state. Tests operate on
isolated snapshots or fresh stores.

## 11. Definition of V1 Convergence

V1 is converged only when:

1. this contract is `Frozen` and the machine-readable matrix and toolchain snapshot are
   committed or content-addressably archived;
2. all gates in Section 9 pass on one repository commit;
3. the complete gate passes twice consecutively from clean workspaces without changing
   code, contract, fixtures, or expected results between runs;
4. no open in-scope P0 defect remains;
5. all deferred P1 and out-of-scope findings are recorded in a versioned backlog;
6. the evidence packages are independently reviewable;
7. the release commit is tagged as the V1 baseline.

Convergence means the declared V1 contract is satisfied. It does not mean that future
research, broader platforms, or adversarial testing cannot discover additional work.

## 12. Change Control and Debug Stop Rule

While this document is `Proposed`, the owner and reviewer may refine scope, fixtures,
and thresholds. Changing status to `Frozen` records their approval and the associated
matrix/toolchain digests.

After freeze, every new finding is triaged as follows:

```text
Does it violate an in-scope invariant or permit false learning/publication?
  yes -> V1 blocker; add one minimal regression and repair it
  no  -> record in V1.1/V2 backlog; do not expand the V1 gate

Does it reveal that the contract itself is scientifically invalid?
  yes -> revise the contract version, record the rationale, and restart the gate
  no  -> keep the frozen boundary unchanged
```

No exploratory test becomes a release requirement merely because it found an edge
case. The release gate changes only through a reviewed contract revision.

Once Section 11 passes, open-ended V1 debugging stops. Further exploratory auditing is
performed against the next version and cannot retroactively prevent the V1 result unless
it demonstrates corruption within the frozen V1 boundary.

## 13. Freeze Record

Complete this record before changing `Status` to `Frozen`:

```text
Contract version:
Repository commit:
Toolchain snapshot digest:
Validation-matrix digest:
Graph schema version:
Campaign configuration digest:
Owner:
Independent reviewer:
Freeze date:
Approved P1 deferrals:
```
