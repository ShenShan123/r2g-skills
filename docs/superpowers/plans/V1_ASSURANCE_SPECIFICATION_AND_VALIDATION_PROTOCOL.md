# R2G Agent V1 Execution Specification and Validation Protocol

- Status: **Proposed**
- Version: **0.9**
- Date: **2026-07-20**
- Repository reference: `95c809c` (v0.8 runner/registry relocation baseline; v0.9
  folds the round-2 pilot findings — strict platform capability GC-ENV-07,
  signoff evidence-bundle binding, six-stage lineage, strict data tier)
- Target platforms: `nangate45`, `sky130hd`, `sky130hs`
- Machine registry: `tools/v1_validation_registry.yaml`
- Registry runner: `tools/run_v1_validation_registry.py`
- Quality contract companion: `docs/superpowers/plans/V1_QUALITY_GATE_AND_CONVERGENCE_CRITERIA.md`

## 0. How to Use This Document

This is the execution contract for V1 debug and release validation. Read it in this
order:

1. identify the failing subskill or handoff gate;
2. locate the gate's `GC-*` conditions (Section 2) and the corresponding `REQ-*`
   requirement;
3. execute the gate's executable conditions (`gates` command), then its paired
   `VAL-*` case through the registry;
4. preserve the required evidence and fix the implementation, not the oracle;
5. rerun the same case and all historical regressions mapped to that requirement.

The English requirement text is normative. The registry defines executable case
parameters, evaluator readiness, the diagnostics inventory, and the machine binding
of every Section 2 gate condition (`gate_conditions`). The companion quality
contract defines convergence exit criteria but does not award `VAL-*` verdicts.
Historical bug reports are regression inputs, not V1 scope.

`MUST`, `MUST NOT`, `SHALL`, and `SHALL NOT` are mandatory. Missing, stale,
contradictory, unbound, or unreadable safety evidence fails closed.

## 1. V1 Boundary and Success

### 1.1 Product Claim

For each qualified target platform, V1 performs:

```text
environment qualification
-> RTL discovery and source freeze
-> platform synth-only and deduplication
-> promotion into full flow
-> clock qualification and Fmax-derived constraints
-> ORFS implementation and strict signoff
-> b/c/d/e/f graph construction and verification
-> transactional clean-dataset publication
-> bounded diagnosis, Recipe learning, and A/B promotion
```

V1 does not guarantee that every discovered RTL is accepted, every admitted design
completes, every failure is repaired, or every design works on all three platforms.
Correct rejection and `needs_human` escalation are valid Agent outcomes.

### 1.2 Platform Qualification

Each platform profile has exactly one state:

```text
candidate | qualified | suspended
```

All three profiles start as `candidate`. A platform becomes `qualified` only after
its complete frozen validation matrix passes and an independent reviewer approves
the evidence. A candidate, suspended, or non-target platform cannot publish
`r2g_clean` or provide authoritative cross-platform Recipe evidence.

### 1.3 Data Tiers and Terminal States

Graph quality tier:

```text
r2g_clean | research_only | rejected
```

Only `r2g_clean` enters the official clean index. Every admitted candidate-platform
implementation reaches exactly one durable terminal state:

```text
published_clean | retained_research_only | rejected | failed_with_reason | needs_human
```

### 1.4 Authority Order

```text
raw artifacts and independently recomputed facts
> validated structured reports
> Agent summaries
> free-form logs
```

An Agent statement cannot override a deterministic gate failure.

## 2. Gate Map and Detailed Gate Conditions

| Subskill or boundary | Gate | Required output |
| --- | --- | --- |
| `eda-install` | `ENV-GATE` | Qualified platform/toolchain profile or structured failure. |
| `rtl-acquire` | `ACQ-GATE` | Traceable, licensed, source-frozen candidate. |
| `rtl-acquire` | `SYNTH-GATE` | Platform-qualified synth-only generation and duplicate decision. |
| acquisition to flow | `RTL2FLOW-GATE` | Promoted project identical to the qualified source/configuration. |
| `signoff-loop` | `CONSTRAINT-GATE` | Qualified clock intent, provisional SDC, Fmax evidence, final objective. |
| `signoff-loop` | `SIGNOFF-GATE` | Complete ORFS run with strict route/antenna/timing/DRC/LVS/RCX verdicts. |
| `signoff-loop` | `LEARNING-GATE` | Eligible Recipe, causal A/B evidence, safe lifecycle transition. |
| flow to graph | `FLOW2GRAPH-GATE` | One same-run physical/signoff artifact bundle. |
| `def-graph` | `GRAPH-GATE` | Verified b/c/d/e/f generation and manifest. |
| `def-graph` | `PUBLISH-GATE` | Atomic clean-index publication or explicit non-clean state. |
| cross-cutting | `OPS-GATE` | Ledgered, isolated, reproducible, resource-accounted campaign operation. |

Every gate records `pass`, `fail`, `inconclusive`, or predeclared `not_applicable`,
plus input and output generation identities. A local pass cannot override a failed
handoff or downstream gate.

### 2.1 Gate-Condition Classes

Each gate decomposes into numbered conditions `GC-<GATE>-<NN>`. The registry block
`gate_conditions` is the machine binding of every condition below; `lint` enforces
spec/registry `GC-*` traceability, and the `gates` command executes every
executable condition fail-closed. Five binding classes exist:

```text
suite    -> executable now: a registry diagnostic suite must exit 0
builtin  -> executable now: a named deterministic probe inside the runner
command  -> executable now: a direct command must exit 0
formal   -> deferred: certified only by the listed frozen VAL-* cases
operator -> documented per-generation/per-machine procedure; command is normative
```

Rules:

1. A gate is **executably ready** when every `suite`/`builtin`/`command` condition
   passes. It is **certified** only when its `formal` conditions pass in a frozen
   campaign. Executable readiness never substitutes for certification.
2. Any failed executable condition fails the gate immediately (fail closed).
3. Deferred `formal` and `operator` conditions are always reported with counts and
   identities — never silently skipped (Runtime Rule 8).
4. Every `VAL-*` case MUST be referenced by a `formal` condition of the gate that
   owns it, so no formal scope can drop out of the gate map (lint-enforced).
5. `operator` conditions bind machine-local or per-generation evidence the
   repository cannot carry; their documented commands are normative.

### 2.2 `ENV-GATE` — toolchain truth before any expensive work

**GC-ENV-01: Comprehensive toolchain verification.** The verifier
`r2g-skills/signoff-loop/scripts/flow/check_env.sh` MUST exit 0 on the campaign
machine: python3/yosys/openroad/ORFS resolve, and every target platform reports
present. Binding: `command`. Evidence: verifier transcript.

**GC-ENV-02: Shared environment-layer parity.** The four `<skill>/scripts/flow/_env.sh`
copies MUST be byte-identical so all subskills resolve one toolchain (ENV-003); a
diverged copy silently splits the environment between stages. Binding: `builtin`
`env_sh_parity`. Evidence: per-file sha256 table.

**GC-ENV-03: Canonical skill deployment.** Every deployed skill under
`.claude/skills/` MUST be a symlink resolving inside the canonical `r2g-skills/`
tree — a copied install goes silently stale while the canonical skill evolves
(2026-06-08 stale-skill defect). Binding: `builtin` `skills_symlinked`. Evidence:
link-target table.

**GC-ENV-04: Graph/validation interpreter readiness.** The resolved validation
Python MUST import `pytest`, `torch`, `torch_geometric`, and `pandas` — the graph
stage and the independent dataset verifier depend on all four. Binding: `command`.
Evidence: import-probe status.

**GC-ENV-05: Provisioning unit surface.** The `eda-install`
detect → plan → install → pin → verify contract tests MUST pass. Binding: `suite`
`DIAG-EDA-INSTALL`. Evidence: pytest transcript.

**GC-ENV-06: Snapshot identity, conflict, and isolation certification.** Frozen
fault-injection proof that provisioning is honest, snapshots are sensitive,
resolution conflicts fail early, and out-of-domain evidence is excluded. Binding:
`formal` `VAL-ENV-001..004`.

**GC-ENV-07: Strict per-platform signoff capability.** Executable availability is
NOT platform capability: the round-2 pilot's ENV gate passed while nangate45 had
no LVS rule deck and an unusable zero-diff-area antenna diode (pilot P0-3), so
every strict SIGNOFF/CONSTRAINT gate was unreachable — discovered only after
multi-hour flows. For every target platform,
`r2g-skills/signoff-loop/scripts/flow/platform_capability.py --strict` MUST
report `strict_signoff_ready`: a full DRC deck, an LVS path (KLayout deck, or the
Magic+Netgen+PDK triple on sky130), a usable antenna model (per-layer
ANTENNA*AREARATIO rules plus a `CLASS CORE ANTENNACELL` diode with
`ANTENNADIFFAREA > 0`), RCX rules, and timing liberty. Binding: `command`.
Evidence: per-platform capability manifest.

### 2.3 `ACQ-GATE` — traceable, policy-screened candidates

**GC-ACQ-01: Versioned policy layer parseable.** Every screening/repair/publish
policy JSON under `r2g-skills/rtl-acquire/references/*.json` MUST parse as a
non-empty JSON document — screening decisions must be replayable from versioned
policy, never from free text (ACQ-003). Binding: `builtin` `policies_parse`.
Evidence: per-file parse table.

**GC-ACQ-02: Acquisition/screening unit surface.** The `rtl-acquire` tests
(discovery ledger, screening, dedup, expansion, publish gating) MUST pass.
Binding: `suite` `DIAG-RTL-ACQUIRE`. Evidence: pytest transcript.

**GC-ACQ-03: Discovery, closure, and screening certification.** Frozen proof of
traceable discovery fields, compilation-closure completeness, and metamorphic
screening stability. Binding: `formal` `VAL-ACQ-001..003`.

### 2.4 `SYNTH-GATE` — honest synth-only qualification

**GC-SYNTH-01: Scope and event parity.** `project_frontend_diagnosis.py --check`
MUST exit 0: every synth-only run stamped `flow_scope='synth_only'` and every
frontend abort paired with a `synth-frontend-*` failure event. An empty projection
is honest-empty, not proof — corpus machines MUST add `--require-nonempty`.
Binding: `suite` `DIAG-SYNTH-PROJECTION`. Evidence: parity counts.

**GC-SYNTH-02: Shared-store honesty.** The committed knowledge store MUST pass
`knowledge/honesty.py` (fail-run/event parity spans synth-only rows; no event on a
non-fail run). Binding: `suite` `DIAG-KNOWLEDGE-HONESTY`. Evidence: gate listing.

**GC-SYNTH-03: Qualification, dedup, and retry certification.** Frozen per-platform
proof that empty netlists, nonzero synthesis, mislabeled scope, or missing frontend
events cannot qualify, duplicates collapse, and retries are idempotent. Binding:
`formal` `VAL-ACQ-004..006`.

### 2.5 `RTL2FLOW-GATE` — promotion without drift

**GC-R2F-01: Promotion unit surface.** The promote path
(`scripts/promote/promote_candidates.py`) MUST be covered green by the
`rtl-acquire` suite. Binding: `suite` `DIAG-RTL-ACQUIRE` (shared, deduplicated).
Evidence: pytest transcript.

**GC-R2F-02: Closure byte-verification and configuration preservation
certification.** Frozen proof that changed source, missing manifests, unvendored
headers, profile mismatches, and clock-intent drift block automatic promotion.
Binding: `formal` `VAL-ACQ-007`, `VAL-FLOW-001`.

### 2.6 `CONSTRAINT-GATE` — qualified clocks and Fmax objectives

**GC-CON-01: Timing/Fmax unit surface.** The `signoff-loop` timing and Fmax-search
units (`check_timing`, `fmax_search`, SDC handling) MUST pass. Binding: `suite`
`DIAG-SIGNOFF-LOOP`. Evidence: pytest transcript.

**GC-CON-02: Clock, constraint-integrity, and Fmax-objective certification.**
Frozen proof that clock qualification classifies correctly, silent relaxation
cannot certify the original task, and Fmax objectives bind probe/model/policy/SDC/
confirming-run identities. Binding: `formal` `VAL-FLOW-007..009`.

The agent-side machine binding is `reports/signoff_manifest.json` `constraint.*`
(`build_signoff_manifest.py`, pilot P0-2): `qualified` is true only when the
stamped SDC period matches the Fmax-search winner AND the confirming run's final
timing tier (`reports/timing_check.json`) is clean. A failing qualification MUST
ENUMERATE the missing evidence — e.g. the absent final-timing confirmation —
never merely echo the otherwise-matching proxy and SDC periods (pilot H3).

### 2.7 `SIGNOFF-GATE` — strict, complete, bounded signoff

**GC-SIG-01: Flow/extractor unit surface.** The `signoff-loop` stage-runner,
extractor, and report tests MUST pass. Binding: `suite` `DIAG-SIGNOFF-LOOP`
(shared, deduplicated). Evidence: pytest transcript.

**GC-SIG-02: Downstream signoff gate fails closed.** The shared `signoff_gate.py`
consumed by `def-graph` MUST fail closed on MISSING DRC/LVS/route reports
(failure-patterns #34) — proven by the `def-graph` suite's gate tests. ORFS
completion MUST require a reconstructable SIX-STAGE lineage, not merely a clean
`finish` row: a repair-only generation (route+finish rerun) either carries a
recorded parent chain (`resume_meta.json` `parent_lineage`) or is attributed via
sibling ledgers, else it is incomplete (pilot P0-4). In strict tier
(`R2G_SIGNOFF_GATE=strict`) only the exact verdict `pass` may build the clean
tier; `pass_with_caveats` yields an explicitly research-tier artifact
(`dataset_tier` in the graph manifest) that MUST NOT enter a clean index (pilot
P0-1). Binding: `suite` `DIAG-DEF-GRAPH`. Evidence: pytest transcript.

**GC-SIG-03: Dependency, completion, strict-clean, and recovery certification.**
Frozen proof that stale/cross-run inputs block clean, absent stages stay non-clean,
every strict-signoff fault class is excluded from clean, and bounds/interruption
are honest. Binding: `formal` `VAL-FLOW-002..004`, `VAL-FLOW-006`.

**GC-SIG-04: Hang-alarm sweep.** A tool process older than `ORFS_TIMEOUT` with
`PPID=1` beside a frozen stage ledger is a hang the honesty DBs cannot see — a
hang writes no run. Before trusting campaign quiet, the operator MUST sweep:
`ps -eo pid,ppid,etimes,args | grep -E 'openroad|yosys'` and reconcile survivors
against live ledgers (kill `-9 -<pgid>` the stage group, then ingest). Binding:
`operator`. Evidence: sweep transcript.

### 2.8 `LEARNING-GATE` — a loop that cannot silently lie

**GC-LRN-01: Committed-store honesty gates.** `knowledge/honesty.py` over the
shipped `knowledge.sqlite` MUST be green: every `fail` run carries an
`orfs-fail-%` event, no event lands on a non-fail run, `ab_trials` is non-empty
once fail/partial rows exist, and failure events stay derivable from run columns.
Binding: `suite` `DIAG-KNOWLEDGE-HONESTY` (shared, deduplicated). Evidence: gate
listing with counts.

**GC-LRN-02: Dual-DB write honesty.** `tools/check_db_integrity.py` MUST exit 0:
journal actions carry run ids, A/B symptoms have `ab_launch` actions, promotions
have `promote` actions, escalations are journaled (requires the machine-local
journal). Binding: `suite` `DIAG-DB-INTEGRITY`. Evidence: J/L probe verdicts.

**GC-LRN-03: Learner and A/B unit surface.** The `signoff-loop` knowledge/learner/
A-B lifecycle tests MUST pass. Binding: `suite` `DIAG-SIGNOFF-LOOP` (shared,
deduplicated). Evidence: pytest transcript.

**GC-LRN-04: Per-platform promotion liveness.** Once a platform accumulates A/B
trials, `promoted` MUST eventually grow for that platform — trials-grow-but-
promoted-flat per platform is the 2026-06-24 arms-identical alarm (subtler than
empty `ab_trials`). The operator MUST review per-platform
`recipe_status`/`ab_trials` counts each campaign wave. Binding: `operator`.
Evidence: per-platform trial/promotion counts.

**GC-LRN-05: Evidence, lifecycle, and causal A/B certification.** Frozen proof of
evidence precedence, bounded actions, domain separation, lifecycle enforcement,
scope authority, A/B provenance with the staleness handshake, global judgment,
promotion sufficiency, idempotency, non-convergence, and safety monotonicity.
Binding: `formal` `VAL-AGENT-001..011`.

### 2.9 `FLOW2GRAPH-GATE` — one physical truth per bundle

**GC-F2G-01: Provenance/staleness unit surface.** The `def-graph` staleness-marker
and provenance tests MUST pass (stage-completion markers written LAST; the
2026-07-05 half-finish incident). Binding: `suite` `DIAG-DEF-GRAPH` (shared,
deduplicated). Evidence: pytest transcript.

**GC-F2G-02: Same-run provenance certification.** Frozen proof that same-name
cross-run or cross-platform mixtures are rejected and only one physical run's
artifact bundle passes. Binding: `formal` `VAL-FLOW-005`.

### 2.10 `GRAPH-GATE` — datasets that cannot silently lie

**GC-GRA-01: Extractor, view, and verifier unit surface.** The `def-graph` suite —
including the synthetic corner-case pipeline and the verifier's clean+negative
controls (every check proven to FAIL on a deliberate corruption) — MUST pass.
Binding: `suite` `DIAG-DEF-GRAPH` (shared, deduplicated). Evidence: pytest
transcript.

**GC-GRA-02: Independent corpus verification.** NEVER declare a regenerated corpus
good without `tools/verify_graph_dataset.py --batch <corpus>` under
`$R2G_GRAPH_PYTHON` — it re-derives topology, features, and labels from raw
DEF/LEF/liberty/SPEF with independent code; silent-value defects are invisible in
manifest row counts. A design whose graph generation was INTENTIONALLY denied
(signoff-gate block) reports `BLOCKED / not_applicable` (single-case exit 3),
never a `FileNotFoundError` crash and never a batch failure (pilot H2). The batch
additionally enforces corpus-wide `graph_id` uniqueness — individually-valid
graphs with colliding corpus ids fail the batch (pilot P0-5). Binding: `operator`
(per generation). Evidence: batch verifier report.

**GC-GRA-03: Input, five-view, identity, semantic, and manifest certification.**
Frozen proof that unqualified inputs are blocked, all five views survive
corruption tests, identity mismatches are rejected end-to-end, semantic
verification catches planted value corruption, and manifests are truthful.
Binding: `formal` `VAL-DATA-001..006`.

### 2.11 `PUBLISH-GATE` — atomic, honest publication

**GC-PUB-01: Corpus publish-gating unit surface.** The `rtl-acquire` publish-gate
tests MUST pass. Binding: `suite` `DIAG-RTL-ACQUIRE` (shared, deduplicated).
Evidence: pytest transcript.

**GC-PUB-02: Manifest/status honesty unit surface.** The `def-graph` manifest and
stats-gate tests MUST pass — a degraded column MUST read `invalid`/`skipped`,
never `ok`. Binding: `suite` `DIAG-DEF-GRAPH` (shared, deduplicated). Evidence:
pytest transcript.

**GC-PUB-03: Transactional publication and consumer certification.** Frozen
interruption proof that no partial generation becomes active, recovery commits
exactly once, and published files load in the declared consumer environment.
Binding: `formal` `VAL-DATA-007..008`.

### 2.12 `OPS-GATE` — campaign operation that stays auditable

**GC-OPS-01: Registry traceability lint.** `run_v1_validation_registry.py lint`
MUST pass: protocol digest+version pin, 45 REQ↔VAL pairs, gate coverage,
`GC-*` traceability, formal coverage, phase order, dependency acyclicity.
Binding: `builtin` `registry_self_lint`. Evidence: lint output.

**GC-OPS-02: Evidence isolation from the tracked tree.** Formal and diagnostic
evidence MUST land in the gitignored `docs/superpowers/plans/validation-reports/`
so a scored campaign never mutates tracked repository state. Binding: `builtin`
`reports_gitignored`. Evidence: `git check-ignore` verdict.

**GC-OPS-03: Ledger, isolation, reproducibility, resource, and continuity
certification.** Frozen proof of terminal-state ledgers, evaluation-state
isolation, declared reproducibility, resource accounting, and campaign resume.
Binding: `formal` `VAL-OPS-001..006`.

## 3. Subskill Contracts, Requirements, and Validation

### 3.1 `eda-install`

**Input:** host environment, target platform, V1 tool requirements.

**Output:** content-addressed toolchain snapshot and per-platform qualification
profile, or a structured actionable failure.

**ENV-001: Observable provisioning.** Every required tool, PDK/library, rule deck,
and graph dependency MUST be detected and verified per target platform. A partial
installation MUST NOT report ready or qualified.

**VAL-ENV-001: Provisioning readiness and honest failure.** Method: demonstration and
fault test. Pass only if every complete platform profile is ready and removing each
required dependency blocks downstream work. Oracle: direct version/import probes,
file digests, and process status. Evidence: detection snapshot, verification report,
profile state, and downstream-start audit.

**ENV-002: Frozen toolchain identity.** Every official run MUST bind the R2G commit
and dirty state, ORFS revision, executable versions and paths, graph packages,
platform files, PDK/library/rule-deck digests, environment, OS, and resource limits
into one stable snapshot digest.

**VAL-ENV-002: Snapshot completeness and sensitivity.** Method: inspection and
analysis. Pass only if unchanged snapshots normalize identically and any controlled
tool, PDK, library, or deck change creates a new digest. Oracle: independent probes
and hashing. Evidence: snapshots and field-level digest diff.

**ENV-003: Shared environment resolution.** Acquisition, signoff, and graph stages
in one run MUST resolve the same authoritative toolchain and platform profile.
Conflicts MUST fail before expensive execution.

**VAL-ENV-003: Shared resolution.** Method: conflict test. Pass only if normal
component resolutions agree and injected root, PDK, deck, or executable conflicts
fail early. Oracle: normalized path/version/digest comparison. Evidence: component
resolution reports and conflict log.

**ENV-004: Domain isolation.** Evidence from another platform profile, toolchain,
constraint policy, or graph schema MUST NOT silently enter V1 clean publication or
authoritative learning.

**VAL-ENV-004: Domain isolation.** Method: off-nominal test. Pass only if mismatched
or suspended evidence is excluded or explicitly retained as out-of-domain research.
Oracle: direct knowledge-store and clean-index queries. Evidence: evidence stamps,
queries, and exclusion records.

### 3.2 `rtl-acquire`

**Input:** discovered repositories or immutable RTL packages.

**Output:** source record, compilation manifest, screening decision, platform
synth-only result, duplicate identity, and promotion-ready candidate.

**ACQ-001: Traceable discovery.** Each candidate MUST record source origin,
immutable revision or archive digest, discovery time, license decision, and admission
decision.

**VAL-ACQ-001: Traceable discovery.** Method: demonstration and negative test.
Pass only if valid sources contain every required field and missing revision/license or
unsupported sources cannot qualify. Oracle: independent revision/archive hash and
policy lookup. Evidence: candidate ledger, source record, license decision, terminal
state.

**ACQ-002: Complete compilation manifest.** Qualification MUST bind every RTL,
transitive header, package, generated HDL, include order, define, top, parameter,
frontend, synthesis switch, relative path, size, and content digest.

**VAL-ACQ-002: Compilation closure.** Method: frontend trace and mutation test.
Pass only if the manifest equals the independently observed compilation closure and every
changed or removed input invalidates qualification. Oracle: frontend-opened-file trace
and hashing. Evidence: manifests, traces, mutations, and verdicts.

**ACQ-003: Evidence-based screening.** Screening MUST be reproducible from versioned
policy and semantic evidence. Risk text alone MUST NOT reject a supported design.

**VAL-ACQ-003: Semantic screening.** Method: policy replay and metamorphic test.
Pass only if repeated decisions are stable and irrelevant risk-like text does not cause
rejection. Oracle: policy replay and elaboration/synthesis facts. Evidence: decisions,
risk records, and diffs.

**ACQ-004: Honest synth-only qualification.** Success is platform-specific and
requires a completed pinned frontend, non-empty mapped design, valid statistics, and
all required pre-layout outputs. A nonzero command or skipped graph conversion cannot
be success. Every synth-only run MUST be stamped `flow_scope='synth_only'` in the
shared knowledge store, and every frontend abort MUST land as a paired
`synth-frontend-*` failure event (event/run parity).

**VAL-ACQ-004: Honest synth-only.** Method: per-platform positive and fault test.
Pass only if complete baselines qualify and empty netlists, nonzero synthesis, profile
mismatch, or unavailable required graph conversion do not. A run mislabeled with a
full-flow scope, or a frontend abort missing its `synth-frontend-*` event, also
fails. Oracle: command status, independent netlist statistics, library identity,
graph load, and knowledge-store scope/event parity queries. Evidence: stage
ledger, netlist, statistics, graph, and index state.

**ACQ-005: Deduplication integrity.** Byte-identical or policy-equivalent RTL and
mapped netlists MUST not count as independent designs. One source implemented on
multiple platforms retains one source identity.

**VAL-ACQ-005: Duplicate handling.** Method: hash and identity-graph test.
Pass only if exact/equivalent copies collapse, near-duplicate decisions are reproducible, and
three platform generations remain linked to one source. Oracle: canonical source and
netlist hashes. Evidence: signatures, policy, source count, generation count.

**ACQ-006: Retryable failure semantics.** Failures MUST retain classified reasons.
Repaired environment, policy, or frontend conditions MUST be retryable without
deleting history or duplicating current state.

**VAL-ACQ-006: Retry and idempotency.** Method: recovery test. Pass only if an initial
temporary failure remains auditable, the repaired attempt can proceed, and repeated
retry does not duplicate index, run, or learning evidence. Oracle: ledger and database
natural keys. Evidence: attempt chain and count diff.

**ACQ-007: Promotion source integrity.** Automatic promotion requires qualification
on the selected qualified platform and byte verification of the complete compilation
closure. Legacy unverifiable sources require requalification or operator-only
research handling.

**VAL-ACQ-007: Promotion integrity.** Method: per-platform mutation test.
Pass only if the unchanged complete candidate promotes and changed source, missing manifest,
unvendored header, profile mismatch, or unqualified profile is blocked. Oracle:
independent closure/profile comparison. Evidence: vendored inputs, manifests,
digests, promotion record, rejection reason.

### 3.3 `signoff-loop`

**Input:** a project that passed `RTL2FLOW-GATE`.

**Output:** qualified constraints, complete ORFS/signoff generation, structured
failure diagnosis, and bounded learning evidence.

#### 3.3.1 Promotion, Constraints, Physical Flow, and Signoff

**FLOW-001: Configuration preservation.** Promotion MUST preserve source closure,
top, parameters, frontend, platform, synthesis settings, and clock intent.

**VAL-FLOW-001: Promotion preservation.** Method: per-platform configuration mutation.
Pass only if the baseline passes `RTL2FLOW-GATE` and each unintended source, top,
parameter, frontend, platform, or clock mismatch is detected before Fmax or ORFS.
Oracle: normalized configuration/profile diff. Evidence: manifests, promoted config,
readiness report, mutation diff.

**FLOW-002: Stage dependency integrity.** Every downstream stage MUST consume the
intended successful upstream generation. Missing, stale, cross-run, or contradictory
prerequisites block clean status.

**VAL-FLOW-002: Stage dependency.** Method: artifact mutation test. Pass only if
removing, replacing, or staling each upstream input blocks downstream clean despite
a leftover success marker. Oracle: independently rebuilt dependency graph. Evidence:
stage ledger, artifact digests, mutation, verdict.

**FLOW-003: Full-flow completion.** Official completion requires successful synth,
floorplan, place, CTS, route, and finish stages plus required final artifacts.

**VAL-FLOW-003: Full-flow completion.** Method: per-platform positive and missing-stage
test. Pass only if complete runs pass and every absent, failed, incomplete, or
finish-only ledger remains non-clean. Oracle: independent stage-ledger and artifact
inspection. Evidence: raw stages, final artifacts, completion verdict.

**FLOW-004: Strict V1 clean signoff.** `r2g_clean` requires zero routing and antenna
violations, full DRC clean, LVS matched, frozen timing criteria met, RCX complete, and
parseable SPEF. `skipped`, `clean_beol`, missing evidence, or advisory-only timing is
not strict clean.

**VAL-FLOW-004: Strict signoff.** Method: per-platform parameterized mutation.
Pass only if each clean baseline passes and routing, antenna, DRC, LVS, timing, RCX, SPEF,
missing, or skipped faults are excluded from clean. Oracle: independent parsing under
frozen constraints and decks. Evidence: raw reports, parsed reports, policy IDs,
mutations, tier decisions.

**FLOW-005: Same-run provenance.** DEF, ODB, GDS, mapped netlist, SPEF, constraints,
stage ledger, and signoff reports MUST bind to one physical run by identity and
content digest.

**VAL-FLOW-005: Same-run provenance.** Method: mixed-bundle test. Pass only if genuine
bundles pass `FLOW2GRAPH-GATE` and same-name cross-run or cross-platform mixtures are
rejected. Oracle: independent provenance-graph reconstruction. Evidence: run
manifests, digests, mixed bundles, gate verdicts.

**FLOW-006: Bounded execution and recovery.** Every flow, repair, retry, and resume
loop MUST declare iteration, wall-time, and no-improvement bounds and preserve honest
interruption/recovery state.

**VAL-FLOW-006: Bounded recovery.** Method: timeout, interruption, repeated-state,
no-improvement, and resume tests. Pass only if bounds are honored, no false success is
learned, non-convergence escalates, and resume consumes only bound current artifacts.
Oracle: elapsed time, iteration/state sequence, artifact identities. Evidence:
limits, ledgers, exit status, resume record, terminal state.

**FLOW-007: Constraint integrity.** A clean verdict MUST NOT be obtained by silently
relaxing clock, area, guardband, checks, or rule decks. An approved change creates a
new flow variant, not a repair win for the original target.

**VAL-FLOW-007: Constraint integrity.** Method: controlled comparison. Pass only if
objective-preserving repair remains comparable and every relaxed variant receives a
new identity and cannot certify the original task. Oracle: normalized objective,
constraint-policy, and deck diff. Evidence: baseline/variant manifests and judge
record.

**FLOW-008: Clock and provisional-constraint qualification.** Before physical flow,
the design MUST be classified as combinational or sequential and have validated
clock semantics. Autonomous V1 Fmax supports one validated primary clock. Unresolved
multi/generated clocks, CDC, false paths, or multicycle paths require rejection,
`needs_human`, or an approved external constraint package.

**VAL-FLOW-008: Clock qualification.** Method: per-platform classification and fault
test. Pass only if combinational logic records `fmax_not_applicable`, valid single
clock receives traceable provisional SDC, and unresolved or mutated clock cases stop
at `CONSTRAINT-GATE`. Oracle: independent RTL event analysis and SDC parsing.
Evidence: clock candidates, assumptions, SDC, profile, gate, terminal state.

**FLOW-009: Fmax-derived objective qualification.** Uncurated sequential RTL without
an authoritative target MUST use bounded platform-specific Fmax search. Placement is
only a predicted proxy. The final objective MUST follow a frozen guardband policy and
be confirmed by the complete run's setup, TNS, and hold results. All probe, model,
policy, SDC, platform, and confirming-run identities MUST bind.

**VAL-FLOW-009: Fmax qualification.** Method: per-platform search, mutation, and
full-flow test. Pass only if a bound result becomes `fmax_derived` after final timing
passes, while cross-platform, proxy-only, inconclusive, changed-policy, changed-SDC,
or final-timing-failed results are blocked or research-only. Oracle: independent SDC
policy calculation and final timing recomputation. Evidence: probes, model, policy,
SDC digests, run identity, timing vector, gate verdict.

#### 3.3.2 Agent Memory, Recipe, A/B, and Promotion

**AGENT-001: Structured observation.** Decisions MUST cite current platform,
constraint, configuration, stage ledger, validated reports, manifests, graph checks,
and knowledge evidence. Structured terminal facts outrank free text.

**VAL-AGENT-001: Evidence precedence.** Method: controlled contradiction.
Pass only if the Agent follows structured/raw evidence when text disagrees and cites the source.
Oracle: Section 1.4 authority order. Evidence: observation, trajectory, verdict,
artifact IDs.

**AGENT-002: Bounded action space.** Automatic actions MUST come from a versioned
catalog with safety clamps. The Agent cannot bypass deterministic gates.

**VAL-AGENT-002: Action authority.** Method: policy and bypass test. Pass only if
allowed schedule/Fmax/retry/repair/stop actions are auditable and every gate-bypass
action is refused. Oracle: action catalog and gate state. Evidence: policy, tool
calls, refusal, final gates.

**AGENT-003: Failure-domain separation.** Environment, source, constraint/Fmax,
physical flow, signoff, extraction, data, and Agent-control failures MUST remain
distinct and MUST NOT create unrelated repair learning.

**VAL-AGENT-003: Failure classification.** Method: labeled fault test. Pass only if
every injected failure reaches the correct domain and cannot strengthen an unrelated
Recipe. Oracle: hidden fixture labels and database query. Evidence: failures,
diagnoses, Recipe diff, confusion matrix.

**AGENT-004: Recipe identity and lifecycle.** Every Recipe MUST have stable identity,
version, normalized effect, applicability, provenance, positive/negative evidence,
lifecycle, and retry bounds. Only applicable promoted Recipes may auto-apply.

**VAL-AGENT-004: Lifecycle enforcement.** Method: state-machine test. Pass only if
candidate, parked, shadow, demoted, stale, unreadable, incomplete, or wrong-domain
Recipes cannot affect live execution and missing safety state fails closed. Oracle:
lifecycle table. Evidence: Recipe rows, read status, ranking, action journal.

**AGENT-005: Evidence scope.** Live use requires matching qualified platform,
constraint, check, symptom, and design domain. Pooled evidence may nominate a
candidate but cannot grant authority. Equivalent effects share negative evidence.

**VAL-AGENT-005: Scope and effect identity.** Method: ranking test. Pass only if large
wrong-domain or cross-platform evidence cannot outrank valid exact authority and a
renamed equivalent Recipe cannot escape negative evidence. Oracle: domain keys and
effect fingerprint. Evidence: rows, fingerprints, ranking, eligibility.

**AGENT-006: Controlled A/B design.** A decisive trial MUST bind trial, Recipe
version/effect, subject, baseline, arms, runs, source, toolchain, platform,
constraint policy, objective, and intended delta. Arms may differ only by the target
Recipe effect. Plan and judge MUST complete a staleness handshake: an arm planned
against one Recipe version/effect cannot be judged against another, and trial
ownership binds to the full recipe key (symptom, platform, constraint policy,
effect), never a name or partial key.

**VAL-AGENT-006: A/B provenance.** Method: valid and confounded pair test.
Pass only if the valid pair is judgeable and extra config, relaxed objective, foreign run,
different subject/platform/policy, stale Recipe, version-skewed plan/judge pairs,
partial-key ownership collisions, or no-op arms are invalidated.
Oracle: independent full configuration, objective, ownership, and hash diff.
Evidence: plans, arm manifests, diffs, run records, judge decisions.

**AGENT-007: Global outcome judgment.** A higher intermediate score or cleared target
symptom is insufficient. Terminal usability, full signoff vector, regressions,
constraint integrity, and cost determine the winner.

**VAL-AGENT-007: Global judgment.** Method: parameterized outcome test. Pass only if
true global improvement may win while non-clean, regressive, clock/Fmax/area-relaxed,
or check-disabled arms cannot win the original objective. Oracle: frozen terminal
and severity ordering. Evidence: metrics, signoff vectors, objective diffs, rationale.

**AGENT-008: Promotion evidence.** Promotion requires complete A/B provenance and at
least two independent subjects in the same qualified platform, constraint, and
applicability domain. Duplicate, imported, benchmark, self-scored, or pooled transfer
evidence is insufficient by itself.

**VAL-AGENT-008: Promotion sufficiency.** Method: lifecycle evidence test.
Pass only if two independent in-domain subjects can satisfy promotion and every listed
substitute cannot. Oracle: unique subject/configuration count, domain keys, and
origin query. Evidence: evidence rows, calculation, transition, rejection reasons.

**AGENT-009: Learning integrity.** Ingest, aggregation, A/B judgment, and lifecycle
transition MUST be idempotent and recoverable. Evaluation evidence cannot be counted
again as ordinary independent history.

**VAL-AGENT-009: Idempotency and recovery.** Method: duplicate-ingest and interrupted
transaction test. Pass only if counts stay stable, reconciliation commits once, run
identities remain distinct, and A/B evidence is not double counted. Oracle: natural
keys and transaction journal. Evidence: database snapshots, reconciliation logs,
confidence values.

**AGENT-010: Safe non-convergence.** The Agent MUST detect no-improvement,
repeated-state, and strategy cycles within declared bounds, while not confusing
material progress with a cycle.

**VAL-AGENT-010: Non-convergence.** Method: state-machine sequence test. Pass only if
true cycles stop and escalate within bounds, attempts are preserved, and meaningful
count progress does not trigger false termination. Oracle: independent state/effect
sequence analysis. Evidence: trajectory, state hashes, budgets, escalation.

**AGENT-011: Safety monotonicity.** Learning may reorder eligible actions but MUST
NOT weaken platform, source, constraint/Fmax, objective, signoff, provenance, graph,
or publication gates.

**VAL-AGENT-011: Safety under learning.** Method: matched no-memory versus learned
comparison. Pass only if eligible action order may change but all safety-gate verdicts
remain identical. Oracle: gate-vector equality. Evidence: paired trajectories,
rankings, gates, final states.

### 3.4 `def-graph`

**Input:** one bundle that passed `FLOW2GRAPH-GATE`.

**Output:** verified b/c/d/e/f graph generation, complete manifest, and atomic
publication decision.

**DATA-001: Qualified input.** Official graph construction MUST consume a qualified
platform generation with complete flow, strict signoff, same-run provenance, and
applicable constraint/Fmax evidence. Overrides are research-only.

**VAL-DATA-001: Qualified input.** Method: per-platform positive and off-nominal test.
Pass only if qualified baselines are clean-eligible and candidate/suspended, dirty,
incomplete, unbound, or overridden inputs are blocked or research-only. Oracle:
independent gate evaluation. Evidence: input manifest, profile, constraints, override,
graph manifest, index query.

**DATA-002: Feature and label completeness.** Every required extractor and join MUST
complete for the current generation. Applicable values cannot silently be stale,
empty, all-zero, or all-NaN. Every normalized label tensor MUST carry its raw twin
(`y_raw`, `edge_y_raw`, `rc_edge_y_raw`) with slot-for-slot shape and NaN parity and
the declared `log1p` identity where one is defined.

**VAL-DATA-002: Feature and label integrity.** Method: per-platform fault test.
Pass only if complete baselines pass and missing, stale, empty, zero, NaN, invalid-join,
or raw-twin-parity mutations cannot publish clean. Oracle: raw-artifact sampling,
joins, distributions, and twin shape/NaN/`log1p` recomputation.
Evidence: extractor markers, tensor/CSV statistics, recomputations.

**DATA-003: Five-view completeness.** Each official generation MUST contain b, c, d,
e, and f under one schema. `netlist_graph.pt` is not a substitute. The official
emission is heterogeneous (`HeteroData`); the homogeneous form remains the verified
source of truth, and the hetero re-view MUST be value-preserving with an exact
homogeneous inverse.

**VAL-DATA-003: Five views.** Method: remove/corrupt/interruption test. Pass only if
complete sets load and no missing, corrupt, partial, or lossy hetero/homo round-trip
set reaches the clean index. Oracle: digest, schema load, required-view enumeration,
independent homo reconstruction. Evidence: graph files, build
ledger, staging manifest, verdict.

**DATA-004: Cross-view identity.** Views, features, labels, and manifests MUST agree
on design, platform, source, constraint, run, graph, entity, and generation identity.
Generation identity is carried end-to-end: every artifact records the generation that
produced it, and a consumer MUST reject an input whose recorded generation differs
from the run it claims to extend.

**VAL-DATA-004: Identity consistency.** Method: one-field-at-a-time mutation.
Pass only if baselines agree, every mismatch is rejected, and one source across three
platforms retains one source identity with distinct implementation/graph identities.
Oracle: manifest, identity graph, tensor keys. Evidence: identity table and verifier.

**DATA-005: Independent semantic verification.** A verifier independent of generator
health flags MUST check topology, counts, relations, vocabularies, statistics, joins,
units, and sampled raw-artifact values.

**VAL-DATA-005: Semantic verification.** Method: corruption test. Pass only if each
platform baseline passes and topology, feature, vocabulary, label-join, or unit/value
corruption is detected. Oracle: independent DEF/LEF/Liberty/SPEF/ODB parsing or tool
recomputation. Evidence: raw artifacts, mutations, recomputed values, named checks.

**DATA-006: Complete manifest.** The manifest MUST identify schema, generation,
design, qualified platform, flow, source, compilation, toolchain, physical run,
constraints, Fmax, artifacts, extractors, views, counts, signoff, verifier, tier,
publication state, and timestamps with required digests.

**VAL-DATA-006: Manifest truthfulness.** Method: schema and mutation test.
Pass only if every platform baseline is complete and removing a field class or changing a
digest/verdict blocks clean. Oracle: schema validation, file hashing, profile lookup.
Evidence: manifests, schema results, digest inventory, gate decisions.

**DATA-007: Transactional publication.** Build and verify in staging. The clean index
and active pointer change only after all checks pass. Failure leaves the previous
generation byte-identical.

**VAL-DATA-007: Atomic publication.** Method: interruption at extraction, assembly,
verification, pointer, and index steps. Pass only if no partial generation becomes
active and recovery commits exactly once or cleans staging. Oracle: before/after
hashes and pointer/index inspection. Evidence: staging ledger, pointer, index,
recovery, hashes.

**DATA-008: Consumer usability.** Published files MUST load in the declared
PyTorch/PyG environment, pass schema checks, and have documentation matching actual
composition, provenance, quality, use, and limitations.

**VAL-DATA-008: Consumer validation.** Method: independent loading, data-loader,
training-smoke, and documentation test. Pass only if all views and tensors load and
documentation matches manifests and platform/constraint qualification. Oracle:
consumer code and manifest inspection. Evidence: environment lock, logs, schema
report, documentation checklist.

### 3.5 Cross-Cutting Operation and Evidence

**OPS-001: Complete run ledger.** Every source, candidate-platform implementation,
run, and Agent action MUST have durable identity, state history, time, outcome, and
reason. Source and platform-generation counts remain distinct.

**VAL-OPS-001: Terminal ledger.** Method: mixed campaign test. Pass only if each
source and implementation has correct identity and exactly one terminal state with no
orphans or contradictions. Oracle: identity graph and state-machine query. Evidence:
campaign input, ledger, terminal summary.

**OPS-002: Isolated evaluation state.** Formal validation MUST use isolated,
versioned knowledge snapshots and prevent development, production, regression, or
held-out leakage.

**VAL-OPS-002: State isolation.** Method: marker contamination test. Pass only if the
evaluator reads/writes only declared stores and held-out outcomes cannot become
pre-score learning. Oracle: store hashes and marker queries. Evidence: store IDs,
access log, queries, Agent configuration.

**OPS-003: Evidence preservation.** Formal runs MUST preserve source, configuration,
platform, constraints/Fmax, toolchain, stages, signoff, graphs, Agent trajectory,
knowledge, resources, and verdict evidence.

**VAL-OPS-003: Evidence completeness.** Method: package inspection and removal test.
Pass only if a complete package is independently auditable and removing any required
artifact class makes the verdict incomplete. Oracle: package schema and digest
inventory. Evidence: package manifest, mutations, audit results.

**OPS-004: Reproducibility semantics.** The release MUST declare byte-stable versus
semantic/tolerance comparisons and measure nondeterminism.

**VAL-OPS-004: Reproducibility.** Method: two clean-workspace campaigns. Pass only if
all differences satisfy the frozen comparison class and tolerance. Oracle: independent
artifact comparator. Evidence: both packages, diffs, seeds, verdicts.

**OPS-005: Resource accountability.** Wall time, CPU, available peak memory, Fmax
probes, retries, Agent actions, and manual interventions MUST be recorded against
frozen limits.

**VAL-OPS-005: Resource accounting.** Method: small/medium/large and limit-exhaustion
test. Pass only if measurements are present, unavailable fields are explicit, and
flow/Fmax exhaustion terminates honestly. Oracle: scheduler/process measurements.
Evidence: limits, resource logs, exit states, metrics.

**OPS-006: Campaign continuity.** One candidate failure or escalation MUST NOT
corrupt, reset, or starve unrelated work. Campaign state MUST be durable and resumable.

**VAL-OPS-006: Continuity and resume.** Method: multi-candidate failure, hang,
interrupt, and restart test. Pass only if unrelated work continues, completion is not
duplicated, resumable work restarts correctly, and only declared work/budget/safety
conditions stop the campaign. Oracle: scheduling timeline, ledger, artifact identity.
Evidence: queue, worker events, restart record, terminals, duplicate audit.

## 4. Handoff Contracts

### 4.1 Environment to All Subskills

Every subskill consumes the same `toolchain_snapshot_id` and
`platform_profile_id`. A path fallback that resolves different tool or PDK bytes is a
hard failure. `ENV-GATE` is a prerequisite for every official case.

### 4.2 `RTL2FLOW-GATE`

Required matching fields:

```text
source_id and source_manifest_digest
complete compilation closure digests
top module and top parameters
frontend, defines, include order, synthesis switches
mapped-netlist identity and synthesis status
platform_profile_id
clock intent and provisional-constraint status
```

Any missing or mismatched field blocks automatic promotion.

### 4.3 `FLOW2GRAPH-GATE`

Required same-run fields:

```text
physical_run_id and stage-ledger digest
platform_profile_id and toolchain_snapshot_id
constraint_policy_id and final SDC digest
applicable Fmax evidence and confirming-run identity
DEF, ODB, GDS, mapped-netlist, SPEF digests
route, antenna, timing, DRC, LVS, RCX report digests
strict signoff verdict
```

Matching design/platform names alone never establish provenance.

### 4.4 `PUBLISH-GATE`

One graph generation contains all five views, feature/label generations, schema,
verifier report, signoff/provenance record, and file digests. Publication uses
staging, verification, atomic pointer/index update, and exactly-once recovery.

## 5. Runtime Rules

1. **Fail closed:** missing or contradictory safety evidence blocks clean status.
2. **Stable identities:** source, platform implementation, physical run, graph
   generation, Recipe, trial, and fix session have separate durable IDs.
3. **No stale reuse:** reuse requires matching generation IDs and content digests.
4. **Bounded autonomy:** every retry, repair, Fmax probe, A/B trial, and resume has
   time, iteration, and no-improvement limits.
5. **Objective protection:** clock, area, guardband, checks, and decks cannot change
   inside a repair comparison without creating a new variant.
6. **Evidence isolation:** development, production, regression, A/B, benchmark, and
   held-out evidence remain distinguishable.
7. **Exactly-once transitions:** ingest, lifecycle transition, and publication are
   idempotent and recoverable.
8. **No silent skips:** required checks and test subcases cannot be skipped in a
   scored campaign.
9. **Honest status:** evaluator, fixture, and prerequisite failures are not Agent
   failures and are never passes.
10. **No gate weakening by learning:** memory may reorder safe actions only.

## 6. Machine Execution

### 6.1 Registry Commands

```bash
cd /proj/workarea/user5/r2g-skills   # repo root

python3 tools/run_v1_validation_registry.py lint
python3 tools/run_v1_validation_registry.py list
python3 tools/run_v1_validation_registry.py plan --case VAL-FLOW-009
python3 tools/run_v1_validation_registry.py plan --platform sky130hs --gate CONSTRAINT-GATE
python3 tools/run_v1_validation_registry.py gates --dry-run
python3 tools/run_v1_validation_registry.py gates
python3 tools/run_v1_validation_registry.py gates --gate SIGNOFF-GATE
python3 tools/run_v1_validation_registry.py diagnostics --dry-run
```

`gates` executes every executable (`suite`/`builtin`/`command`) Section 2 gate
condition fail-closed, deduplicating suites shared across gates, and reports every
deferred `formal`/`operator` condition with counts — never silently skipped. It
writes `validation-reports/gate-conditions.json` and exits nonzero if any
executable condition fails. Like `diagnostics`, it never awards an official
`VAL-*` verdict: executable readiness is a prerequisite for, not a substitute for,
the frozen formal campaign.

`diagnostics` executes current component and adversarial suites but never awards an
official `VAL-*` verdict. The inventory is executable today and MUST stay green:

| Suite | Command target | Verifies |
| --- | --- | --- |
| `DIAG-EDA-INSTALL` | `pytest eda-install/tests` | toolchain detect/pin/verify contract |
| `DIAG-RTL-ACQUIRE` | `pytest rtl-acquire/tests` | acquisition, screening, expansion, publish gating |
| `DIAG-SIGNOFF-LOOP` | `pytest signoff-loop/tests` | flow, signoff, memory DBs, A/B lifecycle |
| `DIAG-DEF-GRAPH` | `pytest def-graph/tests` | extractors, five views, verifier, corner cases |
| `DIAG-KNOWLEDGE-HONESTY` | `knowledge/honesty.py --db <shipped store>` | committed knowledge-store honesty gates |
| `DIAG-SYNTH-PROJECTION` | `project_frontend_diagnosis.py --check` | synth-only scope/event parity (vacuously empty on a fresh clone; corpus machines add `--require-nonempty`) |
| `DIAG-DB-INTEGRITY` | `tools/check_db_integrity.py` | dual-DB write honesty; needs the machine-local journal |

Formal `run` schedules only frozen cases whose fixtures are
bound and evaluators are ready:

```bash
python3 tools/run_v1_validation_registry.py run \
  --platform nangate45 \
  --out docs/superpowers/plans/validation-reports/validation-report.json \
  --evidence-dir docs/superpowers/plans/validation-reports/validation-evidence
```

### 6.2 Execution Status

```text
completed | blocked_by_prerequisite | harness_error | not_scheduled
```

Only `completed` receives:

```text
pass | fail | inconclusive | not_applicable
```

A mandatory non-completed case makes the campaign incomplete. It does not fabricate
a case pass or system-under-test failure.

### 6.3 Formal Order

```text
1. registry lint, executable gate-condition sweep (gates), freeze/evaluator self-check
2. ENV
3. ACQ and SYNTH
4. RTL2FLOW and CONSTRAINT/Fmax
5. physical flow and strict signoff
6. FLOW2GRAPH, graph verification, publication
7. Agent learning and A/B
8. operations, reproducibility, evidence package
9. held-out capability and Agent ablation
10. per-platform qualification and independent review
```

Zero-tolerance failure stops scored downstream work unless the frozen plan requires
diagnostic continuation.

## 7. V1 Acceptance

V1 acceptance requires:

1. this specification, registry, toolchain, fixtures, schema, policies, limits, and
   thresholds are Frozen and content-addressed;
2. all 45 requirements and every mandatory expanded subcase pass with no required
   skip, and every executable gate condition passes on the release machine;
3. all source, platform, constraint, signoff, graph, learning, and publication
   zero-tolerance false-positive counts are zero;
4. validator mutations prove that critical checks fail when their protected
   invariant is broken;
5. the complete campaign passes twice from clean workspaces without changing code,
   fixtures, policies, expected results, or toolchain;
6. an independent reviewer accepts traceability and evidence;
7. the release commit and validation generation are archived.

Complete multi-platform V1 requires all three target profiles qualified. A named
qualified subset may be released with a narrower claim.

## 8. Debug Convergence and Change Control

```text
Does a finding violate a Frozen in-scope REQ?
  yes -> implementation blocker; fix and rerun the same VAL plus regressions
  no  -> later-version or research backlog

Does the finding prove the REQ or VAL oracle is invalid?
  yes -> version the specification and start a new validation generation
  no  -> preserve the Frozen contract
```

Before freeze, reviewers may revise this Proposed document. After freeze:

- implementation fixes do not change the requirement or oracle;
- historical regressions add examples, not scope;
- clarifications require reviewer approval;
- scope, oracle, fixture population, metric, or threshold changes require a new
  specification version and validation generation.

Passing Section 7 means V1 has converged inside its declared boundary. It does not
mean no defect can exist outside that boundary.
