# Sky130HD SHA ABC Timing-Mapping Candidate

Status: **pre-registered development screen; no learner or lifecycle evidence.**

## Frozen Task

- Subject: `sha256_top` from
  `hanghia005/sha256-pipelined-core-with-hardware-padding`, commit
  `1d953e26e169aff2c5f1ae0d6d2ec7ef3affae04`.
- Platform/task: Sky130HD, fixed 100 MHz, fixed footprint, full strict signoff.
- Baseline witness: route, DRC, LVS, antenna and RCX clean; setup WNS
  `-0.581937 ns`. A second baseline replay must reproduce the setup-only
  failure before this screen is eligible.
- Candidate project:
  `/home/yangao/r2g_exp2_recipe_closure_2026_08_25/candidate_screens/sha_abc_timing_mapping`.
- Protected-task digest:
  `1da1a130bb4092f7e92576fb95eb435eef3c4be31c7c3c13c715664b43d968d0`.

## Candidate Effect

- Strategy: `abc_timing_mapping`.
- Sole effect fingerprint: `ABC_AREA=1 -> 0`.
- Rationale: use ORFS timing-driven ABC mapping for a moderate post-route
  setup miss while preserving the registered SDC, die/core geometry, source
  closure, routing layers and signoff check set.
- Rollback: restore `ABC_AREA=1`.

## Scope And Negative Evidence

This is a deliberately narrow scope hypothesis: Sky130HD, clean route and
physical signoff, setup-only WNS in approximately the `[-1 ns, 0)` band. It is
not a general remedy for deep combinational paths.

The same effect was already screened out on ACE2 RoPE at WNS `-3.12182 ns`:
its post-CTS checkpoint regressed to `-5.860 ns`. That evidence remains valid
and forbids broad promotion. A SHA win may justify searching for a second
independent mild-deficit family; it does not erase the ACE2 failure.

## Decision Rule

- Do not run unless the second SHA baseline reproduces an eligible setup-only
  failure under the same protected-task digest.
- Reject early if a comparable stage shows material timing regression or any
  route/DRC feasibility regression.
- A terminal strict-clean result is only a candidate success. Promotion still
  requires a second independent natural RTL family and two repeated 2A/2B
  trials with complete arm ownership, provenance and global non-regression.
