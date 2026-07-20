# R2G Agent V1 Assurance Specification and Independent Validation Protocol

- Status: **Proposed**
- Document version: **0.1**
- Date: **2026-07-19**
- Initial repository reference: `136bb7dd19f9f0a62351fae40b322467ab1a8d35`
- Official V1 platform: **nangate45**

## Document Control

This document combines two logically separate normative parts:

- **Part I, System and Quality Specification**, defines what R2G Agent V1 is
  required to do and the boundary within which those claims apply.
- **Part II, Independent Validation Protocol**, defines how objective evidence
  is collected to determine whether an implementation satisfies Part I.

The two parts are combined for review convenience, not because requirements and
tests are interchangeable. Validation cases are derived from requirement IDs.
Historical defects do not define requirements and are not listed here. They belong
in a separate regression catalog and may be mapped to an existing requirement after
the fact.

The terms **MUST**, **MUST NOT**, **SHALL**, **SHALL NOT**, **SHOULD**, and **MAY**
are normative. Text marked **Rationale** or **Note** is explanatory.

This document is editable while its status is `Proposed`. After approval, its
status becomes `Frozen`. Post-freeze changes require versioned change control as
defined in Section 18.

---

# Part I: System and Quality Specification

## 1. Purpose and Intended Claim

R2G Agent V1 is an agentic, evidence-managed data-production system that:

```text
provisions a supported open-source EDA environment
-> discovers and qualifies open-source RTL
-> performs synthesis-only screening and deduplication
-> promotes eligible designs into a physical-design flow
-> executes ORFS implementation and signoff
-> extracts physical features and labels
-> constructs the b/c/d/e/f graph views
-> independently verifies and publishes qualified graph generations
-> learns bounded repair policies from auditable run evidence
```

The V1 claim is not that every repository found on the internet will become a
graph, that the Agent can repair every EDA failure, or that the resulting layout is
foundry-certified. The V1 claim is that, inside a declared operating domain, the
system can continuously process previously uncurated RTL and produce graph datasets
whose source, implementation, signoff, labels, topology, and Agent decisions are
traceable and independently checkable.

## 2. Verification, Validation, and Success

For this document:

- **Verification** asks whether the implementation satisfies the requirements in
  Part I.
- **Validation** asks whether the verified system is fit for its intended purpose:
  producing trustworthy physical-design graph datasets from uncurated open-source
  RTL with bounded autonomous operation.
- **Agent success** includes correct publication, correct rejection, and correct
  escalation. It does not mean that every input reaches layout.
- **Data success** means that a graph generation is usable under its declared
  quality tier. File existence alone is not success.

## 3. V1 Operating Domain

### 3.1 Supported Environment

The official V1 release environment is a documented Linux host with sufficient
storage and compute resources for the pinned toolchain. The official physical-design
platform is `nangate45`.

The supported toolchain includes the versions of ORFS, OpenROAD, Yosys, KLayout,
the graph Python environment, and all other tools actually invoked by an official
run. The exact versions and platform-file digests are fixed by a toolchain snapshot
at validation freeze time.

Other platforms and tool versions MAY be used for research. Their results MUST be
identified as out-of-domain and MUST NOT silently contribute to V1 qualification or
V1 Recipe evidence.

### 3.2 Supported RTL Inputs

V1 supports newly acquired, publish-eligible digital RTL for which the system can
establish:

- an immutable repository revision or equivalent immutable source package;
- a recorded license classification;
- a resolvable compilation-input closure;
- one selected top module;
- supported preprocessing, elaboration, and synthesis settings;
- a valid clock definition for sequential physical-design runs;
- successful synthesis-only qualification on the official platform.

The supported input language boundary is the subset accepted by the pinned frontend
and explicitly recorded in the compilation manifest. Unsupported analog behavior,
encrypted IP, missing proprietary dependencies, unresolved generators, ambiguous
tops, or unsupported HDL constructs are valid rejection outcomes.

### 3.3 Official End-to-End Path

The V1 qualification path is:

```text
environment preflight
-> RTL acquisition
-> source freeze
-> synthesis-only qualification
-> deduplication and quality screening
-> promotion
-> ORFS synth/floorplan/place/CTS/route/finish
-> route/antenna/timing/DRC/LVS/RCX evaluation
-> feature and label extraction
-> b/c/d/e/f graph construction
-> independent graph and provenance verification
-> transactional publication
```

The implementation may internally resume stages or reuse verified artifacts, but
the final evidence MUST prove the same ordered dependency relationship.

### 3.4 Out of Scope

The following are not V1 guarantees:

- support for every RTL repository or hardware-description language;
- official qualification on platforms other than `nangate45`;
- unrestricted cross-platform Recipe transfer;
- foundry production signoff;
- optimal PPA or exhaustive design-space exploration;
- autonomous repair of every design, tool, or infrastructure failure;
- automatic migration of all historical corpus and knowledge records;
- unlimited retries or unattended operation after a safety boundary is reached;
- throughput or population-level reliability claims not supported by the frozen
  evaluation sample.

## 4. Quality Tiers and Terminal States

### 4.1 Graph Quality Tiers

Every graph generation SHALL have exactly one quality tier:

- **`r2g_clean`**: satisfies all official source, toolchain, physical-flow,
  signoff, extraction, graph, and provenance requirements.
- **`research_only`**: intentionally retained for analysis but has one or more
  explicit caveats, such as incomplete labels, non-clean timing, unsupported
  platform, incomplete signoff, or operator override.
- **`rejected`**: determined to be unfit for graph publication.

Only `r2g_clean` generations may enter the official V1 clean index. A warning,
override, or manually edited status SHALL NOT convert a `research_only` generation
into `r2g_clean`.

### 4.2 Candidate Terminal States

Every candidate admitted to an official campaign SHALL reach exactly one durable
terminal state:

```text
published_clean
retained_research_only
rejected
failed_with_reason
needs_human
```

An interrupted candidate MAY remain resumable while the campaign is active, but it
is not a successful terminal outcome and cannot contribute successful learning
evidence or published data.

## 5. Authority and Trust Boundaries

Deterministic tools and parsers execute EDA stages and compute structured facts. The
Agent observes these facts, chooses among bounded actions, schedules work, and manages
evidence. The following authority order applies:

```text
raw tool artifacts and independently recomputed facts
> validated structured reports
> Agent summaries and recommendations
> free-form log interpretation
```

An LLM assertion, a copied status file, or a historical success record cannot
override a failed deterministic safety gate.

The generator and verifier SHALL be separated enough that they do not rely on the
same success flag or the same derived value for a critical check. When practical,
the verifier SHALL re-parse raw artifacts or use an independent implementation.

## 6. Normative System Requirements

### 6.1 Environment and Configuration Requirements

**ENV-001: Observable provisioning.** The environment layer MUST detect required
tools and either provision and verify them or terminate with a structured, actionable
failure. A partial installation MUST NOT be reported as ready.

**ENV-002: Frozen toolchain identity.** Every official run MUST carry a toolchain
snapshot containing the R2G commit and dirty-state flag, ORFS and submodule revisions,
tool versions and executable paths, graph-package versions, platform and rule-deck
digests, relevant environment settings, operating-system identity, and resource
limits. The snapshot MUST have a stable digest.

**ENV-003: Shared environment resolution.** All flow components participating in
one official run MUST resolve the same authoritative toolchain. Conflicting or
incomplete environment resolutions MUST fail before expensive execution.

**ENV-004: Domain isolation.** Evidence produced under a different platform,
toolchain generation, or graph schema MUST NOT be silently pooled with official V1
evidence.

### 6.2 RTL Acquisition and Qualification Requirements

**ACQ-001: Traceable discovery.** Every acquired candidate MUST record its origin,
immutable revision or archive digest, discovery time, license classification, and
the policy decision that admitted or rejected it.

**ACQ-002: Complete compilation manifest.** Before successful qualification, the
candidate MUST have a normalized compilation manifest containing all RTL files,
transitive include headers, packages, generated HDL and generation provenance,
include search order, preprocessor definitions, top module, top parameters,
frontend/language selection, synthesis-affecting switches, relative paths, sizes,
and content digests.

**ACQ-003: Evidence-based screening.** Screening decisions MUST be reproducible from
versioned policy and recorded, semantically relevant evidence. A risk indicator may
trigger additional analysis, but it SHALL NOT become a rejection unless the frozen
policy defines and validates its relationship to an unsupported dependency or design
property.

**ACQ-004: Honest synth-only qualification.** Synthesis-only success requires a
completed pinned-frontend run, a non-empty mapped design, valid design statistics,
and all required pre-layout outputs. A skipped or failed graph conversion cannot be
recorded as complete qualification.

**ACQ-005: Deduplication integrity.** Exact or policy-defined equivalent RTL and
mapped-netlist identities MUST be detected before they are counted as independent
corpus designs. Near-duplicate decisions MUST retain their evidence and policy
version.

**ACQ-006: Retryable failure semantics.** A failure MUST carry a classified reason.
Failures caused by repaired environment, policy, or frontend conditions MUST be
retryable without falsifying or deleting the earlier attempt. Retry and replacement
behavior MUST be explicit and idempotent.

**ACQ-007: Promotion source integrity.** Automatic promotion requires successful
qualification under the supported domain and byte verification against the complete
compilation manifest. The promoted project MUST vendor or otherwise immutably bind
the complete compilation closure. Unverified legacy sources require requalification
or an explicit operator-only research path.

### 6.3 Physical Flow and Signoff Requirements

**FLOW-001: Configuration preservation.** Promotion MUST preserve the qualified
source closure, top module, parameters, frontend settings, platform, and clock intent.
The promoted configuration MUST pass deterministic readiness validation before ORFS.

**FLOW-002: Stage dependency integrity.** A downstream stage MUST consume artifacts
from the intended successful upstream stage and generation. Missing, stale,
contradictory, or unbound prerequisites MUST block the downstream clean verdict.

**FLOW-003: Full-flow completion.** An official physical run is complete only when
the required ORFS synthesis, floorplan, placement, CTS, routing, and finish stages
reach recorded successful terminal states and the required final artifacts exist.

**FLOW-004: Strict V1 clean signoff.** An `r2g_clean` run requires all of the
following under the frozen V1 checks and constraints:

- routing completed with zero authoritative routing violations;
- antenna evaluation completed with zero residual violations;
- DRC completed with zero accepted-rule violations;
- LVS completed and matched; `skipped` is not clean on official `nangate45`;
- timing constraints are valid and the frozen clean timing criterion is met;
- RCX completed and the required SPEF is present and parseable.

Results that intentionally relax this policy MAY be retained as `research_only` but
MUST NOT enter the clean index.

**FLOW-005: Same-run signoff provenance.** DEF, ODB, GDS, mapped netlist, SPEF,
constraints, stage ledger, and signoff reports MUST be bound to the same physical run
and generation through run identity and content digests. Matching design and platform
names alone are insufficient.

**FLOW-006: Bounded execution and recovery.** Every flow, repair, retry, and resume
loop MUST have declared iteration, wall-time, and no-improvement limits. Timeout,
interruption, repeated state, and partial completion MUST produce structured,
recoverable outcomes and MUST NOT fabricate success.

**FLOW-007: Constraint integrity.** The Agent MUST NOT obtain a clean verdict by
silently weakening the clock target, enlarging physical objectives outside an
approved policy, disabling checks, changing the rule deck, or otherwise making the
task easier. Any approved objective change starts a distinct flow variant and cannot
be treated as a repair of the original objective.

### 6.4 Graph Dataset Requirements

**DATA-001: Qualified input.** Official graph construction MUST consume only the
physical generation certified by FLOW-003 through FLOW-005. An operator override may
create `research_only` output but cannot satisfy this requirement.

**DATA-002: Feature and label completeness.** The official schema SHALL declare the
required feature and label families. For `r2g_clean`, every required extractor MUST
complete for the current generation, joins MUST be valid, and applicable values MUST
not be silently replaced by stale, empty, all-zero, or all-NaN columns. Structurally
inapplicable values MAY use schema-defined missing values.

**DATA-003: Five-view completeness.** One official publication unit MUST contain all
five graph views `b`, `c`, `d`, `e`, and `f` under one frozen schema version. A
pre-layout `netlist_graph.pt` MAY accompany them but is not a substitute for a
required physical view.

**DATA-004: Cross-view identity.** All views, features, labels, and manifests in one
generation MUST agree on design identity, platform, source generation, physical run,
graph ID, node/entity identifiers, and normalization policy.

**DATA-005: Independent semantic verification.** Before clean publication, an
independent verifier MUST check, as applicable, topology, node and edge counts,
folding relations, forward/reverse edge alignment, categorical vocabularies, feature
statistics, label joins, physical units, and selected values recomputed from raw DEF,
LEF, Liberty, SPEF, ODB, constraints, and signoff evidence.

**DATA-006: Complete manifest.** Each generation MUST include a machine-readable
manifest containing at least schema version, generation ID, design identity,
platform, flow variant, source and compilation-manifest digests, toolchain snapshot,
backend-run identity, constraints digest, artifact digests, extractor generations,
graph kind and view declarations, node/edge counts, signoff verdict, verifier verdict,
quality tier, publication state, and timestamps.

**DATA-007: Transactional publication.** Construction and verification MUST occur in
a staging generation. The clean index and active-generation pointer may change only
after all required checks pass. A failed publication attempt MUST leave the previous
active generation byte-identical and addressable.

**DATA-008: Consumer usability.** Published files MUST load through the declared
PyTorch/PyG interface and pass schema-level consumer checks. The release MUST include
dataset documentation covering motivation, composition, source and license policy,
generation procedure, quality tiers, intended use, and known limitations.

### 6.5 Agent, Memory, Recipe, and A/B Requirements

**AGENT-001: Structured observation.** Agent decisions MUST cite the current
configuration, stage ledger, raw or validated reports, artifact manifests, graph
verification results, and relevant knowledge evidence. Structured terminal evidence
is authoritative over free-form log interpretation.

**AGENT-002: Bounded action space.** Automatic actions MUST come from a declared,
versioned action catalog with safety clamps. The Agent may schedule, retry, apply a
qualified repair, stop, or escalate, but it cannot override deterministic publication
or signoff gates.

**AGENT-003: Failure-domain separation.** Environment/toolchain, source/frontend,
physical-design, signoff, extraction, data-integrity, and Agent-control failures MUST
be distinguishable. Evidence from one domain MUST NOT be learned as a repair rule for
another domain without explicit, validated mapping.

**AGENT-004: Recipe identity and lifecycle.** Every Recipe MUST have a stable key,
version, normalized effect fingerprint, applicability domain, evidence provenance,
positive and negative evidence, lifecycle state, and bounded retry policy. Candidate,
parked, shadow, demoted, stale, unreadable, or provenance-incomplete Recipes MUST NOT
affect live automatic execution.

**AGENT-005: Evidence scope.** Live Recipe application requires the supported
platform and an applicable check/symptom/design domain. Pooled or transferred evidence
may nominate a candidate hypothesis, but it cannot alone grant live authority in an
unsupported domain. Equivalent effects cannot evade negative evidence by changing
names.

**AGENT-006: Controlled A/B design.** Every decisive Recipe experiment MUST use a
durable trial plan that binds the trial, Recipe version and effect, subject, baseline,
arm roles, run identities, toolchain, source, platform, objective constraints, and
intended configuration delta. A and B MUST differ only by the target Recipe effect.

**AGENT-007: Global outcome judgment.** A Recipe cannot win solely because an
intermediate score improves or the target symptom disappears. The judge MUST consider
terminal usability, signoff, new regressions, constraint integrity, and execution
cost. A repair that introduces an equal or more severe problem is not a win.

**AGENT-008: Promotion evidence.** Promotion requires complete A/B provenance and
verified positive evidence from at least two independent subjects in the supported
domain, with no disqualifying safety regression. Imported, backfilled, duplicate,
benchmark, or self-generated evaluation evidence cannot independently satisfy this
requirement.

**AGENT-009: Learning integrity.** Ingest, aggregation, trial judgment, and lifecycle
transition MUST be idempotent and recoverable. Repeated ingestion or interruption
MUST NOT duplicate evidence or leave a partially committed promotion. Held-out
evaluation data and A/B validation evidence MUST not be counted again as ordinary
independent learning evidence.

**AGENT-010: Safe non-convergence.** The Agent MUST detect bounded no-improvement,
repeated-state, and strategy-cycle conditions. Exhaustion produces `needs_human` or an
equivalent structured escalation, with the attempted actions and evidence preserved.

**AGENT-011: Safety monotonicity.** Learning may change diagnosis, prioritization, or
repair selection, but it MUST NOT weaken source integrity, objective constraints,
signoff, provenance, graph verification, or publication requirements.

### 6.6 Operational and Evidence Requirements

**OPS-001: Complete run ledger.** Every admitted candidate and every Agent action MUST
have a durable identity, state transition history, start/end time, outcome, and reason.

**OPS-002: Isolated evaluation state.** Formal validation MUST use an isolated,
versioned knowledge-store snapshot. Production knowledge, development experiments,
regression fixtures, and held-out evaluation state MUST remain distinguishable.

**OPS-003: Evidence preservation.** Each formal run MUST preserve the source,
configuration, toolchain, stage, signoff, graph, Agent-trajectory, knowledge, resource,
and final-verdict evidence required for independent review.

**OPS-004: Reproducibility semantics.** The release SHALL declare which outputs are
expected to be byte-identical and which are evaluated by semantic equivalence or a
frozen numerical tolerance. Nondeterminism MUST be measured rather than hidden.

**OPS-005: Resource accountability.** Wall time, CPU allocation, peak memory where
available, retries, Agent actions, and manual interventions MUST be recorded. Resource
limits are part of the test condition, not an unreported implementation detail.

**OPS-006: Campaign continuity.** A terminal failure or escalation for one candidate
MUST NOT corrupt, reset, or indefinitely starve unrelated candidates. A campaign MUST
be resumable from durable state and continue until its admitted work is terminal, its
frozen resource budget is exhausted, or a declared campaign-level safety condition
requires human action.

## 7. V1 Publication Manifest Minimum

The exact schema is frozen separately, but an official generation manifest MUST be
sufficient to answer these questions without consulting mutable workspace state:

1. Which immutable RTL bytes and compilation settings produced this design?
2. Which toolchain and platform generation executed it?
3. Which physical run produced the DEF, ODB, GDS, netlist, and SPEF?
4. Which signoff checks were run, under which constraints and rule decks, and what
   were their authoritative outcomes?
5. Which extractor generation produced each feature and label family?
6. Which graph schema and transformation produced each b/c/d/e/f view?
7. Which independent checks passed or failed?
8. Why and when did the generation enter its current quality tier and index?

## 8. V1 System Invariants

The following are zero-tolerance safety invariants inside the V1 boundary:

1. No unverified source is automatically promoted.
2. No failed or incomplete flow is reported as physically clean.
3. No stale or cross-run artifact certifies a different generation.
4. No missing required signoff evidence becomes a clean pass.
5. No incomplete or semantically unverified graph enters the clean index.
6. No ineligible Recipe affects live automatic execution.
7. No uncontrolled A/B experiment produces promotion evidence.
8. No constraint relaxation is misreported as repair success.
9. No duplicated, leaked, or invalid evidence inflates learning confidence.
10. No autonomous loop continues beyond its declared bounds.

These invariants define risk boundaries, not a catalog of previously observed bugs.

---

# Part II: Independent Validation Protocol

## 9. Validation Objective

The protocol produces objective evidence for two distinct questions:

1. **Conformance:** Does the tested release satisfy every applicable requirement in
   Part I under the frozen V1 operating domain?
2. **Fitness for purpose:** Does it process a representative set of uncurated RTL
   into trustworthy graph datasets with useful autonomy, bounded failure behavior,
   and reproducible evidence?

A release may conform to a narrow boundary while lacking evidence for a broader
research claim. Reports SHALL state this distinction explicitly.

## 10. Protocol Independence

### 10.1 Requirements-Derived Tests

Every mandatory validation case SHALL trace to one or more requirement IDs in Part I.
Every mandatory requirement SHALL trace to at least one verification or validation
case. The trace is bidirectional.

Historical defect IDs, issue-report counts, and previous pass/fail results SHALL NOT
be used to define the normative matrix. Historical regression tests are executed
separately. If a regression exposes a Part I violation, the release fails through
that requirement, not because the historical catalog has normative authority.

### 10.2 Evaluator Independence

At minimum, formal validation SHALL provide technical independence through all of
the following:

- the evaluation harness, fixtures, expected outcomes, and acceptance thresholds are
  frozen before execution;
- the Agent cannot modify the evaluator or hidden expected outcomes;
- critical verdicts are recomputed from raw artifacts rather than copied from Agent
  summaries;
- held-out inputs are excluded from development and promotion evidence before scoring;
- validation uses an isolated knowledge store and fresh workspace;
- an identified reviewer other than the implementation author reviews the protocol,
  frozen matrix, and final evidence package.

Where resources permit, a second operator or machine SHALL perform an independent
reproduction subset.

### 10.3 Pre-Registration and Freeze

Before a formal campaign begins, the following SHALL be immutable or content
addressed:

- Part I and Part II versions;
- repository commit and dirty-state policy;
- toolchain and platform snapshot;
- graph schema and required label set;
- input fixtures and held-out sampling rule;
- test-case definitions and expected outcomes;
- Agent model, scaffold, prompt/policy version, temperature, seed policy, and tool
  permissions, if an LLM participates;
- resource limits and timeout policy;
- metrics, denominators, confidence method, exclusion rules, and acceptance
  thresholds;
- evaluator commit and evidence-output schema.

Changing one of these after outcomes are visible creates a new validation generation.
The earlier report remains immutable.

## 11. Verification Methods and Oracles

Each requirement SHALL be assigned one or more of the following methods:

- **Inspection:** review a frozen artifact, schema, policy, configuration, or source
  property without executing the full system.
- **Analysis:** independently derive or recompute a value from raw evidence.
- **Demonstration:** exercise an observable workflow with a representative input.
- **Test:** apply controlled inputs and compare behavior against a predefined oracle.

The strongest practical oracle SHALL be selected in this order:

1. exact deterministic expected result;
2. independent recomputation from raw source artifacts;
3. differential comparison with an independent parser or tool;
4. metamorphic relation between controlled executions;
5. blinded domain-expert adjudication using a frozen rubric.

An LLM judge MAY assist with non-safety qualitative analysis, but it SHALL NOT be the
sole oracle for source integrity, signoff, graph correctness, Recipe promotion, or
publication eligibility.

## 12. Evaluation Populations and Data Separation

### 12.1 Four Evaluation Populations

The protocol distinguishes four populations:

- **Conformance fixtures:** pinned designs with known eligibility and expected
  outcomes, used to establish deterministic end-to-end behavior.
- **Held-out validation corpus:** real RTL projects not used to write policies,
  develop Recipes, tune thresholds, or choose fixes before evaluation.
- **Off-nominal controls:** synthetic or minimally modified inputs designed to test
  fail-closed and recovery behavior.
- **Live-internet canary:** current discovery runs used to measure operational drift.
  Because internet contents change, canaries are reported but are not deterministic
  release oracles.

The historical regression corpus is separate from all four.

### 12.2 Stratification

The held-out and conformance sets SHALL document coverage across the applicable
dimensions below:

- RTL scale and mapped-cell scale;
- single-file versus multi-file compilation closure;
- include/package/preprocessor complexity;
- combinational versus sequential logic;
- one-clock versus supported clock-edge cases;
- ordinary logic versus synthesizable memory-risk patterns;
- clear versus intentionally ambiguous top selection;
- expected acceptance, rejection, failure, and escalation outcomes;
- failure stage and failure domain;
- graph size and topology distribution.

The frozen matrix SHALL report coverage, not merely the number of designs. At least
pairwise coverage of feasible categorical combinations SHOULD be achieved. Targeted
three-way coverage SHALL be used for high-risk interactions involving source
provenance, signoff state, graph publication, Recipe lifecycle, and interruption.

### 12.3 Sample Size and Claim Discipline

Known positive fixtures prove conformance, not population reliability. Repeating one
design measures run stability; adding independent designs measures generalization.
These quantities SHALL NOT be conflated.

For a binary failure event with zero failures in `n` independent trials, the one-sided
95% upper confidence bound is:

```text
p_upper = 1 - 0.05^(1/n)
```

This is approximately `3/n` for moderate `n`. Therefore, three clean examples are a
smoke demonstration, not evidence of a low population failure rate. Any broad paper
claim SHOULD use at least 30 independent held-out designs or provide a different
pre-registered power/confidence justification. If compute limits require a smaller
sample, the claim SHALL be narrowed and the confidence interval reported.

## 13. Validation Layers

### 13.1 Layer A: Static Conformance

Inspection and lightweight analysis SHALL verify:

- requirement and test traceability completeness;
- supported-platform and toolchain declarations;
- schema and manifest definitions;
- policy and lifecycle state definitions;
- retry, timeout, and terminal-state declarations;
- absence of required-test skips in the frozen environment.

### 13.2 Layer B: Component and Interface Contracts

Deterministic tests SHALL exercise the boundaries between:

- environment detection and flow execution;
- candidate records and compilation manifests;
- synthesis qualification and promotion;
- promotion and ORFS configuration;
- physical runs and signoff reports;
- signoff evidence and graph construction;
- feature/label extraction and graph assembly;
- Agent observations, knowledge ingestion, Recipe ranking, A/B judgment, and
  lifecycle transition;
- staging generations and the published corpus index.

The objective is not code coverage alone. Each boundary SHALL have a success case,
a missing-evidence case, and a contradictory-evidence case when applicable.

### 13.3 Layer C: End-to-End Positive Conformance

The frozen positive set SHALL include real designs spanning the declared input strata.
Each design SHALL begin from a clean workspace and immutable source package and SHALL
execute the official path without unrecorded manual intervention.

A positive-control pass requires:

- the expected candidate qualification outcome;
- complete source and toolchain identity;
- successful promotion and full physical flow;
- strict V1 clean signoff;
- complete current-generation features and labels;
- all five graph views under the frozen schema;
- independent verifier pass;
- transactional entry in the clean index;
- a complete evidence bundle.

### 13.4 Layer D: Off-Nominal and Fault-Injection Validation

Controlled faults SHALL be injected at every major boundary, including at least:

- incomplete or changed compilation inputs;
- invalid or conflicting toolchain resolution;
- synthesis failure and timeout;
- interrupted promotion or stage transition;
- incomplete ORFS stage ledger;
- missing, malformed, stale, contradictory, or cross-run signoff evidence;
- graph extractor or graph-view construction failure;
- stale feature/label outputs from another generation;
- unavailable or unreadable Recipe lifecycle evidence;
- duplicate ingest, interrupted lifecycle transition, and invalid numerical metrics;
- exhausted retry budget and repeated Agent state.

Every fault case has a predeclared expected terminal state and blocking gate. The test
passes only when the system rejects, retains as research-only, retries within bounds,
or escalates exactly as specified. Merely emitting a warning is insufficient when the
corresponding requirement is fail-closed.

### 13.5 Layer E: Metamorphic Validation

The protocol SHALL include relations for cases where a complete output oracle is too
expensive or brittle. At minimum, applicable tests SHALL establish that:

1. relocating an unchanged source package does not change elaborated design semantics;
2. changing only filesystem timestamps does not certify stale artifacts or change
   semantic output;
3. semantically irrelevant source comments do not change the mapped design or graph
   semantics, although the source identity may change;
4. changing a compilation input invalidates dependent synthesis, physical, label,
   graph, and publication generations;
5. changing constraints or platform creates a distinct flow identity;
6. reordering semantically order-independent manifest entries does not change the
   normalized manifest identity;
7. interruption followed by valid resume reaches an outcome equivalent to a clean
   execution under the declared reproducibility semantics;
8. repeated ingest does not change evidence counts;
9. renaming a Recipe without changing its normalized effect does not bypass negative
   evidence;
10. permuting evaluation execution order does not systematically alter verdicts.

### 13.6 Layer F: Agent Learning and Causal Evaluation

Agent evaluation SHALL compare matched conditions rather than report only the full
system's final score. The frozen study SHALL include, where applicable:

- **Deterministic baseline:** flow and static repair logic without learned ranking;
- **Agent without mutable memory:** diagnosis and action selection from frozen policy;
- **Agent with retrieval but without live promotion:** learned evidence can rank but
  cannot gain new authority during evaluation;
- **Full Agent:** memory, Recipe ranking, controlled A/B validation, and lifecycle
  transitions enabled.

The arms SHALL use the same input, source revision, platform, toolchain, initial
configuration, objectives, resource limits, and injected failure. Execution order
SHOULD be randomized within resource blocks. Stores and workspaces SHALL be isolated.

Primary Agent outcomes are:

- recovery to a valid terminal state without weakening a safety invariant;
- correct stop or escalation when recovery is unavailable;
- absence of false Recipe application and false promotion;
- improvement over the matched deterministic baseline on recovery or cost.

A paper claim that memory, Recipe learning, or A/B promotion improves the system
requires a paired ablation result with uncertainty, not only an anecdotal successful
repair.

### 13.7 Layer G: Dataset Consumer Validation

The release SHALL be validated from the viewpoint of a downstream user who did not
run the EDA flow. Tests SHALL include:

- loading every graph view using the declared environment;
- checking tensor shapes, data types, missing-value conventions, and relation schemas;
- independently sampling and recomputing physical features and labels;
- checking graph/design/platform identity across all files;
- running a minimal reference data-loader and training smoke test;
- reviewing the dataset documentation and limitations against the actual manifest.

The training smoke test proves consumability, not predictive quality. Any claim about
ML utility requires a separate model experiment and evaluation protocol.

## 14. Testing the Validation System

Passing tests are useful only if the tests fail when a protected invariant is broken.
The frozen protocol SHALL therefore include a targeted validator-mutation campaign.

Representative temporary mutations include:

- bypassing one source-digest comparison;
- accepting a missing required signoff report;
- accepting a non-clean lifecycle state for live Recipe use;
- omitting one A/B provenance check;
- allowing one failed graph variant to update the clean index;
- accepting a non-finite learning metric;
- disabling one generation-consistency comparison.

Each mutation SHALL be introduced only in an isolated test checkout or through a test
double. The relevant validation case MUST fail and identify the violated requirement.
A surviving critical mutation is a defect in the validation protocol and blocks
freeze or release.

This targeted campaign is risk-based; it is not a requirement to mutate every line of
the implementation.

## 15. Metrics and Statistical Analysis

### 15.1 Zero-Tolerance Metrics

The following counts MUST be zero in every formal conformance campaign:

- false clean publication;
- mixed-source, mixed-run, or mixed-generation clean publication;
- false Recipe promotion;
- live application of an ineligible Recipe;
- unbounded autonomous loop;
- required validation case silently skipped;
- evaluator modification or held-out leakage by the Agent.

### 15.2 Capability Metrics

The report SHALL provide clearly defined numerators and denominators for:

- discovery-to-admission yield;
- admission-to-synthesis qualification yield;
- synthesis-to-full-flow attempt yield;
- full-flow-to-`r2g_clean` yield;
- end-to-end clean graph yield;
- correct rejection and correct escalation rate;
- diagnosis accuracy by failure domain and stage;
- repair recovery rate;
- Recipe promotion precision and held-out transfer success;
- graph-verifier pass rate and blocked-publication rate;
- terminal-state completeness;
- wall time, CPU time where available, memory, retries, Agent actions, and manual
  interventions per clean graph.

Metrics SHALL be reported overall and by relevant stratum. Averages alone are
insufficient; reports SHALL include counts and uncertainty or distributional
summaries.

### 15.3 Repetition and Reliability

Deterministic stages SHALL be checked for declared repeatability. Stochastic Agent
conditions SHALL be run multiple times under the frozen seed policy. Reports SHALL
include ordinary success rate and a consistency measure such as `pass^k`, the
probability that all `k` repeated trials succeed, when the same task is expected to be
reliably repeatable.

Binary rates SHOULD use Wilson or exact binomial confidence intervals. Paired Agent
comparisons SHOULD use paired bootstrap intervals or another pre-registered paired
method. Runtime distributions SHOULD report median and an interval such as IQR in
addition to the mean. Best-of-N selection SHALL NOT replace the pre-registered
aggregation rule.

## 16. Outcome Classification and Evidence

### 16.1 Test Outcomes

Every validation case SHALL end as exactly one of:

```text
pass
fail
inconclusive
not_applicable
```

`inconclusive` is not a pass. `not_applicable` is allowed only when the frozen matrix
declares the condition outside the case's domain. A missing V1-required dependency or
evidence item is `fail`, not `not_applicable`.

### 16.2 Evidence Package

Each formal campaign SHALL produce a content-addressed evidence package containing:

- specification, protocol, matrix, evaluator, repository, and toolchain identities;
- immutable input packages and sampling records;
- initial and final knowledge-store snapshots;
- complete Agent trajectories and tool invocations;
- per-stage ledgers, configurations, logs, reports, and resource records;
- signoff inputs, raw outputs, parsed reports, and independent recomputation results;
- feature, label, graph, and publication manifests with file digests;
- individual test verdicts mapped to requirement IDs;
- metrics with denominators, uncertainty, exclusions, and deviations;
- final reviewer signoff and unresolved limitations.

The evidence package SHALL permit a reviewer to audit a verdict without trusting the
Agent's natural-language explanation.

## 17. Release Acceptance and Convergence

### 17.1 Mandatory Acceptance Conditions

R2G Agent V1 satisfies this protocol only when:

1. the specification, protocol, test matrix, toolchain snapshot, graph schema, and
   acceptance thresholds were frozen before the scored campaign;
2. every mandatory Part I requirement has bidirectional traceability to objective
   validation evidence;
3. all applicable deterministic component and interface suites pass with no required
   skips;
4. all frozen positive conformance fixtures reach their expected terminal states;
5. all specification-derived off-nominal controls are blocked or handled at their
   intended gate;
6. all zero-tolerance metrics in Section 15.1 are zero;
7. all critical validator mutations are detected;
8. held-out capability results satisfy only the claims and thresholds explicitly
   frozen for V1;
9. the complete conformance campaign passes twice consecutively from clean workspaces
   without changes to code, fixtures, expected results, policy, or toolchain;
10. an independent reviewer accepts the traceability matrix and evidence package;
11. the release commit and protocol generation are tagged and archived.

The two repeated campaigns measure execution stability. They do not replace the need
for independent held-out designs when making generalization claims.

### 17.2 Claim-Specific Acceptance

The release report SHALL separate these conclusions:

- **Pipeline conformance:** the declared RTL-to-graph path and safety gates work on
  the frozen conformance matrix.
- **Autonomous corpus expansion:** the Agent achieves the frozen yield and terminal
  behavior on held-out RTL.
- **Evidence-driven self-repair:** the full Agent improves a pre-registered recovery
  or cost metric over matched baselines without degrading safety.
- **Dataset trustworthiness:** published graphs pass independent physical, semantic,
  provenance, and consumer checks.

Failure to support one research claim does not authorize changing its metric after
the run. The claim is narrowed or the implementation is improved and re-evaluated
under a new campaign.

## 18. Change Control and Stop Rule

### 18.1 Before Freeze

While `Proposed`, reviewers may change scope, requirements, fixtures, methods,
thresholds, and terminology. Decisions and rationale SHOULD be recorded in review
history.

### 18.2 After Freeze

Post-freeze changes are classified as follows:

- **Implementation fix:** changes code while preserving the requirement and protocol.
  Re-run the same frozen validation generation.
- **Historical regression addition:** records a newly discovered example of an
  existing requirement violation. It does not alter this document.
- **Protocol clarification:** removes ambiguity without changing the tested claim or
  acceptance outcome. Record an erratum and reviewer approval.
- **Protocol or specification change:** changes scope, requirement, oracle, fixture
  population, metric, or threshold. Increment the document version and begin a new
  validation generation.

### 18.3 Debug Convergence Rule

Exploratory testing MAY continue indefinitely, but it does not indefinitely expand
the frozen V1 release gate.

```text
Does the finding violate a frozen in-scope requirement?
  yes -> V1 implementation blocker; fix and re-run the same requirement-derived test
  no  -> record for a later version or research backlog

Does the finding prove that the frozen requirement or validation method is invalid?
  yes -> version the document and restart formal validation
  no  -> preserve the frozen protocol
```

Once Section 17 passes, V1 is converged within its declared domain. This means the
specified evidence threshold has been met. It does not mean that the system is free
of all possible defects or that later versions should stop improving.

## 19. Freeze Record

Complete every field before changing `Status` to `Frozen`:

```text
Document version:
Specification digest:
Validation-protocol digest:
Requirement-to-validation matrix digest:
Repository commit:
Evaluator commit:
Toolchain snapshot digest:
Platform/rule-deck digest:
Graph schema identifier:
Required feature/label set:
Strict signoff policy and accepted statuses:
Clean timing criterion:
Publication quality-tier policy:
Conformance-fixture manifest digest:
Fixture-role binding manifest digest:
Off-nominal mutation manifest digest:
Held-out sampling rule and corpus digest:
Agent model/scaffold/policy identity:
Seed and repetition policy:
Resource and timeout policy:
Claim-specific metrics and thresholds:
Approved exclusions:
Owner:
Independent reviewer:
Freeze date:
```

## 20. Normative Requirement-to-Validation Matrix

This section is the pre-specified validation matrix for Part I. It defines the
minimum official case associated with every requirement. The evaluator MAY split a
case into smaller executable tests, but it SHALL preserve the case ID, all mandatory
subcases, the stated oracle, and the acceptance rule.

A case passes only when every mandatory subcase passes. A missing case, missing
subcase, missing evidence item, or unexpected skip is a failure. Additional
exploratory tests do not change the scored matrix during a frozen campaign.

### 20.1 Frozen Fixture Roles

Concrete paths are deliberately not embedded in the protocol because paths are not
stable identities. Before freeze, each role below SHALL be bound to an immutable
source package or content-addressed generated fixture in the conformance-fixture
manifest. The binding records source revision, archive digest, expected top, clock,
platform, expected terminal state, and any permitted generation procedure.

| Fixture role | Required content |
| --- | --- |
| `FX-ENV-READY` | A supported clean Linux environment capable of executing the pinned V1 toolchain. |
| `FX-RTL-SIMPLE` | A small, single-clock, single-file digital design with an unambiguous top. |
| `FX-RTL-MULTI` | A real multi-file design with transitive includes, defines, or packages. |
| `FX-RTL-MEDIUM` | A medium-scale sequential design representative of an ordinary full physical flow. |
| `FX-RTL-LARGE` | A larger supported design used to exercise resource limits and campaign continuity. |
| `FX-RTL-RISK` | A synthesizable design containing policy risk indicators but no unsupported dependency. |
| `FX-RTL-REJECT` | A controlled unsupported or incomplete design whose correct outcome is rejection. |
| `FX-RUN-CLEAN` | A content-addressed, strict-signoff-clean physical run created from a frozen positive fixture. |
| `FX-GRAPH-CLEAN` | A verifier-clean b/c/d/e/f generation bound to `FX-RUN-CLEAN`. |
| `FX-AGENT-CASES` | Controlled failure episodes spanning the Agent failure domains and Recipe lifecycle states. |
| `FX-HOLDOUT` | The immutable held-out RTL sample selected by the frozen sampling rule. |

Off-nominal fixtures SHALL be derived from these baselines by a deterministic
mutation operation. Each mutation receives its own identity and records the baseline
digest, mutation description, changed bytes or fields, and expected blocking gate.
The Agent SHALL not receive hidden expected outcomes.

### 20.2 Case Record Schema

Every execution record SHALL contain:

```text
requirement_id:
test_id:
protocol_version:
evaluator_version:
method: inspection | analysis | demonstration | test
population: conformance | held_out | off_nominal | canary
fixture_id_and_digest:
initial_state:
controlled_action_or_fault:
oracle:
expected_outcome:
acceptance_threshold:
evidence_artifacts:
resource_limit:
repeat_and_seed_policy:
independence_controls:
actual_outcome:
verdict: pass | fail | inconclusive | not_applicable
reviewer:
```

### 20.3 Requirement-to-Case Index

The primary trace is one-to-one for readability. Parameterized subcases inside a
`VAL-*` entry test the positive, negative, and recovery behaviors of the same
requirement.

| Requirement | Official validation case | Requirement | Official validation case |
| --- | --- | --- | --- |
| `ENV-001` | `VAL-ENV-001` | `ENV-002` | `VAL-ENV-002` |
| `ENV-003` | `VAL-ENV-003` | `ENV-004` | `VAL-ENV-004` |
| `ACQ-001` | `VAL-ACQ-001` | `ACQ-002` | `VAL-ACQ-002` |
| `ACQ-003` | `VAL-ACQ-003` | `ACQ-004` | `VAL-ACQ-004` |
| `ACQ-005` | `VAL-ACQ-005` | `ACQ-006` | `VAL-ACQ-006` |
| `ACQ-007` | `VAL-ACQ-007` | `FLOW-001` | `VAL-FLOW-001` |
| `FLOW-002` | `VAL-FLOW-002` | `FLOW-003` | `VAL-FLOW-003` |
| `FLOW-004` | `VAL-FLOW-004` | `FLOW-005` | `VAL-FLOW-005` |
| `FLOW-006` | `VAL-FLOW-006` | `FLOW-007` | `VAL-FLOW-007` |
| `DATA-001` | `VAL-DATA-001` | `DATA-002` | `VAL-DATA-002` |
| `DATA-003` | `VAL-DATA-003` | `DATA-004` | `VAL-DATA-004` |
| `DATA-005` | `VAL-DATA-005` | `DATA-006` | `VAL-DATA-006` |
| `DATA-007` | `VAL-DATA-007` | `DATA-008` | `VAL-DATA-008` |
| `AGENT-001` | `VAL-AGENT-001` | `AGENT-002` | `VAL-AGENT-002` |
| `AGENT-003` | `VAL-AGENT-003` | `AGENT-004` | `VAL-AGENT-004` |
| `AGENT-005` | `VAL-AGENT-005` | `AGENT-006` | `VAL-AGENT-006` |
| `AGENT-007` | `VAL-AGENT-007` | `AGENT-008` | `VAL-AGENT-008` |
| `AGENT-009` | `VAL-AGENT-009` | `AGENT-010` | `VAL-AGENT-010` |
| `AGENT-011` | `VAL-AGENT-011` | `OPS-001` | `VAL-OPS-001` |
| `OPS-002` | `VAL-OPS-002` | `OPS-003` | `VAL-OPS-003` |
| `OPS-004` | `VAL-OPS-004` | `OPS-005` | `VAL-OPS-005` |
| `OPS-006` | `VAL-OPS-006` |  |  |

### 20.4 Environment Validation Cases

**VAL-ENV-001: Provisioning readiness and honest failure.** Method: demonstration
and off-nominal test. Run provisioning and preflight on `FX-ENV-READY`, then repeat
in an isolated copy with one required executable unavailable. Pass only if the clean
environment is verified ready and the faulty environment terminates not-ready with
the missing dependency and recovery action identified; no downstream flow may start.
Oracle: direct executable/version probes and process exit status. Evidence: detection
snapshot, install plan, verification report, exit codes, and downstream-start audit.

**VAL-ENV-002: Toolchain snapshot completeness and sensitivity.** Method: inspection
and analysis. Generate two snapshots without changing the environment, then change
one controlled tool, platform file, or rule-deck digest and generate a third. Pass
only if the first two normalized digests match, every ENV-002 field is present, and
the controlled change produces a distinct digest and validation generation. Oracle:
independent version probes and file hashing. Evidence: all snapshots and digest diff.

**VAL-ENV-003: Shared resolution and conflict handling.** Method: test. Resolve the
environment independently through acquisition, physical-flow, and graph components,
then inject a conflicting root or executable. Pass only if normal resolutions agree
on the authoritative snapshot and the conflict is reported before expensive work;
silently selecting different tools fails. Oracle: independently normalized path and
version comparison. Evidence: per-component resolution reports and conflict log.

**VAL-ENV-004: Domain isolation.** Method: off-nominal test. Present otherwise valid
evidence stamped with a different platform, toolchain generation, and graph schema to
the V1 learner and publication path, one dimension at a time. Pass only if each item
is excluded from official V1 aggregation and publication or explicitly retained as
out-of-domain research evidence. Oracle: direct query of the knowledge store and clean
index. Evidence: input stamps, queries, and rejection or tier records.

### 20.5 Acquisition Validation Cases

**VAL-ACQ-001: Traceable discovery.** Method: demonstration and test. Acquire the
pinned real source bound to `FX-RTL-SIMPLE`, then process `FX-RTL-REJECT` and a derived
candidate lacking an immutable revision or license decision. Pass only if the real
candidate records all ACQ-001 fields and each incomplete or unsupported candidate
cannot enter successful qualification.
Oracle: independent repository revision/archive hash and policy lookup. Evidence:
candidate ledger, source record, license decision, and terminal state.

**VAL-ACQ-002: Compilation closure completeness.** Method: analysis and off-nominal
test. Expand `FX-RTL-MULTI`, independently enumerate frontend-opened files and compile
settings, then remove or alter one transitive header, generated file, definition, or
top parameter. Pass only if the normalized manifest matches the independent closure
and every mutation invalidates qualification until re-expanded. Oracle: frontend file
trace plus independent hashing. Evidence: source manifests, traces, mutations, and
qualification verdicts.

**VAL-ACQ-003: Reproducible semantic screening.** Method: metamorphic test. Apply the
same frozen policy twice to unchanged fixtures, then add semantically irrelevant text
that resembles a risk indicator to a supported design and use `FX-RTL-RISK` as the
positive risk case. Pass only if repeated decisions are identical, reasons cite the
policy version and relevant evidence, and risk indicators do not become unsupported
rejections without the policy-required semantic evidence. Oracle: policy replay and
synthesis/elaboration evidence. Evidence: decisions, risk records, and diffs.

**VAL-ACQ-004: Honest synth-only qualification.** Method: demonstration and fault
injection. Qualify a positive fixture, then repeat with the mapped design empty or the
required pre-layout graph conversion unavailable. Pass only if the positive run has
the completed frontend result, non-empty mapped design, statistics, and required graph,
while each faulty run remains non-success with a structured reason. Oracle: independent
netlist statistics and graph load. Evidence: stage ledger, netlist, statistics, graph,
and index state.

**VAL-ACQ-005: Duplicate and near-duplicate handling.** Method: test and analysis.
Submit byte-identical and mapped-netlist-equivalent copies under different names,
plus one policy-defined near-duplicate pair. Pass only if exact/equivalent copies are
not counted as independent designs and the near-duplicate decision is reproducible
from its recorded signature, threshold, and policy. Oracle: independent source and
netlist canonical hashes. Evidence: duplicate audit, signatures, and corpus counts.

**VAL-ACQ-006: Retryable and idempotent failures.** Method: recovery test. Cause a
candidate to fail through a controlled temporary environment or frontend condition,
restore the condition, and retry twice. Pass only if the first failure remains
auditable, the restored run is allowed to reach its honest terminal state, and the
second retry does not duplicate index, run, or learning evidence. Oracle: ledger and
database natural-key queries. Evidence: attempt chain, terminal records, and count diff.

**VAL-ACQ-007: Promotion source integrity.** Method: positive and off-nominal test.
Promote an unchanged, fully qualified candidate; then separately modify one manifest
input and remove the source manifest to emulate an unverified record. Pass only if the
unchanged candidate is self-contained and promotion-ready, while changed and
unverified candidates cannot auto-promote and are directed to requalification or an
explicit research-only operator path. Oracle: independent byte closure comparison.
Evidence: vendored inputs, manifests, promotion records, and rejection reasons.

### 20.6 Physical Flow and Signoff Validation Cases

**VAL-FLOW-001: Promotion configuration preservation.** Method: analysis and test.
Compare a promoted project's source, top, parameters, frontend, platform, and clock
intent with the qualified manifest, then mutate each category in isolation. Pass only
if the unmodified project passes readiness and every unintended mismatch is detected
before ORFS. Oracle: independent normalized configuration comparison. Evidence:
qualification manifest, promoted configuration, readiness report, and mutation diff.

**VAL-FLOW-002: Stage dependency integrity.** Method: off-nominal test. Execute a valid
stage chain, then replace, remove, or stale one required upstream artifact while
leaving a downstream success marker. Pass only if the downstream clean verdict is
blocked for every mutation and identifies the broken dependency. Oracle: stage/run
identity and content-digest graph recomputed from raw artifacts. Evidence: stage
ledger, dependency manifest, mutation record, and verdict.

**VAL-FLOW-003: Full-flow completion.** Method: demonstration and test. Execute
`FX-RTL-SIMPLE`, `FX-RTL-MEDIUM`, and `FX-RTL-LARGE` through the positive full-flow
path, then create controlled copies in which each required ORFS stage is absent,
failed, or incomplete. Pass only if every expected-positive complete run is recognized
and every incomplete copy is non-clean regardless of final-file existence. Oracle:
independent stage-ledger interpretation and required-artifact inspection. Evidence:
raw stage records, final artifacts, and completion verdicts.

**VAL-FLOW-004: Strict V1 clean signoff.** Method: parameterized test. Start from
`FX-RUN-CLEAN`, then independently introduce one unacceptable condition for routing,
antenna, DRC, LVS, timing, and RCX/SPEF. Pass only if the baseline is `r2g_clean` and
all six mutated subcases are excluded from the clean tier; a missing or skipped V1
check is a failure, not a clean pass. Oracle: independent parsing or recomputation of
each raw check. Evidence: constraints, rule-deck IDs, raw/parsed reports, mutations,
and tier decisions.

**VAL-FLOW-005: Same-run provenance.** Method: off-nominal test. Validate the genuine
`FX-RUN-CLEAN` bundle, then pair its DEF/ODB/GDS with clean reports, netlist, or SPEF
from another content-addressed run of the same design and platform. Pass only if the
genuine bundle passes and every mixed bundle is rejected through run identity or
artifact digest. Oracle: independent provenance-graph reconstruction. Evidence: both
run manifests, file digests, mixed-bundle manifest, and gate verdict.

**VAL-FLOW-006: Bounded execution and recovery.** Method: fault-injection and recovery
test. Inject a stage timeout, an interruption between stages, repeated no-improvement,
and a valid resumable interruption. Pass only if every condition receives a structured
state, limits are honored, no false success is learned, no-improvement escalates, and
valid resume consumes only bound current-generation artifacts. Oracle: elapsed time,
iteration ledger, state-cycle analysis, and artifact identities. Evidence: campaign
configuration, ledgers, signals/exit codes, resume record, and terminal states.

**VAL-FLOW-007: Constraint integrity.** Method: controlled comparison. Apply a repair
that preserves objectives, then create variants that loosen the clock, enlarge the
area outside policy, disable a check, or change a rule deck. Pass only if the preserving
repair remains eligible for evaluation and every relaxed case receives a new objective
identity and cannot win or certify the original task. Oracle: normalized constraints,
physical objectives, and rule-deck digest diff. Evidence: baseline/variant manifests,
judge record, and quality tier.

### 20.7 Graph Dataset Validation Cases

**VAL-DATA-001: Qualified graph input.** Method: demonstration and off-nominal test.
Build from `FX-RUN-CLEAN`, then attempt the same build from a dirty or incomplete run
with each supported override mode. Pass only if the clean input is eligible for clean
publication and every override is visibly `research_only` or rejected. Oracle:
independent signoff/provenance evaluation. Evidence: input manifests, override record,
graph manifest, and index query.

**VAL-DATA-002: Feature and label completeness.** Method: parameterized fault test.
Verify the complete baseline, then remove, stale, empty, zero-fill, or all-NaN each
required applicable feature/label family in turn. Pass only if the baseline satisfies
the frozen schema and every silent-degradation mutation is detected and barred from
clean publication. Oracle: independent raw-artifact sampling, join-key checks, and
distribution checks. Evidence: extractor markers, CSV/tensor statistics, sampled
recomputations, and manifest health.

**VAL-DATA-003: Five-view completeness.** Method: test. Load and verify all five views
from `FX-GRAPH-CLEAN`, then remove or corrupt each view one at a time and inject a
failure after only a subset is written. Pass only if the complete generation passes
and no partial set enters the clean index. Oracle: independent file digests, schema
load, and required-view enumeration. Evidence: graph files, build ledger, staging
manifest, and publication verdict.

**VAL-DATA-004: Cross-view identity.** Method: off-nominal test. Compare all baseline
views and associated feature/label artifacts, then change design ID, graph ID,
platform, flow variant, entity identity, or generation in one member at a time. Pass
only if baseline identities agree and every mismatch is rejected. Oracle: independent
manifest and tensor-key comparison. Evidence: identity table, mutation records, and
verifier output.

**VAL-DATA-005: Independent semantic verification.** Method: mutation test and
analysis. Independently verify the baseline, then corrupt one topology relation, one
feature value/statistic, one categorical vocabulary entry, one label join, and one
physical unit/value. Pass only if the clean baseline passes and every critical
corruption is detected without trusting generator health flags. Oracle: separate
raw DEF/LEF/Liberty/SPEF/ODB parsing or tool recomputation. Evidence: raw artifacts,
mutation manifest, recomputed values, and named verifier checks.

**VAL-DATA-006: Manifest completeness and truthfulness.** Method: inspection and
parameterized test. Validate every required DATA-006 field and digest, then remove
each field class and alter representative artifact/verdict digests. Pass only if the
baseline is complete and every omission or contradiction blocks clean publication.
Oracle: schema validation plus independent file hashing. Evidence: manifests, schema
results, digest inventory, and gate decisions.

**VAL-DATA-007: Transactional publication.** Method: interruption test. Publish a
valid baseline, begin a new generation, and interrupt separately during extraction,
graph assembly, verification, active-pointer switch, and index update. Pass only if
no partial generation becomes active, the prior generation remains byte-identical,
and a subsequent idempotent recovery either commits exactly once or cleans staging.
Oracle: before/after hashes and atomic pointer/index inspection. Evidence: staging
ledger, active pointer, corpus index, recovery record, and file hashes.

**VAL-DATA-008: Consumer usability and documentation.** Method: independent
demonstration and inspection. In the frozen consumer environment, load every view,
check tensors and missing-value conventions, run the reference data-loader and minimal
training smoke test, and compare the dataset documentation with the actual manifest.
Pass only if all loads and schema checks succeed and documentation accurately states
composition, provenance, quality tiers, intended use, and limitations. Oracle:
consumer-side code and manifest inspection. Evidence: environment lock, loader/training
logs, schema report, and documentation checklist.

### 20.8 Agent and Learning Validation Cases

**VAL-AGENT-001: Structured evidence precedence.** Method: controlled contradiction
test. Present a run whose free-form log or summary claims success while its structured
terminal ledger or raw artifact proves failure, and the converse with harmless warning
text. Pass only if the Agent follows authoritative structured/raw evidence and cites
it in the decision. Oracle: frozen authority order in Section 5. Evidence: observation
bundle, Agent trajectory, selected verdict, and cited artifact IDs.

**VAL-AGENT-002: Bounded action authority.** Method: policy and off-nominal test. Run
one case for each allowed action class, then offer an action that bypasses signoff or
publication gates. Pass only if allowed actions are versioned and auditable and the
bypass is refused regardless of model recommendation. Oracle: action-catalog and gate
state comparison. Evidence: policy version, tool calls, refusal record, and final gate.

**VAL-AGENT-003: Failure-domain separation.** Method: parameterized classification
test. Inject labeled environment/toolchain, source/frontend, physical-flow, signoff,
extraction, data-integrity, and Agent-control failures from `FX-AGENT-CASES`. Pass only
if each reaches the correct domain and an environment/tool failure cannot create or
strengthen a design-repair Recipe. Oracle: hidden fixture labels and database query.
Evidence: raw failures, diagnosis events, Recipe diff, and confusion matrix.

**VAL-AGENT-004: Recipe lifecycle enforcement.** Method: state-machine test. Present
otherwise identical Recipes in candidate, parked, shadow, demoted, stale, unreadable,
provenance-incomplete, promoted-wrong-domain, and promoted-applicable states. Pass
only if exactly the applicable promoted Recipe can affect live execution and missing
safety state fails closed. Oracle: lifecycle table and selected action order. Evidence:
Recipe records, store-read status, ranking, and applied-action journal.

**VAL-AGENT-005: Evidence-scope and effect identity.** Method: controlled ranking
test. Compare a small exact-domain evidence set with a larger out-of-domain pooled set,
then create two differently named Recipes with the same normalized effect after one
has negative evidence. Pass only if pooled evidence cannot grant unsupported live
authority and the alias cannot evade the equivalent effect's negative evidence.
Oracle: independent domain keys and effect fingerprint. Evidence: evidence rows,
fingerprints, ranking, and eligibility verdicts.

**VAL-AGENT-006: Controlled A/B provenance.** Method: A/B integrity test. Execute one
valid pair whose arms share the frozen baseline and differ only by the target effect,
then create pairs with an extra configuration change, relaxed objective, wrong arm
run ID, different subject/platform, stale Recipe version, and identical no-op arms.
Pass only if the valid pair is judgeable and every confounded or non-divergent pair is
invalid, parked, or replanned before promotion. Oracle: independent full configuration,
objective, run-ownership, and Recipe-hash diff. Evidence: trial plans, arm manifests,
diffs, run records, and judge decisions.

**VAL-AGENT-007: Global outcome judgment.** Method: parameterized judgment test.
Evaluate a genuine globally improved B arm, a higher-score but non-clean B arm, a B
arm that clears the target symptom but introduces an equal or worse regression, and
a constraint-relaxed B arm. Pass only if the genuine improvement can win and every
unsafe or incomparable case cannot win. Oracle: frozen terminal-state and severity
ordering applied independently. Evidence: arm metrics, full signoff vectors, objective
diffs, and verdict rationale.

**VAL-AGENT-008: Promotion evidence sufficiency.** Method: lifecycle test. Supply
complete positive A/B evidence from two independent subjects, then separately use
duplicate runs of one subject, imported/backfilled rows, benchmark rows, self-scored
evaluation rows, and incomplete provenance. Pass only if the independent evidence can
satisfy the promotion rule and every substitute remains insufficient. Oracle: unique
subject/configuration-family count and evidence-origin query. Evidence: evidence rows,
promotion calculation, lifecycle transitions, and rejection reasons.

**VAL-AGENT-009: Idempotent learning and transaction recovery.** Method: stateful
fault test. Ingest the same run and fix session repeatedly, interrupt after A/B trial
write but before lifecycle update, restart reconciliation repeatedly, and expose A/B
arm results to the ordinary learner. Pass only if evidence counts remain stable, the
transition commits exactly once, and evaluation evidence is not double-counted as
independent run history. Oracle: natural-key database queries and transaction journal.
Evidence: before/after table snapshots, reconciliation logs, and confidence values.

**VAL-AGENT-010: Non-convergence termination.** Method: state-machine test. Create
no-improvement and A-B-A-B strategy-cycle episodes with limits below the test timeout.
Pass only if the Agent detects the repeated state within the frozen bounds, stops
automatic action, preserves attempted strategies and outcomes, and emits a structured
human escalation. Oracle: independent state/effect sequence analysis and elapsed
limit. Evidence: trajectory, state hashes, budgets, and escalation record.

**VAL-AGENT-011: Safety monotonicity under learning.** Method: matched comparison.
Run the same controlled cases and the pre-registered applicable subset of `FX-HOLDOUT`
with frozen/no-memory ranking and with learned ranking. Pass only if learning may
change eligible action order or improve recovery but cannot change the outcome of
source, objective, signoff, provenance, graph-verification, or publication gates.
Oracle: gate-vector equality plus action-order diff. Evidence: paired trajectories,
rankings, gate results, and final states.

### 20.9 Operational Validation Cases

**VAL-OPS-001: Complete terminal ledger.** Method: campaign test. Run a mixed batch
containing publishable, rejectable, failing, and escalation fixtures. Pass only if
every admitted candidate has one durable identity, complete state history, and exactly
one valid terminal state, with no orphan or contradictory terminal rows. Oracle:
independent ledger state-machine query. Evidence: campaign input, ledger, and terminal
summary.

**VAL-OPS-002: Evaluation-state isolation.** Method: contamination test. Seed distinct
markers in production, development, regression, and held-out knowledge stores and run
formal evaluation. Pass only if the evaluator reads the declared frozen snapshot,
writes only to its isolated destination, and no held-out outcome becomes pre-score
learning or promotion evidence. Oracle: before/after store hashes and marker queries.
Evidence: store identities, access log, queries, and Agent configuration.

**VAL-OPS-003: Evidence-package completeness.** Method: inspection and negative test.
Build a complete formal evidence package, validate it against Section 16.2, then remove
one required artifact class at a time. Pass only if the complete package is auditable
and every omission makes the corresponding verdict incomplete or failed rather than
silently acceptable. Oracle: independent package schema and digest inventory. Evidence:
package manifest, mutation list, and audit results.

**VAL-OPS-004: Declared reproducibility.** Method: repeated clean-workspace test. Run
the frozen conformance campaign twice and compare every declared byte-stable artifact
by digest and every semantic/numerical artifact by its frozen equivalence rule and
tolerance. Pass only if all differences are permitted and explained; undeclared
nondeterminism fails. Oracle: independent artifact classifier and comparator. Evidence:
both packages, digest/tolerance diff, seeds, and verdict.

**VAL-OPS-005: Resource accountability.** Method: inspection and boundary test. Run
representative small, medium, and large fixtures at frozen limits and force one limit
exhaustion. Pass only if allocations and observed resource metrics are recorded, the
exhausted case terminates honestly, and excluded or unavailable measurements are
explicit rather than fabricated. Oracle: scheduler/process measurements and run
records. Evidence: campaign limits, resource logs, exit state, and metric summary.

**VAL-OPS-006: Campaign continuity and resume.** Method: multi-candidate fault test.
Run a campaign in which one candidate fails, one hangs until its bound, and others can
complete; interrupt and restart the campaign once. Pass only if unrelated candidates
are not corrupted or indefinitely starved, completed work is not duplicated, resumable
work continues from durable state, and the campaign stops only for the declared work,
budget, or campaign-level safety condition. Oracle: independent scheduling/ledger
timeline and artifact identities. Evidence: queue snapshots, worker events, restart
record, terminal states, and duplicate audit.

### 20.10 Formal Execution Order

The official campaign SHALL execute in this order so that a broken evaluator or
environment cannot waste a full physical-flow campaign:

```text
1. freeze-integrity and evaluator self-check
2. VAL-ENV-* cases
3. lightweight component and interface cases
4. validator-mutation cases from Section 14
5. VAL-ACQ-* conformance and off-nominal cases
6. VAL-FLOW-* and VAL-DATA-* positive/off-nominal cases
7. VAL-AGENT-* controlled learning cases
8. VAL-OPS-* campaign, repeatability, and evidence cases
9. held-out capability characterization and Agent ablations
10. independent review and release verdict
```

Failure of a zero-tolerance case stops scored downstream execution unless continuing
is necessary to collect pre-registered diagnostic evidence. Such continuation is
marked diagnostic and cannot convert the failed campaign into a pass.

### 20.11 Matrix Completion Rule

Before freeze, the owner and independent reviewer SHALL confirm:

- all 43 Part I requirement IDs appear exactly once in the primary index;
- all 43 official `VAL-*` cases have executable procedures or evaluator entrypoints;
- every fixture role is bound to an immutable fixture manifest;
- every parameterized mutation and expected blocking gate is enumerated;
- all case-specific limits, repeats, seeds, and thresholds are fixed;
- the requirement index, fixture manifest, mutation manifest, and evaluator are
  content-addressed in the Freeze Record.

The case definitions above are normative. Translating them into code or a YAML runner
is implementation work and does not create another category of governance material.

## 21. Methodological References

This protocol is informed by the following public methods and standards guidance:

- NASA Systems Engineering Handbook, especially requirements management,
  verification methods, Requirements Verification Matrix, and Validation
  Requirements Matrix:
  <https://www.nasa.gov/wp-content/uploads/2018/09/nasa_systems_engineering_handbook_0.pdf>
- NASA Independent Verification and Validation overview:
  <https://www.nasa.gov/ivv-overview/>
- NIST AI Risk Management Framework, Measure function and TEVV guidance:
  <https://airc.nist.gov/airmf-resources/airmf/5-sec-core/>
- NIST Engineering Statistics Handbook, experimental design, randomization,
  replication, and blocking:
  <https://www.itl.nist.gov/div898/handbook/pri/section3/pri3.htm>
- NIST combinatorial methods for software assurance:
  <https://csrc.nist.gov/projects/automated-combinatorial-testing-for-software/>
- OSF registration and pre-registration guidance for freezing hypotheses, methods,
  and analysis plans before outcomes are observed:
  <https://help.osf.io/article/330-welcome-to-registrations>
- Metamorphic testing for systems with difficult test oracles:
  <https://arxiv.org/abs/2002.12543>
- AgentBoard, analytical evaluation of multi-turn Agent progress:
  <https://arxiv.org/abs/2401.13178>
- tau-bench, repeated-trial reliability for tool-using Agents:
  <https://arxiv.org/abs/2406.12045>
- ACM artifact review and reproducibility guidance:
  <https://www.acm.org/publications/artifacts>
- Datasheets for Datasets:
  <https://arxiv.org/abs/1803.09010>
