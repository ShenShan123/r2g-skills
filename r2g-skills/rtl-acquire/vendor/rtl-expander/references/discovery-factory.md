# RTL Discovery, Acquisition, and Storage Policy

## Pipeline boundary

Keep three independent layers plus thin orchestration:

`Discovery providers -> frontier.sqlite -> safe acquisition -> immutable RepositoryRevision -> run_expansion_round.py`

Do not add provider/network logic to the processing script. `run_factory_round.py` may invoke the layers but must not reimplement them.

For a declared acquired-revision batch target, place `run_until_revision_target.py` above these one-shot components. Persist its state after every cycle, recompute progress as the distinct post-start RepositoryRevisionKey set, and keep retry/backoff bounded. Component exit success means only that invocation completed; it does not satisfy the batch target. On threshold crossing, persist `TARGET_REACHED_PENDING_LOCK`, prohibit new ordinary acquisition, wait for zero claims, and transactionally freeze the complete sorted success set, including bounded overshoot. Treat `cohort_lock.json` as write-once and bind its hash into completion evidence. Use that lock as the FINAL marginal-yield denominator even when older backlog work proceeds concurrently.

Treat a revision target as a requested ceiling for an adaptive Family child
round, never as permission to fill with low-value evidence. A versioned early
close may lock fewer revisions only after the production frontier and the
round-global exploration allowance are both exhausted, at least five targeted
healthy-provider discovery cycles produce no production candidate, claims are
zero, and the minimum useful-cohort gate passes. Persist requested and actual
sizes plus the complete exhaustion evidence; do not rewrite the original
target. Rate-limit backoff and frontier exhaustion are mutually exclusive.

## Frontier schema

Use `rtl_frontier_v1` at `<corpus>/state/frontier.sqlite`. It must contain:

- `repositories`: canonical identity, priority, state, acquisition claim, retry, size and preliminary metadata.
- `discovery_events`: every query/graph/ecosystem path that found a repository.
- `queries`: provider, strategy, cursor, budget, priority, attempts and resume state.
- `repo_edges`: organization, dependency, submodule, fork, upstream and other graph edges.
- `acquisition_attempts`: method, revision, result, size, artifact and classified error.
- `provider_state`: pagination, rate-limit and backoff state.
- `source_yield`: candidates, acquisitions, new instances/families, synthesis-valid families and CPU hours.
- `scheduler_state`: diversity gaps and deterministic scheduler configuration.
- `repository_revisions`: one immutable acquired object per RepositoryKey and commit.

Use WAL, transactional claims, uniqueness constraints and stale-claim recovery. Export reproducible JSON/JSONL summaries at snapshots; never use published JSONL as the live work queue.

## Identity before download

Normalize HTTPS, SSH/SCP, optional `.git`, case and trailing slash variants into:

`RepositoryKey = provider:canonical_namespace/canonical_repository`

After provider metadata resolves a full immutable commit:

`RepositoryRevisionKey = RepositoryKey@commit_sha`

Multiple discovery paths create multiple events and edges but exactly one Repository row. Multiple attempts for one revision create attempt history but exactly one RepositoryRevision object.

## Providers and scheduler

Implement providers behind `DiscoveryProvider`: `search`, `get_repository_metadata`, `list_organization_repositories`, `resolve_upstream`, `resolve_forks`, and `discover_dependencies`. Initial providers are GitHub, GitLab, Codeberg, and FuseSoC discovery through `.core` metadata.

Run query discovery and graph expansion. Persist pagination before moving to the next page. On rate limit or transient failure, store retry/backoff without dropping the query or discovered candidates.

Legacy batches use `rtl_discovery_evidence_v1_1`. After three FINAL family-target micro-batches, activate the versioned `rtl_discovery_precision_policy_v1` only for Batch 4 and later and emit `rtl_discovery_evidence_v2` plus transparent `rtl_presence_score_v1`. Preserve the legacy score column as a compatibility view, but separate RTL-presence probability, design-value score, expected new-Family yield, expected acquisition/processing cost, and scheduler utility.

Only `DIRECT_HDL_LANGUAGE`, `DIRECT_HDL_FILE`, `HDL_MANIFEST`, parsed project metadata, or a semantic edge from a processing-confirmed RTL repository is RTL content evidence. Rename query provenance to `RTL_QUERY_ORIGIN`; query words never prove archive contents. Split verified graph evidence into dependency, submodule, and project-reference anchors. Organization siblings and generic references raise priority only and remain bounded exploration unless independent content evidence arrives. Merely acquired graph sources are not verified RTL sources. Strong Java/JavaScript/TypeScript/web/font/mobile/IDE-plugin/kernel-driver/compiler/simulator evidence without content evidence enters neither lane; do not infer the same from C/C++ or Python alone.

Do not globally lower the acquisition likelihood threshold to increase recall. A controlled exploration lane may consume 10–20% of attempts (15% default) for evidence-rich below-threshold candidates, excluding explicit negative evidence. Persist the acquisition lane and measure its parse/elaboration/synthesis/family yield separately. If its yield is poor, reduce that lane rather than weakening processing or publication gates.

Enforce exploration as a round-global, restart-safe hard ceiling in SQLite.
Count both acquired and active exploration claims atomically; never derive the
allowance from a worker-local or per-cycle budget. Production remains preferred
even when exploration capacity is unused. Persist executor-wide attempt
budgets separately from worker identity so idle workers can work-steal without
increasing total attempts. Treat unknown repository size as `UNKNOWN_SIZE`, not
as a small/fast repository.

Persist `admission_anchor` as `DIRECT_HDL_LANGUAGE`, `DIRECT_HDL_FILE`, `HDL_MANIFEST`, `RTL_QUERY_ORIGIN`, `VERIFIED_RTL_GRAPH_NEIGHBOR`, `GRAPH_ONLY`, `ORGANIZATION_ONLY`, or `MULTI_EVIDENCE` (with explicit fallbacks for legacy/unanchored records). The FINAL Phase-2 delta must report per-anchor acquired and processed revisions, NO_RTL rate, and DesignInstance/Family/Gold yield per revision. A cell with support above 100, NO_RTL above 95%, and Family/revision below 0.02 becomes `DORMANT` until new evidence changes its admission class. Production, exploration, and dormant are mutually exclusive.

Apply a round-global low-value subcap to `GRAPH_ONLY`: no more than 20% of the
already bounded exploration ceiling, counting active plus acquired claims in
the shared claim transaction. This is a ceiling, not a target; strong
production frontier must never be displaced merely to fill exploration quota.

Report repository outcomes as `NO_HDL_SOURCE`, `HDL_NON_DESIGN_ONLY`, `HDL_METADATA_FALSE_POSITIVE`, `HDL_GENERATED_OR_VENDOR_ONLY`, or `HDL_UNSUPPORTED_OR_UNUSABLE`; the last is recovery/failure evidence rather than proof that no RTL exists. Optimize new synthesis-valid DesignFamilies per acquisition-plus-processing cost. Treat NO_RTL as an intermediate metric, with staged goals below 60% and then 40–50%, and target Family/revision at 0.25 or better without narrowing away novelty.

Keep rate-limit state provider-aware. Persist `Retry-After`, reset time, and cooldown in `provider_state`; a cooldown excludes only its quota provider (FuseSoC discovery shares GitHub quota). Do not count HTTP 403/429 quota responses as candidate failures, bounded retries, or terminal suppression evidence. If every requested quota provider is cooling down, the target controller remains nonterminal in `ACQUIRING_BACKOFF`, waits for the earliest reset with small jitter, then uses a canary acquisition before normal throughput resumes. Reserve low remaining API quota for revision resolution/acquisition: classify a positive balance at or below the configured reserve as `QUOTA_RESERVED`, exclude it from discovery and graph expansion, but keep it available for acquisition. Use the non-cooldown acquisition-eligible frontier—not global raw frontier—as the discovery-demand signal. `FRONTIER_EXHAUSTED` means raw frontier is zero; a nonzero raw frontier with zero healthy-provider eligibility is `PROVIDER_SCOPED_FRONTIER_EXHAUSTED` and requires targeted discovery on healthy providers. If that refresh remains empty, use `ACQUIRING_BACKOFF` until the earlier of the next targeted refresh or provider reset rather than running repeated zero-attempt cycles.

Use deterministic `rtl_discovery_scheduler_v3` as a multi-objective score over new-family yield, synthesis-valid-family yield, functional diversity gain, scale diversity gain, preliminary RTL likelihood, compute/network cost, and Phase-1.5 provider/strategy priors. Do not optimize family count alone.

For the v4.3 target controller, use acquisition-eligible low/high watermarks rather than carrying the old raw-frontier threshold into eligible-frontier logic. Default to a 250-candidate refill trigger and a 1,000-candidate high-watermark objective. Refill with bounded targeted microbatches (8 queries and 4 graph expansions by default), omit repeated local seeding, and run a bounded acquisition pass immediately after a successful refill. Persist `active_stage`, its start time, and its command before each blocking component call so progress remains observable during long network operations. Clear expired zero-quota state after a successful acquisition canary; GitHub and FuseSoC still share one quota-provider record. In v4.3.1, persistently promote an expired shared zero-quota record to `HEALTHY` when and only when its latest evidence is already `HEALTHY` from `acquisition_canary*`; leave ordinary expired `RATE_LIMITED` evidence at `CANARY_READY`.

Keep each component and coefficient in persisted scheduler state. Reweight toward scarce protocols/functions, VHDL, mixed language, large hierarchy and underrepresented structures. Do not claim ML-based scheduling until measured data supports it.

## Safe acquisition

Prefer provider source archives at an immutable commit. Stream with a compressed-size bound; extract into staging with file-count and expanded-size bounds; reject absolute paths, `..` traversal, symlinks, hardlinks and special files; then atomically rename into final storage.

Never execute repository Makefiles, scripts, Python, binaries, hooks or installers. Never recurse submodules or trigger LFS smudge. Treat `.gitmodules`, FuseSoC/Bender dependencies and referenced IP as new frontier edges subject to normal policy.

The processing intake is a disposable symlink view, not storage. Rebuild it from immutable frontier revisions that are not yet present as `(RepositoryKey, commit_sha)` in the central repository ledger; remove stale intake symlinks before each rebuild. A resource-budget or tool-configuration change creates a new `run_key`, but it must not cause the acquired-intake layer to enqueue already-published revisions implicitly.

For an existing local Git backlog, snapshot only tracked regular files. Record the source local path as provenance, but treat the new immutable revision directory as the corpus original.

## Storage identity

Store original source exactly once:

`repositories/<provider>/<namespace>/<repo>/<commit>/{source/,repository.json}`

Create readable DesignInstance catalogs:

`designs/<repo-slug>__<top-slug>__<first-16-design-hex>/{design.json,semantic_facts.json}`

Derive `repo-slug` from the canonical repository URL, never from a disposable intake symlink name. The readable directory is a display path, not identity. Keep the full stable `design_id` in every manifest and `design.json`. A DesignInstance references a RepositoryRevision and relative source units. Verify every source-unit hash against the immutable revision before migrating a legacy per-design copy. Move legacy v1 directories to recoverable quarantine; do not silently delete them. Do not use `families/<family_id>` as primary storage because family algorithms may merge or split under a future schema.

## Scale-pilot gates

At 212, 1,000 and larger repository milestones, publish the full processing funnel, top classified failures, synthesis classes, repair levels, license/release distribution and discovery/acquisition yield. Require zero corrupt manifest rows, duplicate DesignIDs, duplicate RepositoryRevision objects, split violations, races and unrecoverable resume states before advancing the production milestone.

Every stage summary must satisfy `stage_input = stage_success + stage_duplicate + stage_quarantine + stage_failure + stage_skipped`. Persist every category and the residual. Keep `top_candidates_total`, `top_attempts_total`, `top_candidates_rejected`, `top_candidates_accepted`, and `design_instances_emitted` separate; historical counter gaps remain explicit audit debt rather than being arithmetically hidden.
