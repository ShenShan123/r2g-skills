# Sky130HD Early-Place Timing Repair Candidate

Status: **terminally screened and rejected as partial-only; negative evidence
for this exact effect fingerprint.**

## Pre-registration

- Platform and target: Sky130HD, fixed 100 MHz / 10 ns SDC.
- Action-policy digest: `70d2cf9d46856b847a798aa63befabda1244ece6efd12ac38bca4a45a5c6d500`.
- Candidate strategy: `early_place_timing_repair`.
- Sole configuration delta: `ENABLE_PLACE_REPAIR_TIMING=1`.
- Mechanism: ORFS performs its documented placement-parasitic `repair_timing`
  before CTS. The clock, die/core footprint, utilization, routing layers,
  source closure, and complete strict-signoff set remain unchanged.
- Rollback: unset `ENABLE_PLACE_REPAIR_TIMING`.

## First Natural Subject

- Family: `crypto_hash_independent`.
- Source: `skaarler2005/sha256-hardware-accelerator`, commit
  `c67b70378270c27b347c21f47a7f523c1198c2c4`.
- Top/clock: `top_sha256` / `clk`.
- Explicit closure: `compression.v`, `constant_k.v`, `message_expand.v`,
  `top_sha256.v`.
- Candidate project:
  `/home/yangao/r2g_recipe_readiness_2026_08_24/projects/skaarler_early_place_timing_repair_full`.

A stage-limited screen completed normally. At detailed placement, setup WNS was
`-2.00554 ns`, compared with `-3.30022 ns` for the matched historical baseline
stage. This was sufficient to justify one fresh full strict-signoff screen, but
is not terminal timing evidence.

## Terminal Full-Flow Disposition

The clean retry used four OpenROAD cores and a 7,200-second ORFS budget. It
completed all ORFS stages and strict checkers under run ID
`RUN_2026-08-24_09-27-21_437055_2272`:

- Route, full DRC, LVS, antenna, hold, and RCX were clean.
- Setup WNS improved from the independently replayed baseline `-2.00805 ns` to
  `-1.83635 ns`, but remained negative.
- The strict graph/signoff gate consequently returned `pass_with_caveats`, not
  a strict-clean publication result.

This is a genuine partial improvement, not a global regression, but it cannot
be positive evidence because strict 10 ns setup closure is a protected gate.

The earlier interrupted run remains excluded. The terminal retry is negative
evidence only for `ENABLE_PLACE_REPAIR_TIMING=1`; do not rename or re-run this
same effect. A future combination is permissible only if it is separately
pre-registered, demonstrably non-equivalent, and independently screened.
