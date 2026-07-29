# Three-Platform Fixed-Pilot Remediation Plan

## Scope

This plan covers the single new production defect confirmed while executing
the July 27, 2026 fixed Pilot at Agent commit
`ff01d5ccd62fa53e167446bcf33dce9911bda288`. It does not modify the independent
Pilot scorer, weaken strict signoff, or attempt to force physically dirty
designs to pass.

The accompanying evidence is recorded in
[the three-platform analysis](/home/yangao/r2g-skills/docs/superpowers/plans/2026-07-27-three-platform-pilot-analysis-ff01d5c.md).

The three production defects from the prior report require no further
remediation in this plan because real execution confirmed their fixes:

- globally regressive live repair is recorded as `regression` and excluded from
  positive learning;
- graph, PPA, and ingestion agree on digest-bound resumed-stage completion;
- signoff capability metadata is bound to the actual resolved environment.

| ID | Priority | Required change | Scope |
|---|---|---|---|
| RMD4-P1-01 | P1 | Make repeat `eda-install` resolve deployed pins deterministically | Small to medium |

## RMD4-P1-01: Deterministic Repeat Bootstrap

**Observed failure.** A normal bootstrap invocation did not use the existing
toolchain pins already deployed to `signoff-loop` and `def-graph`. It selected
`/opt/OpenROAD-flow-scripts` and an ambient PDK instead of the active
`/home/yangao/r2g_toolchain` ORFS and conda PDK, then attempted to install
strict collateral into the wrong read-only checkout.

**Risk.** Setup repair, upgrade, or verification can act on a toolchain that
production flows do not use. A successful operation may therefore be
irrelevant, while a failed operation may falsely imply that the active
toolchain lacks required capabilities.

**Implementation direction.**

1. Before detect/plan, inspect the existing `env.local.sh` files in both
   deployed consumer skills.
2. Validate pinned paths before admitting them as candidates.
3. If both consumers agree, use that toolchain as the default repeat-bootstrap
   target.
4. If the pins disagree, fail closed with both resolved path sets and require
   an explicit `R2G_ENV_FILE`, `ORFS_ROOT`/`PDK_ROOT`, or migration option.
5. Define and document precedence among explicit CLI/environment selection,
   a named environment file, agreeing deployed pins, and fresh-machine
   autodetection.
6. Persist the selected source and resolved path digests in
   `install_manifest.json`.
7. Run plan, installation, pinning, post-install canaries, and final capability
   verification against the same immutable resolved selection.

**Acceptance conditions.**

- Running bootstrap twice with unchanged valid pins selects the same ORFS,
  PDK, and tool binaries on both runs.
- An unrelated valid `/opt/OpenROAD-flow-scripts` checkout cannot displace the
  agreed deployed pin during repeat bootstrap.
- Conflicting signoff-loop and def-graph pins fail closed before installation.
- An explicit operator selection overrides pins only when the override is
  clearly reported and validated.
- Nangate45, Sky130HD, and Sky130HS strict postconditions are checked against
  the selected installation, and their capability records match subsequent
  production signoff manifests.
- Add a regression test that stages two valid ORFS checkouts, pins one in both
  consumer skills, and verifies that repeat bootstrap never mutates or selects
  the other.

## Limits to Preserve

Sky130HD GCD's six `m3.2` violations and Sky130HS SHA-256's physical
non-closure are not production-code defects. Keep them as fail-closed
regression fixtures:

- any DRC, route, or LVS failure blocks graph publication;
- a globally regressive repair remains negative evidence even if its target
  count improves;
- exhausted safe strategies escalate rather than relaxing protected
  constraints;
- no advisory checker substitutes for the frozen strict-signoff tier.

Nangate45 SHA-256 reached the 7,200-second full-deck DRC limit, recorded a
digest-bound `stuck` result, escalated, blocked publication, completed grading,
and left no checker process behind. Retain this fail-closed timeout policy and
investigate KLayout/deck scalability separately; do not increase the acceptance
score by weakening the deck.

## Revalidation

1. Add hermetic precedence and idempotence tests for bootstrap selection.
2. Run bootstrap twice against a disposable pair of ORFS/PDK roots.
3. Verify the persisted install manifest and all three strict capability
   canaries.
4. Run one clean fixture per platform through signoff and compare manifest
   capability paths/digests with the install manifest.
5. Re-run the unchanged fixed Pilot only if production path selection or
   generated pin contents changed.

Remediation is complete when setup, signoff, graph gating, and provenance all
refer to the same explicitly selected toolchain across repeated invocations.
