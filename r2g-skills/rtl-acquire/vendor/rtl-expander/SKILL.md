---
name: rtl-expander
description: Discover, safely acquire, recover, validate, conservatively repair, synthesize, deduplicate, contamination-audit, score, and publish provenance-complete RTL design corpora for RTL-model training. Use when expanding Verilog, SystemVerilog, or VHDL datasets; operating a SQLite repository frontier and discovery scheduler; acquiring immutable public repository revisions; recovering design instances and tops; building family-deduplicated training views; auditing quality or benchmark leakage; or running a continuous RTL corpus factory. Excludes graph conversion, partitioning, physical design, and GDS work.
---

# RTL Expander

Build a design-level RTL corpus whose primary metric is unique, high-quality, synthesis-valid `DesignFamily` count. Preserve raw assets in the Data Lake and apply strict gates only when creating Training Exports.

Production validation provenance: this skill completed a certified campaign
covering 33,707 immutable RepositoryRevisions, 13,466 Formal DesignInstances,
and 10,606 provenance-complete synthesis-valid DesignFamilies. These figures
validate the implementation; they are not defaults, bundled state, or a fixed
product target. Require each caller to choose its own corpus root, objective ID,
and target Family count.

## Scope Boundary

Perform:

`DISCOVER -> ACQUIRE -> CLASSIFY -> RECOVER -> PER-FILE FRONTEND -> PREFLIGHT -> REPAIR -> SYNTHESIZE -> MACRO ANALYZE -> FUNCTIONAL EVIDENCE -> DOCUMENT/SPEC RECOVERY -> MACHINE FACTS -> DEDUP -> FAMILY CLUSTER -> SPLIT ASSIGNMENT -> CONTAMINATION AUDIT -> SCORE -> TRAINING VIEWS -> PUBLISH -> SNAPSHOT`

Never perform graph conversion, partitioning, placement, CTS, routing, GDS, or physical verification. Hand published RTL manifests to downstream skills instead.

## Default Paths

- local public-repository intake: `$HOME/work/_downloads`
- corpus root: `$HOME/work/data/rtl_corpus`
- append-only truth ledger: `<corpus>/ledger/*_events.jsonl`
- rebuildable corpus index: `<corpus>/state/corpus.sqlite`
- compatibility views: `<corpus>/manifests/*.jsonl`
- benchmark registry: `<corpus>/benchmark_registry/`
- mutable discovery frontier: `<corpus>/state/frontier.sqlite`
- immutable repository revisions: `<corpus>/repositories/<provider>/<namespace>/<repo>/<commit>/`
- readable design catalog: `<corpus>/designs/<repo>__<top>__<16-hex>/`
- immutable run snapshots: `<corpus>/snapshots/`

Override paths with explicit CLI arguments. Do not use an existing graph dataset as the discovery seed; use it only for overlap audits.

## Mandatory Operating Rules

1. Treat `Repository`, `File`, `Module`, `DesignInstance`, `DesignFamily`, and `TrainingView` as different units.
2. Count `DesignFamily` as the main scale metric and `DesignInstance` as the processing unit.
3. Preserve repository URL, commit, source paths, license evidence, acquisition time, and hashes. Never overwrite original RTL.
4. Never execute untrusted repository scripts, builds, binaries, hooks, or installers. Use static parsing and controlled frontends only.
5. Recover tops from explicit manifests first, then dependency-DAG roots and controlled elaboration. Exclude testbench, simulation, and formal tops structurally and by naming.
6. Resolve recursive source/include/package/interface/module dependencies. Record every unresolved dependency; never silently black-box it.
7. Preserve original Verilog/SystemVerilog/VHDL as the primary code-training asset. Treat converted Verilog as a synthesis representation.
8. Repair only to recover intended compilability. Prefer R1 build repair, then deterministic R2/R3 transforms. Bound retries at attempts 0–4. Never change behavioral semantics without equivalence evidence.
9. Preserve known memories, hard IP, and vendor macros. Penalize unknown components, not known macros.
10. Keep engineering quality, functional confidence, and training value as separate versioned scores.
11. Keep contaminated, generated, unresolved, and historical RTL in the Data Lake; exclude or down-tier it in Training Exports.
12. Never claim Gold/Premium without a completed benchmark contamination audit.
13. Keep every `DesignFamily` in exactly one of train/val/test. Build `rtl_split_group_closure_v1` connected components across families that share any RTL source unit, have an ancestor/descendant hierarchy relation within one repository revision, or belong to one tightly coupled project target; assign the entire component to one persisted `rtl_split_v1` split. Never silently reassign a frozen SplitGroup.
14. Version family identity as `rtl_family_v1` and retain confidence plus evidence. Never report family-count changes without the family schema.
15. Represent mixed-language designs with per-file `source_units`; never force one repository-level language label.
16. Keep `EngineeringQuality` independent from `ReleaseEligibility`; use explicit license status and release policy.
17. Require idempotent run keys, per-revision worker locks, stage state, atomic central-ledger commits, crash resume, and size-based resource classes.
18. Canonicalize provider/namespace/repository into `RepositoryKey` before acquisition and use `RepositoryRevisionKey = RepositoryKey + commit_sha` after resolving an immutable revision.
19. Keep discovery, acquisition, and processing separate. Use SQLite for mutable frontier/scheduler state; use JSONL only for published manifests and snapshot exports.
20. Prefer bounded archive acquisition. Reject traversal, links, oversized archives/file counts, recursive submodule execution, LFS smudge, hooks, and repository-owned code.
21. Store one immutable original snapshot per RepositoryRevision. Use readable design directories only as catalogs; keep `design_id` as stable identity and never make a versioned Family the physical storage owner.
22. Enforce funnel conservation at every stage: input must equal success plus duplicate, quarantine, failure, and skipped/pending outcomes. Report a nonzero residual as an integrity failure; never silently mix local-entry, RepositoryKey, RepositoryRevision, top-candidate, attempt, and DesignInstance counts.
23. Hash immutable bytes once at admission. Persist SHA-256, size, object ID,
    schema, producer, and generation; ordinary Batch, preflight, FINAL, and
    certification compare recorded commitments and never rehash the full source
    corpus. Missing legacy commitments are `REHASH_REQUIRED` and use an explicit
    migration outside finalization.
24. Allow and prefer automatic failure adjudication and repair. Treat classification only as routing evidence, support explicit abstention, preserve the complete diagnostic evidence, and publish a recovered design only after the normal parse, elaboration, synthesis, applicable equivalence, and functional gates pass. Never treat a classifier label as correctness evidence.

Read [corpus-factory-spec.md](references/corpus-factory-spec.md) before changing schemas, gates, repair policy, family identity, scoring, or publication behavior.
Read [phase1-production.md](references/phase1-production.md) when operating or auditing the 1,000-RepositoryRevision milestone.
Read [phase1-5-quality.md](references/phase1-5-quality.md) before running failure-recovery audits, mapped-synthesis cohorts, functional-ontology recalibration, license-evidence recovery, or benchmark-registry promotion.
Read [phase2-production.md](references/phase2-production.md) before operating or changing the 10,000-RepositoryRevision production milestone, online R1 recovery, mixed-language frontend work, selective mapping policy, or Phase-2 dashboards.
Read [scalable-state-plane.md](references/scalable-state-plane.md) before changing ledger events, `corpus.sqlite`, incremental Family/SplitGroup maintenance, snapshots, release identity, write ordering, or the license-clean release subset.
Read [integration.md](references/integration.md) when installing this skill in a
new environment or handing its certified snapshot to a downstream backend flow.

## Run One Expansion Round

Use the deterministic entrypoint first:

```bash
python3 "$HOME/.codex/skills/rtl-expander/scripts/run_expansion_round.py" \
  --source-root "$HOME/work/_downloads" \
  --corpus-root "$HOME/work/data/rtl_corpus" \
  --max-repos 25 \
  --synthesize
```

The entrypoint performs safe local intake, project-bounded top recovery, mixed-language source-unit closure, controlled Yosys/GHDL synthesis, documentation and semantic-fact recovery, versioned family clustering, closure/hierarchy/project-aware frozen splits, deduplication, conservative scoring, atomic publication, and snapshot generation. It never invokes repository code.

Use `--dry-run` to inspect the next repository batch. Use `--include-scanned` only for an intentional deterministic rescan. Use `--repo NAME` to target one repository.

## Run Continuous Factory Components

Keep the layers independently operable:

```bash
python3 "$HOME/.codex/skills/rtl-expander/scripts/discover_repositories.py" \
  --corpus-root "$HOME/work/data/rtl_corpus" --providers github,gitlab,codeberg,fusesoc --budget 5000

python3 "$HOME/.codex/skills/rtl-expander/scripts/acquire_frontier.py" \
  --corpus-root "$HOME/work/data/rtl_corpus" --max-repos 500

python3 "$HOME/.codex/skills/rtl-expander/scripts/run_factory_round.py" \
  --discover-budget 5000 --acquire-budget 500 --process-budget 500
```

For a declared production-batch acquisition target, use the persistent target controller instead of manually invoking one-shot rounds:

```bash
python3 "$HOME/.codex/skills/rtl-expander/scripts/run_until_revision_target.py" \
  --corpus-root "$HOME/work/data/rtl_corpus" \
  --factory-round-id p2r_YYYYMMDD_batchNNNN \
  --target-new-acquired 2000
```

Keep the round's first `start.json` frozen. Let the controller repeat bounded discovery and acquisition until the distinct successful RepositoryRevisionKey delta reaches the target. It must first persist `TARGET_REACHED_PENDING_LOCK`, stop new acquisition, wait for zero acquisition claims, take a transactionally consistent snapshot, and freeze the complete sorted overshoot set in write-once `cohort_lock.json`. Then drain processing, generate a FINAL cohort-filtered delta, write the factory completion manifest, and calibrate the scheduler exactly once from the FINAL-delta identity. Resume the same command after interruption; never create a replacement round merely because one component invocation ended. Treat every `BLOCKED_*`, `HARD_FAIL_*`, and `FAILED_FINALIZATION` state as explicit non-success.

Use `pipelined_processing_v1` by default. Treat `cohort_lock.json` as the
publication barrier, not the processing-start barrier: every newly acquired
immutable revision is inserted into the mutable SQLite `processing_queue` and
may immediately run revision-local classification, top recovery, frontend,
parse/elaboration, generic synthesis, semantic evidence, and bounded repair.
These workers may write only immutable/run-keyed staging artifacts and
revision-local ledger facts. They must not publish final Family or SplitGroup
identity, Gold/public manifests, current snapshots, or FINAL yield. After the
target, freeze the exact overshoot set, drain by exact terminal-key equality,
then run reconciliation and finalization. A restart must reconstruct/requeue
operational claims without changing the cohort or duplicating run-keyed work.
Report pre-lock terminal coverage, queue latency/depth, overlap, post-lock
drain, finalization time, and acquisition/processing idle time.

Preserve Batch 5 as the clean sequential-acquisition baseline. For Batch 6 and
later, `run_until_revision_target.py` automatically enables
`bounded_parallel_acquisition_v1`: a bounded set of process-isolated fast and
slow acquisition lanes partition the existing attempt budget using estimated
repository size. This changes only candidate acquisition into an immutable
RepositoryRevision. The Family controller, child target, cohort lock,
overshoot, processing queue, Family/Split reconciliation, Gold, FINAL, and
release-certification contracts remain unchanged.

For corpus-scale growth, the hard primary target is the global count of unique,
provenance-complete, synthesis-valid `DesignFamily` objects. RepositoryRevision
targets are bounded adaptive micro-cohorts and are never corpus completion:

```bash
python3 "$HOME/.codex/skills/rtl-expander/scripts/run_until_family_target.py" \
  --corpus-root /path/to/rtl_corpus \
  --objective-id design-family-<target> \
  --target-global-design-families <target> \
  --revision-batch 2000
```

The family controller may create multiple independently locked and FINAL child
rounds. It stops only after `state/corpus.sqlite` reports the global
`rtl_family_v1` target and every child round passes the unchanged quality gates.
If a child process exits while its persisted controller remains in an
unambiguous nonterminal/recoverable state, the Family controller backs off and
resumes that same round automatically. It must still hard-stop on identity,
cohort/terminal, lineage, leakage, unknown-split, consumption, or other
correctness ambiguity; continuous operation never means bypassing a hard gate.
License resolution, F2+ functional evidence, and LARGE/XLARGE share are parallel
quality lanes, never Family-admission hard gates. License-unknown designs may
enter the provenance-complete internal Formal Corpus but never the public
license-clean snapshot. The Formal Family gate remains provenance, parse,
elaboration, generic synthesis, deduplication, and split correctness; functional
confidence is promoted into a separately reported verified subset. Prefer large
designs through bounded, directly RTL-anchored discovery priority and report the
share as a soft target without displacing the general Family-yield lane.

When later closure evidence joins frozen SplitGroups, keep the cohort immutable and normalize all raw conflicts into global maximal transitive components before classifying them. An exact `{train,val}` component may be automatically reconciled to val under a round-bound `rtl_split_reconciliation_v1` plan. A component involving test defaults to hard stop and requires `rtl_split_profile_transition_v1` plus a downstream-consumption audit; only a separately authored `CAMPAIGN_INTERNAL` contract with zero recorded training/evaluation consumers authorizes automatic versioned rollover and conservative whole-component promotion to test. Otherwise remove the affected component from benchmark-facing test use. Retain every old assignment as historical lineage with `superseded_by`, bind the current profile ID/hash, split epoch, reconciliation-lineage hash, and benchmark registry to release identity, and publish only after global leakage plus reconciliation invariants pass. Build family/split indexes in memory and commit them only after invariant validation so failed finalization cannot partially publish split state.

From Batch 6 onward, permit `AUTO_RECONCILE_TRAIN_VAL_V1` only for an exact
`{train,val}` conflict after immutable-cohort and exact-terminal-set checks.
Write the same versioned, cohort-bound plan, promote the full transitive
component to val, and rerun FINAL. A test component follows the separately
authorized `CAMPAIGN_INTERNAL` rollover rule above; unknown split, ambiguous
closure, member loss, unresolved canonical target, or lineage cycle remains a
hard stop. Batch 5 remains an explicit reconciliation baseline.

During pipelined processing, maintain a round-local read-only
`rtl_staged_closure_audit_v1` from terminal staging artifacts. It may precompute
potential train/val and test-boundary components while acquisition continues,
but it must not publish Family/Split, Gold, manifest, or snapshot state. Rerun
the audit against the exact cohort after lock. Train/val proposals may prebuild
the normal automatic reconciliation. Test-boundary rollover is allowed only
under a separately authored, hash-bound `CAMPAIGN_INTERNAL` consumption
contract plus zero recorded downstream consumers; the factory must never
self-assert that contract. Otherwise retain the existing hard stop.
While the contract state is `CAMPAIGN_INTERNAL`, every certified intermediate
snapshot must remain externally ineligible for both training and formal
held-out evaluation. At the Family target, certify an internal candidate first,
then transition to `CAMPAIGN_FINAL_FROZEN` and build a second release identity;
never expose a final profile before both the target and certification gates pass.

Handle provider quota independently during target acquisition. Persist provider cooldown, `Retry-After`, and reset evidence; skip only the affected provider while healthy providers continue. Treat quota events as retryable provider state, never as candidate failure or bounded-retry evidence. When all providers are cooling down, remain in the nonterminal `ACQUIRING_BACKOFF` state, wait until the earliest reset with small jitter, and resume through a canary acquisition. Reserve provider quota for immutable revision resolution and acquisition: quota at or below the reserve is `QUOTA_RESERVED`, which blocks discovery but still permits acquisition. Judge frontier sufficiency from the current non-cooldown providers' acquisition-eligible frontier, not the global raw frontier. If the raw frontier is nonempty but the healthy-provider eligible frontier is empty, run provider-targeted discovery; if it remains empty, back off until the earlier of the next targeted refresh or provider reset instead of spinning empty cycles.

Use adaptive eligible-frontier low/high watermarks for target-controller throughput. Run acquisition-only cycles above the low watermark; below it, use small provider-targeted discovery microbatches without repeatedly seeding local repositories, then immediately acquire newly eligible candidates in the same cycle. Treat a successful post-reset acquisition canary as authoritative recovery and clear stale zero-quota evidence when no fresh quota header exists. On v4.3.1 resume, automatically migrate an expired shared zero-quota snapshot to `HEALTHY` only when persisted evidence already records a successful `acquisition_canary*`; never promote bare expired rate-limit evidence. Persist the active blocking stage as a controller heartbeat.

Use `rtl_discovery_evidence_v1_1` for legacy batches and activate the versioned `rtl_discovery_precision_policy_v1` only after three FINAL family-target micro-batches, beginning with Batch 4. Under that policy, `RTL_QUERY_ORIGIN`, organization siblings, and generic graph proximity describe origin/priority, not RTL content. Only direct HDL language/file/manifest/parsed-project evidence or semantic dependency/submodule/project-reference edges from a processing-confirmed RTL repository can enter the production precision lane. Emit `rtl_presence_score_v1` separately from design value and expected new-Family yield/cost; keep weak evidence in bounded exploration and place statistically proven low-yield cells in dormant state. Report split NO-RTL outcomes and per-anchor Family yield. Never lower the normal acquisition likelihood threshold globally; downstream parse, elaboration, synthesis, dedup, hash, split, contamination, license, Gold, FINAL, and completion gates remain unchanged.

Use `--seed-local` to register an existing backlog without network acquisition. Use `acquire_frontier.py --ingest-local-root PATH` to create one tracked-file snapshot per local Git revision. Read [discovery-factory.md](references/discovery-factory.md) before changing frontier tables, provider behavior, acquisition limits, scheduler scoring, or storage identity.
For an incremental resume after the local backlog is already published, add `--skip-local-processing`; the orchestrator must materialize only RepositoryRevisions absent from the central repository ledger. Keep the default per-repository wall-clock budget bounded and include its value in `run_key`.

The historical Phase-2 10,000-RepositoryRevision threshold is an acquisition milestone. The continuing corpus objective is at least 10,000 unique, provenance-complete, synthesis-valid DesignFamilies; optimize and report Gold DesignFamilies separately. Run yield recovery and data-quality maintenance concurrently without weakening discovery, acquisition, R0 processing, or generic synthesis gates.

Keep the factory boundary pure: acquisition, processing, corpus state, and
snapshot publication belong here; tokenizers, SFT/LoRA, optimizers, GPU
training, checkpoints, model selection, and inference evaluation do not. Use
three storage layers: immutable artifacts, append-only event ledgers, and a
rebuildable `state/corpus.sqlite` index feeding immutable versioned snapshots.
Published JSONL files are compatibility/materialized views, not the live source
of truth. New processing must converge toward updating only the new batch plus
affected Family and SplitGroup components.

Use `incremental_finalization_v1` for Family-campaign Batch 8 and later. Consume
the exact cohort's terminal `state/repo_runs` artifacts instead of invoking a
second processing pass, persist `round_change_set.json`, materialize only its
changed DesignIDs with admission-time immutable hashes, and run the blocking
state/manifest semantic shadow comparison for Batch 8. Ordinary rounds must not
invoke `--archive-linked-legacy`; keep that operation as an explicit
migration/repair tool. Preserve append-only finalization attempt records.

Before every incremental commit, run the read-only finalization preflight. It
must resolve terminal artifact identity, prove exact cohort/terminal equality,
normalize the complete affected Family/Split components, validate the split or
profile authorization, compute `round_change_set` commitments, and write a
hash-bound `FINALIZATION_PLAN_READY`. The commit subprocess must recompute the
same plan and refuse any changed cohort, staging artifact, materialized identity
input, ledger generation, or authorization hash. Retry only recognized transient
SQLite/I/O failures with bounded exponential backoff inside the same append-only
attempt; correctness failures are never retried. A failed attempt resumes the
same round/cohort through `attempt_000N`, never a replacement batch.

These hashes are admission-time commitments, not permission to reopen every
source file. Use `scripts/audit_artifact_integrity.py` explicitly for metadata,
sampled-byte, or full-byte maintenance audits. Use
`scripts/migrate_admission_digests.py --apply` once for legacy queues, views,
and cohort locks. Keep dynamic Family, Split, Gold, quality, and release state
in ledger/SQLite rather than rewriting static design/provenance artifacts.

Build a certified immutable snapshot with:

```bash
python3 "$HOME/.codex/skills/rtl-expander/scripts/build_corpus_snapshot.py" \
  --corpus-root "$HOME/work/data/rtl_corpus" --snapshot-id <immutable-id>
```

Rebuild the disposable SQLite index from ledger truth with
`rebuild_corpus_index.py`; this operation must leave ledger bytes unchanged and
must preserve unique RepositoryRevision identity through explicit alias rows.

Operate Phase 2 in declared, auditable production batches (approximately 500 revisions by default, or a larger explicit target such as 2,000). Capture a `factory_round_id` start snapshot and a `rtl_phase2_round_delta_v1` report, drain or explicitly account for the unpublished processing backlog, and label marginal yield provisional until the locked acquisition cohort has complete terminal processing coverage. Recalibrate discovery from finalized provider/strategy/query-family marginal yield, never from cumulative yield alone.

Materialize or migrate the human-readable catalog with:

```bash
python3 "$HOME/.codex/skills/rtl-expander/scripts/materialize_storage_layout.py" \
  --corpus-root "$HOME/work/data/rtl_corpus" --archive-linked-legacy
```

This keeps `design_id` unchanged, derives `<repo-slug>__<top-slug>__<16-hex>` from canonical identity, verifies relative source-unit hashes against the immutable RepositoryRevision, and moves linked v1 per-design copies to recoverable quarantine.

Build each contamination registry only from an explicitly supplied local benchmark source:

```bash
python3 "$HOME/.codex/skills/rtl-expander/scripts/build_benchmark_registry.py" \
  --benchmark verilog_eval --source-root /path/to/immutable/benchmark
```

Empty registry directories do not make the audit ready and must never unlock Gold/Premium.

## Stage Gates

### Discover and acquire

- Canonicalize URL variants before frontier insertion. Keep discovery events even when they resolve to an existing RepositoryKey.
- Persist queries, provider cursors/backoff, graph edges, acquisition attempts, source yield, and scheduler gaps in `frontier.sqlite`.
- Run keyword search and graph expansion over organizations, upstream/forks, dependencies, submodules, and HDL ecosystem metadata.
- Claim acquisition work transactionally. Requeue stale claims after crashes and make repeated RepositoryRevision acquisition a cache hit.
- Maintain one ledger row per repository revision and retain all discovery paths.
- Prefer public forges, HDL package ecosystems, hardware/IP organizations, sibling repositories, dependencies, and historical mirrors.
- Reweight searches toward underrepresented function, scale, hierarchy, language, interface, and source categories.
- Record discovery yield per source. Do not set a fixed terminal `target_count`; continue while novel valuable families remain.

### Recover and validate

- Produce one `DesignInstance` per independently elaboratable top and bounded meaningful configuration.
- Partition monorepositories into project/target boundaries before building module DAGs; never connect same-named modules across unrelated subprojects.
- Rank explicit synthesis tops above structured targets, local DAG roots, documentation hints, and naming heuristics. Reject testbench/formal/simulation candidates by path, name, I/O, and behavioral constructs.
- Publish a formal DesignInstance only after controlled elaboration succeeds. Retain failed top candidates in the failure ledger instead of counting them as families.
- Store `source_languages[]` and `{path, language, sha256}` source units; run the appropriate frontend per file before canonical elaboration.
- Run Q0 static, Q1 parse, Q2 elaborate, and Q3 structural checks.
- Classify failures with the taxonomy in the reference. Do not emit a bare `FAIL`.

### Repair and synthesize

- Rank repair candidates by semantic risk, repair level, equivalence confidence, AST delta, then text delta.
- Permit automatic adjudication and automatic repair, but record `ABSTAIN` when evidence is insufficient. A classification selects the next bounded action; it never proves correctness.
- Retain original and repaired representations plus a patch and tool/version/hash provenance.
- Require the ordinary parse, elaboration, synthesis, applicable equivalence, and functional gates before publishing any recovered candidate, regardless of whether its classification or repair was automatic.
- Record synthesis as `SYNTH_COMPLETE`, `SYNTH_MACRO_PRESERVED`, `SYNTH_GENERIC_ONLY`, `SYNTH_PARTIAL`, or `SYNTH_FAIL`.

### Document, deduplicate, split, and audit

- Snapshot README, docs, specs, register/protocol documentation, and comments at acquisition time. Write a `semantic_facts.json` for each design.
- Compute repository, exact-source, normalized-RTL, AST/hierarchy when available, generic-netlist, mapped-netlist, and lineage evidence.
- Cluster parameter variants, forks, mirrors, renamed copies, and minor revisions under versioned `rtl_family_v1`; store confidence and evidence.
- Assign persisted `rtl_split_v1` splits after building `rtl_split_group_closure_v1` connected components. `DesignFamily` is an identity cluster; `SplitGroup` is a leakage boundary and may contain many families.
- Union families when their DesignInstances share an exact source-unit hash, when one top occurs in another design's hierarchy within the same immutable repository revision, or when they belong to the same recovered project target. Optionally union the source organization with `--organization-aware-split`.
- Verify that every family, shared-source component, hierarchy component, project-target component, and SplitGroup has exactly one split before publication. Treat a merge of frozen groups assigned to different splits as a hard export failure.
- Audit all intended Training Exports against the benchmark registry. Keep matches in the lake and exclude them from benchmark-facing training exports.

### Score and publish

- Version scores as `rtl_eq_v1`, `rtl_fc_v1`, and `rtl_tv_v1`.
- Record license as `PERMISSIVE_CONFIRMED`, `COPYLEFT_CONFIRMED`, `RESEARCH_ONLY`, `UNKNOWN`, or `INCOMPATIBLE`, and release policy independently.
- Generate Module, SubHierarchy, TopHierarchy, and FullDesign views while preserving design/family/view identity.
- Sample family, then variant, then view; cap variants per family.
- Publish manifests by synthesis class and training tier, followed by a reproducible snapshot and summary metrics.

## Definition of Done

A round is complete only when it records:

- repository revisions and scan outcomes
- recovered instances and unique family count
- parse/elaboration/structural and synthesis outcomes
- repair levels and functional evidence levels
- exact/structural duplicate and contamination audit state
- EngineeringQuality, FunctionalConfidence, TrainingValue, and TrainingTier
- tiered manifests, failure summary, discovery summary, and a versioned snapshot
- family/split/split-group schemas, frozen assignments, SplitGroup family membership/evidence, and zero family or closure/hierarchy/project split violations
- source-unit language records, documentation snapshot, semantic facts, release eligibility, run key, stage state, and resource class
- frontier/provider cursor state, canonical repository/revision identities, acquisition safety evidence, discovery yield, and immutable repository snapshot references
- zero corrupt manifest rows, duplicate DesignIDs, duplicate RepositoryRevisions, split violations, and stale unrecoverable worker claims
- zero funnel residuals, immutable-source hash mismatches, published designs without elaboration, and storage-layout violations

Execution or synthesis success alone is not publication success.
