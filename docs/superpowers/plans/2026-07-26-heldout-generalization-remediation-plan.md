# Three-Platform Held-Out Remediation Plan

## Decision

The held-out experiment confirms useful cross-design capability but does not
justify declaring the Agent complete. No new P0 defect was found. Two known P1
consistency defects were independently reproduced, and Nangate45 remains
limited by full-deck DRC scaling.

Production work should remain narrow: fix the two shared-state contracts,
characterize the Nangate45 checker limitation, investigate the platform-
specific AES LVS result, and improve evaluation coverage without weakening
strict signoff.

## P1-HO-01: Unify Effective ORFS Stage Evidence

**Observed case.** Sky130HS AES passed FLOW through digest-verified resumed
lineage but failed LEARNING because ingestion stored ORFS as partial.

**Required change.**

1. Define one versioned effective-stage resolver for local and inherited ORFS
   evidence.
2. Admit inherited stages only when artifact digest, design, platform, flow
   variant, and parent run identity match.
3. Reject null, tampered, ambiguous, cyclic, cross-design, or cross-platform
   lineage.
4. Use the same resolved result in graph gating, PPA extraction, ingestion,
   diagnostics, and learner statistics.
5. Preserve local-only stage history separately for audit.

**Acceptance.**

- A valid floorplan resume returns the same `complete` status in FLOW, PPA,
  ingestion, and LEARNING.
- Invalid lineage fails closed in every consumer.
- Full same-run six-stage flows retain their present result.
- Replaying the held-out Sky130HS AES lineage no longer creates a FLOW versus
  LEARNING disagreement.

## P1-HO-02: Bind Capability Metadata to the Executed Environment

**Observed case.** Eight Sky130HD/HS manifests declared LVS unavailable even
though Netgen ran; seven simultaneously declared strict clean.

**Required change.**

1. Resolve the canonical signoff environment once in the parent entrypoint.
2. Execute capability preflight and child checks under that environment.
3. Persist the resolved tool/PDK paths and capability result with a digest.
4. Build the signoff manifest from that record, never from ambient paths.
5. Reject a manifest whose `strict_clean` result contradicts required platform
   capability.

**Acceptance.**

- Stale or unset ambient `PDK_ROOT` cannot alter metadata when project
  `env.local.sh` resolves the valid environment.
- Every clean Nangate45, Sky130HD, and Sky130HS canary reports all required
  capabilities present.
- Actually removing required collateral fails both preflight and signoff.

## LIM-HO-01: Characterize Nangate45 Full-Deck DRC Scaling

**Evidence.** PicoRV32, AES, and ChaCha reached the 7,200-second bound at
`FreePDK45.lydrc:131`; SERV completed in approximately 187 seconds.

**Actions.**

1. Preserve the three frozen GDS files and deck/tool digests.
2. Record GDS size, instance/polygon counts, peak memory, elapsed time, and last
   completed rule for every run.
3. Reproduce rule 131 outside the Agent to separate KLayout, deck, and layout
   scaling.
4. Compare a supported newer KLayout and any official deck revision against
   the same GDS inputs and complete rule set.
5. Keep the current bound and `stuck/incomplete` classification until an
   equivalent faster configuration is independently demonstrated.

**Acceptance.** The full deck either completes within the frozen budget or
continues to produce an honest bounded failure. BEOL-only, skipped, or relaxed
checks do not count as strict signoff.

## LIM-HO-02: Diagnose Nangate45 AES LVS Mismatch

**Evidence.** Nangate45 AES reported 20 mismatches while the identical pinned
RTL was LVS-clean on Sky130HD and Sky130HS.

**Actions.**

1. Preserve the GDS, extracted and schematic netlists, rule digest, and run ID.
2. Reduce the mismatch to named nets/devices or symmetric groups.
3. Classify it as implementation, extraction, or rule-deck behavior before
   defining a Recipe.
4. Validate any intervention through unchanged DRC, timing, constraints, and
   LVS.

**Acceptance.** The cause is reproducible and supported by concrete net/device
evidence. No ambiguous failure is ingested as a successful repair.

## EXP-HO-01: Strengthen Generalization Measurement

The present cohort establishes feasibility but is not paper-scale evidence.

1. Freeze a larger, size-stratified corpus with complete source closure.
2. Balance CPU, cryptographic, control, datapath, bus-heavy, single-file, and
   multi-file designs.
3. Add two evaluation modes:
   - **frozen memory:** reset knowledge before every design to measure
     independent generalization;
   - **continual memory:** preserve within-campaign learning to measure online
     improvement.
4. Evaluate autonomous discovery separately from fixed-list ingestion.
5. Report per-platform strict-clean yield, correct fail-closed yield, time,
   timeout incidence, repair count, and LLM cost.

**Acceptance.** Every source is pinned before execution and receives one
terminal classification: strict-clean publication, correct rejection, or
unexpected Agent failure. Platform and evaluation-mode denominators remain
separate.

## Regression Set

Retain the following outcomes:

- Sky130HD must continue to publish all four held-out fixtures.
- Sky130HS PicoRV32, SERV, and ChaCha remain clean publications.
- Sky130HS AES remains blocked while residual DRC exists.
- Nangate45 SERV remains clean and publishable.
- Nangate45 bounded DRC cases terminate and never publish.
- The incomplete ZipCPU DMA closure remains rejected during qualification.
- Capability manifests and resume-state consumers receive dedicated regression
  tests after their production fixes.

## Order

1. Fix effective-stage consistency.
2. Fix capability-manifest environment binding.
3. Run focused resume and capability canaries.
4. Re-run the unchanged three-platform held-out cohort.
5. Investigate Nangate45 checker and AES LVS limitations independently.
6. Freeze the expanded frozen-memory and continual-memory experiment design.

This order improves evidence integrity first while preserving genuine physical
failures and the current strict publication policy.
