# Phase 2: 10k RepositoryRevision Production

## Objective

Retain 10,000 successfully acquired unique immutable `RepositoryRevision` objects as the historical acquisition milestone. The continuing primary corpus objective is at least 10,000 unique, provenance-complete, synthesis-valid `DesignFamily` objects; report Gold `DesignFamily` count separately. Never substitute directories, candidates, files, modules, DesignInstances, or revisions for the DesignFamily completion target.

## Independent production lines

Run three lines concurrently:

- Corpus Expansion: discover, acquire, recover, elaborate, generic-synthesize, deduplicate, split, audit, score, and publish.
- Yield Recovery: evidence-strong bounded R1/R2 recovery and mixed-language frontend improvement.
- Data Quality: contamination re-audit, license evidence, ontology-confidence improvement, and scheduler calibration.

Yield Recovery and Data Quality must not block Corpus Expansion. A failure or backlog in either line may restrict an individual Training Export, but it must not stop safe acquisition or R0 Data Lake processing.

## Production priorities

Use this priority order: discovery quality, R0 processing, evidence-strong R1, targeted frontend recovery, R2, then R3.

Decompose mixed-language failures into frontend binding, vendor-library dependency, source incompleteness, interface mismatch, and invalid-language subsets. Give frontend-recovery budget only to evidence-supported frontend/binding cases. Preserve per-file languages and original sources; never flatten an unsupported mixed closure into a false single-language origin.

Run evidence-strong R1 build recovery online only for a unique repository-local missing definition, a manifest-proven omitted source/package, or unambiguous source/package ordering. Abstain before synthesis when that evidence is absent. Permit one bounded retry by default. Require unchanged original source-unit hashes and the normal parse, elaboration, structural, and generic-synthesis gates; record equivalence as `NOT_APPLICABLE` only for source-text-unchanged build recovery.

Keep R3 budget low until measured validated publication yield justifies a new policy. Never increase R3 budget merely to improve candidate pass rate.

Run generic synthesis for every publishable DesignInstance. Run technology mapping selectively for Gold candidates, high-value designs, targeted diagnostics, and periodic deterministic stratified cohorts. Do not require full-corpus mapping unless a later versioned training contract explicitly requires mapped netlists.

Freeze `rtl_function_ontology_v2`. Improve HIGH/MEDIUM evidence coverage instead of expanding categories indefinitely. Apply persisted confidence weights to scheduler diversity gain and version any later recalibration.

Run license evidence recovery automatically for every new RepositoryRevision and as a background pass over historical UNKNOWN records. Never let license recovery block expansion; let ReleaseEligibility and Training Exports enforce policy.

## Required dashboard

Always report these separate family-level counts:

- total DesignFamilies
- synthesis-valid DesignFamilies
- Gold DesignFamilies
- license-resolved DesignFamilies
- public-export-eligible DesignFamilies

Report cumulative and factory-round marginal DesignFamilies/revision and Gold Families/revision separately. A marginal value is provisional until every acquired revision in that round has a terminal processing disposition. Attribute each round by provider, strategy, and query family; preserve multi-touch discovery evidence while assigning one deterministic primary credit for conserved totals.

Process Phase 2 in declared production batches: approximately 500 new revisions by default, with larger explicit targets such as 2,000 allowed for stable runs. For an explicit target, run `run_until_revision_target.py`; do not assume that one discovery or acquisition subprocess invocation completes the target. Preserve the first `start.json` key set and hash. Count distinct RepositoryRevisionKeys, not successful-attempt rows. At the threshold, persist `TARGET_REACHED_PENDING_LOCK` before any further work, exclude ordinary acquisition workers, wait for zero active claims, and freeze every post-start success visible in one SQLite transaction as a sorted write-once `cohort_lock.json`. Overshoot is part of the cohort. Persist its file hash and hard-fail if it changes. Resume the same controller state after interruption. Distinguish provider rate limit, authentication, network, frontier exhaustion, no discovery yield, and loop-limit blocked states; never report false completion.

Before recalibrating, require exact equality between the locked key set and the cohort terminal key set, using only `NO_RTL`, `NO_DESIGN`, `SYNTH_VALID`, and `DESIGN_RECOVERED` as terminal states. Record starting/ending counts, acquisition attempts by cycle, locked acquisition-cohort processing coverage, RTL revisions, project targets, candidate tops, accepted instances, new and duplicate families, new Gold families, repository classifications/states, failure taxonomy, provider/strategy/query-family attribution, marginal LARGE+XLARGE share, marginal complex-function share, and CPU/network cost. Do not recalibrate from an incomplete cohort. Drain an older unpublished backlog explicitly and report it separately in `operational_delta`; calculate cohort DesignFamily and Gold yield only from designs attributable to the frozen revision keys. Bind exactly-once calibration to `hash(round_id + final_delta_hash)`.

If new shared-source, hierarchy, or project-closure evidence joins frozen SplitGroups, first union every raw conflict and expand historical closure to a fixed point. Emit exactly one canonical authorization per pairwise-disjoint maximal component. An exact train/val component uses a versioned `rtl_split_reconciliation_v1` plan bound to the unchanged round ID, cohort-lock hash, cohort size, and exact old group set; create a new canonical SplitGroup in val and preserve old assignments with `superseded_by` lineage. Never split the component for balance. Any component involving test requires a new benchmark/split profile or quarantine because historical training may already have contaminated the old test boundary. During an explicitly declared data-only Family campaign, only a separately authored `CAMPAIGN_INTERNAL` consumption contract plus a zero-consumer ledger audit may authorize automatic exact versioned test-profile rollover; absent that assertion, the test conflict remains a hard failure. Require zero overlaps, boundary identity edges, cross-split closure components, superseded groups without lineage, member loss, multi-split assignments, lineage cycles, and nonunique canonical targets. Construct split indexes in memory and commit them only after all publish invariants pass.

Incremental finalization must run a read-only, recorded-digest-bound preflight before any
ledger/index/manifest mutation. Resolve optional terminal-artifact revision
identity from the exact locked processing context only when the artifact omits
it; an explicit conflict hard-fails. Preflight validates cohort/terminal
equality, canonical component authorization, and a proposed round change set,
then emits `FINALIZATION_PLAN_READY`. Commit recomputes and matches that plan.
Retry only recognized transient SQLite/I/O errors with bounded backoff inside
the same append-only attempt; all correctness ambiguity remains a hard stop.

Recorded-digest-bound means using commitments created when immutable artifacts
were admitted, not reopening all source bytes. Normal rounds use metadata
integrity checks. Full byte rehash is an explicit maintenance/forensic audit;
legacy objects without commitments are `REHASH_REQUIRED` and must be migrated
outside finalization.

Mark every intermediate certified campaign snapshot as internal-only and
ineligible for external training or formal held-out evaluation. Final campaign
freeze is two-phase: certify an internal candidate after the 10,000-Family gate
and all child rounds pass, transition the consumption state to
`CAMPAIGN_FINAL_FROZEN`, then certify a second release identity bound to that
frozen state. Never infer either the initial internal-use assertion or a later
consumption transition from absence of evidence.

Treat the 10k revision target as a milestone field, not Phase-2 completion. Keep dashboard `status=ACTIVE` while the current round is not `COMPLETE`, its factory state is not `PASS`, its delta is not `FINAL`, or completion invariants are invalid. Report `revision_milestone.met` and current-round status separately.

Use the explicit family fields `fully_license_resolved_families` and `families_with_public_exportable_instance`. Define a Gold Family as a family containing at least one Gold-eligible DesignInstance. Gold family sampling must select only Gold-eligible variants before selecting a TrainingView.

Also report CPU hours/new family, functional diversity, mixed-language yield, failure taxonomy, and license status by RepositoryRevision, DesignInstance, and DesignFamily. Trend metrics are observations, not hard completion thresholds. Judge marginal scale/complexity changes after at least roughly 500 acquired revisions, not from one small cohort.

Distinguish `initial_mapping_pass_rate` from `final_mapping_pass_rate`. Preserve initial outcomes and resource-escalation history; classify persistent timeouts as `MAPPING_TIMEOUT_AFTER_ESCALATION` and never retry indefinitely.

## Definition of Done

Phase 2 completes only when all of the following hold:

- at least 10,000 unique, provenance-complete, synthesis-valid DesignFamilies exist under `rtl_family_v1`
- the historical 10,000 unique immutable RepositoryRevision milestone remains recorded separately
- duplicate RepositoryRevision and duplicate DesignID counts are zero
- corrupt central manifest rows are zero
- family split, SplitGroup, and source-closure leakage violations are zero
- immutable-source hash mismatches are zero
- unrecoverable worker claims are zero
- silent untrusted repository-code execution is zero
- provider/strategy yield, failure taxonomy, diversity, and license reports are complete
- contamination audit under the frozen active benchmark profile is complete
- Gold manifest is regenerated under all normal quality, policy, dependency, transformation, and repair gates
- scheduler is recalibrated from Phase-2 observations

Do not create an intermediate Phase 1.75. Keep Phase 2 `ACTIVE` until these hard conditions pass; background mixed-language, R1/R2, licensing, and future benchmark-profile improvements continue independently.
