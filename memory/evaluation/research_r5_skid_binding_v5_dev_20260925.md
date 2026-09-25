# R5 v5 occupied-slot skid binder: observed DEV generation

This generation extends the v4 source-only DEV binder without modifying its
contract or the frozen generation-2 TRAIN Memory. It is a *binder/action
prototype*, not a registered v5 Asset, production action, unseen transfer, or
paper attribution result.

## Bounded contract

- Implementation: `memory/tehm/evaluation/research_r5_skid_binding_v5.py`,
  contract `rtl_skid_temp_payload_binding_dev_v5`, profile
  `rtl.skid.temp_payload.v5.dev`.
- Permitted call inputs: one supplied buggy RTL source string, the exact v5
  template, and explicit public context. No path, testbench, clean source,
  mutation manifest, other-arm output, oracle result, or network input.
- New shape: one `axis_skid` module with public context
  `{"DATA_WIDTH": 8, "SKID_SLOTS": 1}`. The binder checks interface widths,
  ready/valid equations, one synthesizable sequential block, reset and active
  branches, occupied-slot drain, normal flow, stalled capture, metadata/state
  writes, preprocessor directives, and write cardinalities. The sole allowed
  edit changes the occupied-slot output payload RHS from fresh `s_tdata` to
  stored `skid_data`. Offsets and the witness digest come from the *buggy
  source*; action rebinds exact bytes before editing.
- Legacy v4 TRAIN shapes and the observed fetch DEV shape are delegated to
  unchanged v4 logic under a new v5 witness. Unsupported or ambiguous source
  is rejected. The binder proves syntactic structure only; functional success
  still requires an evaluator oracle.

## DEV evidence and limits

The observed `namangoyal-work/fpga-tick-to-trade` checkout is locked at
`ee3ef5fab4d82127c74733e037231e2c7f1f8f58`. Its preregistered
`axis_skid` negative control had scoped native target FAIL and preservation
PASS. The v5 candidate is generated from that observed faulty source and has
SHA-256 `652723dccd678e33d23547086b3efae819a9060ff4897b1391331beaca5e7253`.
It matches the previously visible clean file; this is *not* independent repair
evidence. The conformance receipt in the external pilot is
`dev/axis-skid-v5-candidate-r1/source-only-check.json`: 32/32 checks passed,
including negative structures, stale/tampered witness rejection, no file or
network access, both TRAIN candidate replays, and the observed fetch delegate.
Unchanged v4 checks remain 22/22.

The actual generated candidate was compiled with a one-file source-list
override and run against the repository's original `test_axis_skid.py` in an
isolated evaluator copy. Both native cocotb cases PASS; a fresh rerun matched.
See external `dev/axis-skid-v5-native-audit-r1/receipt.json` and its raw
`make.stdout.log`, `make.stderr.log`, and `tb/results.xml`. The unmodified
full-repository Makefile remains unqualified on local Icarus 11 because it
compiles unrelated unsupported SystemVerilog. No Memory view was used in this
v5 DEV run.

This new shape is currently only one observed development example. It is not
proof of generic axis_skid transfer or independent source lineage. The v5
module is not wired into Asset registration/selection or the production RTL
action registry. A new frozen software/Memory generation and an independently
qualified unseen target are still required before R5-5/R5-6 conclusions.

## Local verification

From this TEHM checkout (with the external pilot evidence present):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_skid_binding_v5_checks --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/axis-skid-v5-candidate-r1
PYTHONDONTWRITEBYTECODE=1 python3 /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/audit_axis_skid_v5_native.py --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/axis-skid-v5-native-audit-r1
```

The pre-v5 M−/M+/Mremove epoch is pinned to software commit `b3c5ed9`.
Verify that older epoch from that exact commit in a separate detached worktree;
do not silently rebadge it as a v5 Memory snapshot.
