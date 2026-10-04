# Sky130HD ABC-Overdrive Candidate

Status: **terminally screened and rejected; negative evidence for this exact
effect fingerprint. It is not learner evidence, a lifecycle candidate, or a
promotion claim.**

## Configuration

- Platform/task: Sky130HD, fixed 100 MHz / 10 ns SDC.
- Action-policy digest: `a3eced637e5298780c77c1d591203c12d079ae7aebc1e1370b5ec950e9334260`.
- Candidate strategy: `abc_overdrive_8ns`.
- Sole configuration delta: `ABC_CLOCK_PERIOD_IN_PS=8000`.
- This is an internal synthesis mapping target, not an SDC edit. The protected
  `constraint.sdc` remains at 10 ns and all strict checks remain required.
- Rollback: unset `ABC_CLOCK_PERIOD_IN_PS`.

## Initial Natural Subject and Screen

- Family: `crypto_hash_independent`.
- Source: `skaarler2005/sha256-hardware-accelerator`, commit
  `c67b70378270c27b347c21f47a7f523c1198c2c4`.
- Top/clock/closure: `top_sha256` / `clk` / `compression.v`, `constant_k.v`,
  `message_expand.v`, `top_sha256.v`.
- The source-bound synth/floorplan screen completed with the protected 10 ns
  task. Its floorplan repair trace reached WNS `-1.122 ns`, compared with the
  current default baseline's terminal WNS `-2.00805 ns`.

The stage-only screen was not a signoff outcome. It justified one fresh,
single-owner, four-core full flow, which has now completed.

## Terminal Full-Flow Result

- Run: `RUN_2026-08-24_11-18-49_468203_795f` in a fresh source-bound
  workspace.
- The ORFS configuration contains the sole registered delta
  `ABC_CLOCK_PERIOD_IN_PS=8000`; the emitted and protected SDC remains 10 ns.
- ORFS completed; full DRC, LVS, route, and antenna were clean; hold WNS was
  `+0.432527 ns`; RCX and artifact/report binding were complete.
- Setup WNS was `-2.008050 ns`, exactly equal to the independently repeated
  default baseline. The final DEF changed at the byte level, so this is not
  classified as a configuration no-op; it is a terminal **non-improving**
  effect under the protected task.

The candidate therefore does not enter a second family, A/B, learner ingest,
or lifecycle promotion. Future timing work must use a diagnosis-justified,
non-equivalent policy-legal mechanism rather than rename this synthesis target.
