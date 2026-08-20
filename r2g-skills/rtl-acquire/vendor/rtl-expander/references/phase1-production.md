# Phase 1: 1,000 Immutable RepositoryRevisions

## Target and baseline

Complete Phase 1 only after the corpus contains at least 1,000 successfully acquired unique `RepositoryRevisionKey` objects. Count existing immutable revisions; never reset or substitute directory count, RepositoryKey count, or discovery-event count.

Run discovery/acquisition and benchmark-registry construction independently. An unavailable registry permits Silver Data Lake expansion but forbids Gold/Premium promotion.

## Required funnel

Report these non-interchangeable quantities:

`discovered candidates -> canonical RepositoryKeys -> unique RepositoryRevision candidates -> acquired immutable revisions -> RTL-containing revisions -> top candidates -> top attempts -> elaboration-valid DesignInstances -> synthesis-valid DesignInstances -> unique DesignFamilies -> SplitGroups`

For every stage enforce:

`stage_input = stage_success + stage_duplicate + stage_quarantine + stage_failure + stage_skipped`

Store all terms and `residual`; require `residual == 0`. Keep local-intake cohorts separate from cumulative network discovery so counts such as local directories, repository ledger rows, and acquired revisions are never presented as one causal chain.

Keep `top_candidates_total`, `top_attempts_total`, `top_candidates_rejected`, `top_candidates_accepted`, and `design_instances_emitted` separate. Record parameter variants and retries explicitly. Never infer one field by subtracting unrelated counters.

## Scheduler and yield

Score sources with `rtl_discovery_scheduler_v2` using new-family yield, synthesis-valid-family yield, functional diversity gain, scale diversity gain, and compute/network cost. Report provider and strategy separately for keyword, organization, upstream/fork, dependency, submodule, and FuseSoC discovery.

Record queries, repositories seen, new RepositoryKeys, new revisions, acquired revisions, new DesignInstances, new families, synthesis-valid families, CPU/tool hours, and network bytes. Report revisions per synthesis-valid family and CPU hours per new family. Use fractional or influenced attribution explicitly when multiple discovery paths contributed; never imply exclusive attribution unless it is enforced.

Generate language, resource-class, function, module-count, hierarchy, RTL-size, clock, memory, macro, and mapped-cell distributions. Mark unavailable metrics rather than fabricating them. Freeze `rtl_storage_layout_v2` throughout Phase 1 except for correctness fixes; retain recoverable v1 quarantine and unresolved-provenance legacy designs.

## Definition of Done

Require all of the following:

- at least 1,000 unique acquired RepositoryRevisions
- valid central manifests and zero duplicate RepositoryRevision or DesignID
- zero family, SplitGroup, and source-closure leakage violations
- zero immutable-source hash mismatch
- zero stale unrecoverable worker claim
- zero detected repository-owned script execution
- zero published design without elaboration
- crash resume, same-run-key idempotency, atomic publication, and `rtl_storage_layout_v2` invariants pass
- complete conserved funnel, failure taxonomy, provider/strategy yield, and diversity/scale reports

For failures report reason, count, percentage, observed CPU/tool hours, recoverability, and estimated candidate gain. Prioritize frequent, recoverable, low-semantic-risk fixes; never weaken the DesignInstance gate to improve pass rate.

Maintain two headline dashboards: total Unique DesignFamilies, and Training-Ready DesignFamilies satisfying uncontaminated, synthesis-valid, engineering-quality, and release/training-policy gates. While the benchmark registry is unavailable, show Training-Ready as blocked rather than treating an empty registry as clean.
