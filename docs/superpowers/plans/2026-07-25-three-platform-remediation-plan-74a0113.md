# Three-Platform Fixed-Pilot Remediation Plan

## Scope

This plan covers only the three production-Agent defects confirmed by the
July 24-25, 2026 fixed four-design Pilot at commit
`74a0113286ffa6b0e890b3f87125f07bc282206d`. It does not modify the Pilot
scorer, weaken strict signoff, or attempt to force every physical design to
close.

Evidence and interpretation are recorded in
[the accompanying analysis](/home/yangao/r2g-skills/docs/superpowers/plans/2026-07-25-three-platform-pilot-analysis-74a0113.md).

| ID | Priority | Required change | Scope |
|---|---|---|---|
| RMD3-P0-01 | P0 | Reject globally regressive live repairs before learning | Medium |
| RMD3-P1-01 | P1 | Share one effective stage-evidence resolver | Medium |
| RMD3-P1-02 | P1 | Bind capability metadata to the signoff environment | Small |

## RMD3-P0-01: Globally Non-Regressive Live Repair

**Observed failure.** Sky130HS SHA-256 recorded `density_relief` as an applied
win because DRC improved from 10 to 8, although route regressed from 0 to 32
violations and LVS became `top_pin_mismatch`.

**Risk.** False-positive live evidence can increase Recipe confidence and
eventually affect ranking or promotion.

**Implementation direction.**

1. Capture a versioned pre/post result vector containing run identity, ORFS
   completion, route, DRC totals and classes, LVS, timing, RCX, and protected
   constraints.
2. Reuse one global comparator for live repair and A/B judgment.
3. Emit `win` only for a target improvement with no hard regression.
4. Emit `regression`, `no_improvement`, or `inconclusive` otherwise.
5. Prevent non-win outcomes from contributing positive Recipe evidence;
   preserve them as negative/audit evidence.
6. Restore the prior accepted configuration or quarantine the regressive run.

**Acceptance conditions.**

- DRC improves while route changes clean to dirty: verdict is `regression`;
  no positive `fix_events` or lifecycle evidence is added.
- DRC improves while LVS or timing becomes dirty: same behavior.
- A fully non-regressive improvement remains eligible for positive evidence.
- Replaying the Sky130HS SHA transition cannot learn `density_relief` as a win.

## RMD3-P1-01: One Effective Stage-Evidence Contract

**Observed failure.** The graph FLOW gate resolved Sky130HS SHA-256 as
digest-verified and ORFS-complete, while the learner stored
`orfs_status="partial"` for the same execution.

**Risk.** Graph trust decisions, PPA metadata, diagnostics, and Recipe learning
can disagree about whether a flow completed.

**Implementation direction.**

1. Extract a shared, versioned effective-stage resolver.
2. Merge local stages with parent stages only when artifact digests, design,
   platform, flow variant, and parent identity match.
3. Reject null or mutated artifacts, cycles, ambiguous parents, and
   cross-design/platform lineage.
4. Return effective status plus a machine-readable provenance explanation.
5. Use this resolver in `signoff_gate.py`, `extract_ppa.py`, and
   `ingest_run.py`; retain local-only history separately for audit.

**Acceptance conditions.**

- A valid partial resume resolves identically in graph gating, PPA extraction,
  and ingestion.
- Null, tampered, foreign, cyclic, or ambiguous lineage fails closed in all
  consumers.
- A normal same-run six-stage flow retains its existing complete result.
- The LEARNING gate agrees with FLOW for a valid digest-bound resume.

## RMD3-P1-02: Deterministic Signoff Capability Metadata

**Observed failure.** All eight final Sky130HD/HS manifests claimed LVS
capability was missing; six simultaneously declared strict clean and had
successful Netgen LVS evidence.

**Risk.** A physically valid result can still carry false platform provenance,
undermining reproducibility and downstream dataset filtering.

**Implementation direction.**

1. Resolve the canonical signoff environment once in each parent entrypoint
   before running checks or building the manifest.
2. Run platform capability preflight against that resolved environment.
3. Persist the capability result, selected tool/PDK paths, and a digest.
4. Bind the signoff manifest to that capability record.
5. Fail the manifest consistency check if `strict_clean=true` conflicts with
   selected-platform capability.

Likely entrypoints include `fix_signoff.sh` and `tools/run_signoff.sh`; the
manifest builder should consume explicit resolved data instead of ambient
environment state.

**Acceptance conditions.**

- Stale or unset ambient `PDK_ROOT` cannot change metadata when project
  `env.local.sh` resolves a valid environment.
- On Nangate45, Sky130HD, and Sky130HS, a strict-clean manifest declares all
  required selected-platform capabilities present.
- Removing real required collateral causes preflight and strict signoff to
  fail consistently; no contradictory clean manifest is emitted.

## Limits to Preserve

Nangate45 SHA-256 full-deck DRC timeout, Sky130HD GCD's six `m3.2`
violations, and Sky130HS SHA-256 physical non-closure are not by themselves
production-code defects. Keep these cases as regression evidence:

- a timeout remains `stuck/incomplete`;
- any DRC, route, or LVS failure blocks graph publication;
- no faster advisory checker replaces the frozen strict deck;
- no constraint or check is relaxed merely to improve the Pilot score.

For Nangate45 DRC, separately record design scale, GDS size, checker wall time,
last active rule, peak memory when available, and configured timeout. This is a
tool-performance investigation, not an Agent acceptance relaxation.

## Implementation and Revalidation Order

1. Implement RMD3-P0-01 first to stop new false-positive memory.
2. Implement RMD3-P1-01 and run focused full-run and resume-lineage tests.
3. Implement RMD3-P1-02 and run capability/manifest canaries on all platforms.
4. Re-run the unchanged fixed four-design Pilot from fresh campaign roots.
5. Run a separate held-out cohort for generalization; do not merge its result
   with the fixed regression score.

Remediation is complete only when the same evidence is interpreted
consistently by FLOW, SIGNOFF, LEARNING, and publication consumers, while
genuine dirty physical results remain fail-closed.
