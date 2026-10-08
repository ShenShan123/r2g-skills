# Scalable Corpus State Plane

Read this reference when changing corpus persistence, incremental identity,
snapshot certification, or large-corpus execution.

## Contract

Treat the factory as four planes:

1. Acquisition: discovery, scheduler, provider quota, immutable revision storage.
2. Processing: static classification, top recovery, frontend, repair, synthesis,
   semantic and functional evidence. Process each revision independently.
3. Corpus state: append-only events plus rebuildable indexed current state.
4. Snapshot: hard gates, Gold/public derivation, manifests, release identity.

Never add tokenizer, SFT/LoRA, optimizer, GPU, checkpoint, model-selection, or
inference-evaluation behavior. Those belong to downstream dataset-builder and
trainer systems.

## Storage authority

- `repositories/`, `designs/`, `synthesis/`, and other artifact directories are
  immutable or content-addressed assets.
- `ledger/*_events.jsonl` is metadata truth. Append facts; never rewrite history.
- `state/corpus.sqlite` is a disposable current-state index. Use WAL and short
  transactions. It must be fully rebuildable with `rebuild_corpus_index.py`.
- `manifests/*.jsonl` is a compatibility view, not a mutable database.
- `snapshots/<snapshot_id>/` is an immutable release view.

Repository aliases may share one revision but the canonical
`RepositoryRevisionKey` remains unique. Store aliases separately; never weaken
the unique revision constraint.

## Incremental publication

For a new micro-cohort, do work proportional to the new objects plus identity
components touched by new evidence:

`new revisions -> new/changed designs -> affected families -> affected split components`

Append object events and update the index in one short transaction. Materialize
compatibility JSONL and immutable snapshots after validation. Global audits may
scan the corpus when their contract requires it, but they must not hold a global
write lock while child processing or logs run.

Use Family signatures and closure edges as indexed lookup keys. Merge only the
connected components reached by new evidence and record versioned lineage:

- `FAMILY_MEMBERSHIP_ASSIGNED`, `FAMILY_MEMBERSHIP_CHANGED`, `FAMILY_MERGED`
- `SPLIT_MEMBERSHIP_ASSIGNED`, `SPLIT_MEMBERSHIP_CHANGED`, reconciliation lineage
- `DESIGN_RETIRED` for superseded current-state objects; never delete its history

Keep the strict global split-leakage audit as the final backstop.

Beginning with Family-campaign Batch 8, use `incremental_finalization_v1`.
The exact locked cohort must resolve to an exactly equal terminal processing
set, and every queue row must bind an immutable `state/repo_runs/<run_key>.json`
artifact with matching revision and terminal-state identity. Finalization
consumes those artifacts directly; it must not rerun repository classification,
frontend, elaboration, or synthesis. Persist a round-local
`rtl_round_change_set_v1` containing changed/retired DesignIDs and affected
Family/SplitGroup IDs. Materialize only that changed DesignID set and never run
`--archive-linked-legacy` as an ordinary round stage.

The compatibility manifests and certified snapshot may still be emitted as
full sequential views. Batch 8 must run a blocking read-only semantic shadow
comparison between `corpus.sqlite` and the compatibility views for DesignID,
Family, Split, Gold, license, contamination, and logical corpus identity.
Preserve every finalization retry under a versioned attempt ID; the canonical
round completion may point to the successful attempt but must not erase older
failed attempts.

Run finalization as `PREPARE -> COMMIT`. `PREPARE` is read-only with respect to
ledger/index/manifests: resolve all revision identities, prove exact
cohort/terminal equality, normalize maximal transitive Family/Split components,
validate reconciliation/profile authorization and all publish invariants, and
emit a hash-bound `rtl_finalization_preflight_plan_v1` in state
`FINALIZATION_PLAN_READY` with a `round_change_set` preview. `COMMIT` must
recompute the plan and require the same cohort, staging-artifact hashes,
materialized identity-input hashes, ledger generation, and plan hash before its
short write transaction. Never discover a repair while partially committing.

Treat terminal-artifact identity independently from an optional DesignInstance
payload. The cohort-lock revision key, exact processing-queue row, nonempty
queue run key, artifact payload run key, and run-keyed artifact filename must
all bind. A legacy `NO_RTL` or `NO_DESIGN` artifact may omit its own revision
key; in that case only, backfill it from the locked processing context. If the
repository or any DesignInstance explicitly asserts another revision key,
hard-fail identity validation. Never infer identity from cohort array position
or an unrelated artifact filename.

Treat split evolution as two different operations:

- `TRAIN_VAL_RECONCILIATION`: a deterministic `{train,val} -> val` component
  promotion may remain in the current profile when its cohort and lineage gates
  pass.
- `TEST_BOUNDARY_INVALIDATION`: any closure component crossing `test` defaults
  to a hard stop. It requires an explicit versioned split-profile transition, a recorded
  downstream-consumption audit, exact full-component authorization, and a new
  release identity. A separately authored `CAMPAIGN_INTERNAL` contract plus a
  zero-consumer audit authorizes automatic rollover and conservative promotion
  of the whole component to `test`. Otherwise do not reuse it as
  benchmark-facing test data.

Keep superseded profiles and SplitGroups as history. A current profile must be
unique, and release identity must bind its profile ID/hash, split epoch,
reconciliation-lineage hash, and benchmark-registry hash.

## Pipelined revision processing

`cohort_lock.json` is a publication barrier, not a processing-start barrier.
During acquisition, atomically persisted RepositoryRevisions feed a mutable
`processing_queue` in `state/corpus.sqlite`. Independent bounded workers may
produce classification, top-recovery, source-closure, frontend,
parse/elaboration, generic-synthesis, semantic, repair, quality, and
per-design contamination-fingerprint artifacts. Use a deterministic run key
covering revision identity, schemas, tool versions, build configuration, and
repair policy; a valid existing artifact is a cache hit after restart.

Queue states and worker claims are operational and rebuildable, not ledger
truth. Append only durable revision-local facts such as `REVISION_ACQUIRED`,
`PROCESSING_STARTED`, and `PROCESSING_TERMINAL`. Before cohort lock, do not
append final Family/Split membership, Gold/publication, snapshot, or FINAL
yield events and do not update compatibility manifests.

The production state sequence is:

`ACQUIRING_PIPELINED -> TARGET_REACHED_PENDING_LOCK -> COHORT_LOCKED_DRAINING -> RECONCILING -> FINALIZING -> COMPLETE`

An adaptive Family campaign may also close a useful child cohort below its
requested revision ceiling through
`PRODUCTION_FRONTIER_EXHAUSTED_PENDING_CLOSE`. Preserve the requested target
and record the actual cohort size separately. This path requires zero
production-eligible frontier, zero remaining exploration capacity, at least
five consecutive healthy-provider targeted-discovery cycles with no new
production candidate, zero active acquisition claims, and a minimum useful
cohort (default: both 1,000 revisions and 75% of the request). Rate limiting
never proves exhaustion. Stop claims, require exact equality between the
acquired and terminal processing key sets, then write the ordinary immutable
cohort lock with `early_close=true` and reason
`ELIGIBLE_PRODUCTION_FRONTIER_EXHAUSTED`. FINAL and every completion invariant
remain unchanged.

After the target, stop acquisition claims, wait for active claims to reach
zero, freeze the complete overshoot set, and drain only that set. Completion
requires exact key equality between terminal processing rows and cohort keys;
matching counts are insufficient. Then reconcile affected Family and
SplitGroup components and retain the existing global leakage audit as the
backstop. Keep acquisition, general processing, and resource-heavy synthesis
concurrency independently bounded so large jobs cannot consume every worker.

Use `bounded_parallel_acquisition_v2` only at the candidate-to-immutable-
RepositoryRevision boundary. Preserve Batch 5 as the sequential performance
baseline. V2 uses one SQLite-backed executor attempt budget shared by persistent
workers; it must never partition capacity into per-worker budgets. Default to
three `FAST_KNOWN`, one `UNKNOWN_SIZE`, and one reserved `SLOW_KNOWN` worker.
Workers atomically claim one repository, commit its outcome, and immediately
claim again. Controlled stealing may cross lanes only after the worker's home
lane is empty; the slow worker reservation prevents high-value large RTL from
starvation. Keep per-repository wall clocks and all archive/extraction gates.

The round owns a restart-safe `round_acquisition_budget`. Exploration is a hard
ceiling, not a quota: production is always preferred, and an exploration claim
is allowed only when `exploration_acquired + exploration_active_claims` remains
below the round cap in the same `BEGIN IMMEDIATE` transaction that claims the
repository. A hot upgrade retains prior acquisitions; if the historical count
already exceeds the cap, all future exploration claims are denied. Parallel
acquisition must drain active children before the controller may take the
exclusive cohort lock. None of this changes child-round targets, overshoot,
processing queues, Family/Split identity, Gold, FINAL, or certification.

Within that ceiling, `GRAPH_ONLY` is a low-value evidence cell and may consume
at most 20% of the round exploration cap (with one claim allowed for a nonzero
small cap). Prefer other exploration anchors first. Count acquired plus active
claims atomically and retain historical over-cap acquisitions; never revoke an
immutable revision to repair a budget-accounting upgrade.

For Batch 6 and later, `AUTO_RECONCILE_TRAIN_VAL_V1` may recover finalization
when the detected split set is exactly `{train,val}`, the
cohort is already immutable, acquisition and processing claims are zero, and
the terminal revision set exactly equals the cohort. Generate a round-bound,
cohort-hash-bound reconciliation plan for the complete reported transitive
component, promote it wholly to `val`, retain all supersession lineage, then
rerun every global split and publication invariant. A test component may use
the separately authorized campaign rollover below; unknown split, ambiguous
component, missing member, unresolved terminal target, or lineage cycle remains
a hard stop. Audit `split_lineage_cycles == 0` and exactly one
terminal canonical target for every superseded group.

## Staged closure auditing

During pipelined acquisition, periodically coalesce newly terminal revision
artifacts into a read-only `rtl_staged_closure_audit_v1`. Compute prospective
Family signatures, shared-source, hierarchy, and project-closure components
against the current published indexes. Persist potential train/val merges,
test-boundary invalidations, affected historical groups/families/designs, and a
proposed canonical component in the round directory. This audit must never
write Family/Split membership, Gold/public manifests, snapshots, or FINAL
events. At cohort lock, rerun it over the exact terminal cohort and treat the
result only as a proposal; ordinary reconciliation validation and the global
leakage audit remain authoritative.

An exact train/val staged proposal may prebuild the existing cohort-bound
`AUTO_RECONCILE_TRAIN_VAL_V1` plan. A test-boundary proposal may prebuild
`AUTO_SPLIT_PROFILE_ROLLOVER_V1` only when a separately authored and hash-bound
`rtl_split_profile_consumption_contract_v1` says `CAMPAIGN_INTERNAL`, explicitly
prohibits external training/evaluation consumption for that campaign, and the
corpus ledger records no consumer/pinning event. The factory must never create
that assertion for itself. Without the contract—or after any training,
evaluation, or profile pin—the test conflict remains a hard stop. Every
rollover advances exactly one version, preserves old profiles and lineage,
reruns global leakage/publication gates, and binds the new profile to release
identity.

Bind that contract to the signing-time split-profile record/manifest, immutable
campaign-controller identity, logical corpus index, ledger hashes, and latest
release identity. Mutable controller heartbeats and later append-only ledger
growth do not invalidate the assertion; the current profile must remain in the
authorized profile lineage. Every intermediate certified snapshot must say
`consumption_scope=CAMPAIGN_INTERNAL` and set both external training and formal
evaluation eligibility to false. At the Family target, first certify an
internal final-candidate snapshot. Only after that certification may the
consumption state become `CAMPAIGN_FINAL_FROZEN`; then build a second final
snapshot whose release identity binds the frozen state. Any later external pin,
training use, or formal evaluation use is an explicit one-way state transition
and restores the hard stop for future test-boundary changes.

## Write ordering and failure safety

1. Write immutable worker artifacts and hashes.
2. Validate the affected objects/components.
3. Durably append events, then commit their materialized index transaction.
4. Materialize compatibility views.
5. Run release gates and build a temporary snapshot directory.
6. Bind release identity and atomically rename the snapshot.

On a blocking failure, stop all downstream mutating stages. Do not regenerate
Gold, dashboards, or central manifests from partial state. Preserve logs and the
failed stage result. A crash after ledger append but before index commit is
recovered by indexing the ledger tail; event IDs make replay idempotent.

Recognized transient SQLite lock/busy and operating-system I/O interruptions
may retry with a small bounded exponential backoff inside the same round and
`attempt_000N`. Exhaustion becomes a normal failed attempt. Never classify
identity mismatch, cohort/terminal mismatch, unknown split, lineage cycle,
member loss, nonunique canonical target, or leakage-audit failure as transient.
Every later finalization retry appends a new attempt record and reuses the same
round/cohort/staging; it must not create a replacement batch.

The parent Family controller automatically resumes the same child after a
nonzero process exit only when the persisted child state is explicitly
nonterminal/recoverable (`ACQUIRING*`, pending lock, draining, reconciliation,
or finalizing). Use bounded backoff and retain exit history. Missing/unreadable
child state and every correctness failure remain a hard stop, never an infinite
retry loop.

Stream child stdout/stderr to stage log files. Keep only bounded tails in
controller JSON and update child PID, heartbeat, last output, and progress time.

## Hash-on-admission policy

Use `HASH_ON_IMMUTABLE_ADMISSION_V1`: compute SHA-256 exactly when a new
immutable object is admitted or regenerated. Store object ID, digest, byte size,
schema, producer, and generation in the ledger or an admission receipt.
Ordinary rounds, preflight, FINAL, and certification compare these recorded
commitments; they must not rescan the corpus source tree.

Rehash only new or explicitly regenerated bytes, a byte-producing migration,
suspected mutation, or an explicit integrity audit. Use
`migrate_admission_digests.py --apply` once for legacy objects. Use
`audit_artifact_integrity.py --mode metadata|sample|full-rehash` outside the
normal critical path. Missing commitments are `REHASH_REQUIRED`; finalization
must never reconstruct them silently.

Static design/provenance bytes must not be rewritten merely because Family,
SplitGroup, Gold, quality, profile, or release membership changes. Those facts
belong in ledger/SQLite. Legacy mutable `design.json` conversion requires an
explicit versioned migration, never an implicit round-time rewrite.

## Release identity and certification

Bind every snapshot identity to at least:

- pipeline/schema and skill source hashes
- policy and quality-policy hashes
- logical corpus-index hash and ledger hashes
- split profile/epoch
- benchmark registry and contamination-audit hashes
- Gold manifest and materialized-manifest hashes

Certification requires zero funnel residual, zero unquarantined unresolved
provenance, a current zero-match contamination audit, and all existing dedup,
source-hash, synthesis, split, Gold, and publish invariants.

## License separation

Maintain three explicit products:

- Raw Data Lake: all acquired and historical evidence.
- Internal training corpus: synthesis-valid, provenance-complete designs allowed
  by internal policy, including license-unknown material only when policy allows.
- Public release corpus: provenance-complete and `PUBLIC_EXPORT_ALLOWED` only.

Unknown license never blocks safe expansion, but it must never leak into the
public snapshot. Report license status by revision, DesignInstance, and Family.

Functional confidence is also a derived-subset dimension, not a base Family
admission gate. The Formal Corpus requires provenance, parse, elaboration,
generic synthesis, deduplication, and split cleanliness; derive a separate
F2/F3/F4 functional-verified subset and promote evidence asynchronously.

## Scale target

The hard growth controller metric is the global count of unique,
provenance-complete, synthesis-valid `rtl_family_v1` DesignFamilies. Revision
targets are adaptive bounded micro-cohorts. At 10k/50k/100k revisions, tune batch
size from measured Family yield and affected-component cost; never declare corpus
completion from RepositoryRevision count alone.

Treat LARGE/XLARGE share as a nonblocking quality objective. Maintain an
independent size-aware discovery lane whose priority uses provider repository
size, direct HDL language/file evidence, HDL manifests/core count, dependency or
submodule edges, and—after acquisition—HDL LOC/file/hierarchy evidence. Scale
evidence changes priority, not RTL likelihood, and must never weaken the base
Family gate.
