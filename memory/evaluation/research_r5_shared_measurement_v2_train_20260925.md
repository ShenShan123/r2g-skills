# R5 generation 2: shared measurement, per-source oracle witnesses

Status: **TRAIN RAM-only preflight passed; no generation-2 M+, target oracle
witness, Asset authority, or transfer result yet.** This does not alter the
generation-1 negative pilot or make the observed ultraembedded repair unseen.

## What changed

The new TRAIN scoped adapter `tehm-r5-rtl-train-scoped-v2` cold-replays the two
existing researcher-assisted TRAIN acquisitions through their original raw
verifiers. The shared contract names only the bounded obligation *one buffered
payload is delivered intact after backpressure*, the v4 research scope, and
required preservation/instance-witness policy. Its digest is
`sha256:cca722761a5980910f9dd7deedfd5dbcc473797ba285f8ac88e4f57395c666f8`.
It does **not** contain source names, native/augmented oracle kinds, test file
hashes, target case counts, or source-specific assertions.

Those details instead remain in each cold-derived oracle-instance witness:

| TRAIN source | Oracle identity and exact mapped check | Witness digest |
|---|---|---|
| `alexforencich/verilog-axis/axis_register[8-2]` | Upstream cocotb JUnit; preregistered `run_test_002` and `run_stress_test_002`; test source includes a sink backpressure generator and received `tdata`/`tid` equality assertions. Seven other preregistered cases are the preservation partition. | `sha256:407d51873042a4887e275d085545398ec17f125a92d6291e6876a1c440126ddb` |
| `ZipCPU/wb2axip/skidbuffer` | Research-augmented, evaluator-private Icarus test; a deterministic five-beat scoreboard varies `i_ready` and compares `o_data` with the expected beat under backpressure. Direct-path scenario is the preservation partition; upstream formal remains a payload-fault miss. | `sha256:6fe97dd3d6c0a38db86f4be4eea5b02babcfb12ac38cd24ca382cb5e3fff1c18` |

The witness builder checks each pinned test-source hash, expected source
assertion/backpressure anchors, exact preregistration hash, raw TRAIN receipt,
role, source revision, fault/candidate hashes, and distinct target/preservation
partition. The original raw TRAIN verifier replays all clean/fault/candidate
results and lineage locks before a v2 canonical record is built. This is a
bounded semantic mapping of two implementations, **not** a claim that their
native and augmented oracles are equivalent or that every buffered-payload
fault would be detected. The two source tests differ in stimulus and coverage.

The v4 source-only action replays the frozen candidate on both TRAIN sources.
Core scoped admission now dispatches v2 separately from v1. Its four
canonical records form two valid L2 controlled pairs and a two-source L3
causal path; strict Knowledge authority is eligible in isolated RAM. Forged
oracle witnesses or shared contracts, swapped acquisition, mismatched campaign,
outside-context replay, and held-out membership all fail closed. The previous
v1 scoped conformance also remains 14/14 valid.

## Cold-replayed evidence

`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/measurement-v2-train-ram-r1.json`
was created once, then fully recomputed and compared in a fresh process:
**20/20 checks**, `valid=true`, SHA-256
`ea20f288d88ce7efb6abfbf0d03371b511afef826cdb4755593dd64a5eb4e2b5`.
The new adapter, action, and checker source SHA-256 values are respectively
`ed061741e15dce9c711cc62f7af157b638b7223047c1017f2622b461483d0e52`,
`1177d4eddbb091bbce7a6eba79bb1af04797c8d52cbcaeaa7673397dcd476f09`,
and `3e2bc756b60b3bbd39e9a790d3dce20b12c16c9261d263ffa283e77a84dfecbd`.

```sh
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_rtl_scoped_v2_checks \
  --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/measurement-v2-train-ram-r1.json
```

## Remaining gates

- The v4 action is dispatched, but the v4 Asset binding/lifecycle and strict
  source-replay authority are not yet integrated. A RAM Knowledge preflight
  is not an exportable M+.
- No target oracle-instance witness is validated for generation 2. The shared
  digest is **not** a license to put that digest directly into a target query;
  evaluator-side qualification must bind the exact target source/test closure,
  target/preservation behavior, visibility, and preregistration before routing.
- New M-/M+/Mremove bundles, dependency removal, target comparisons, and
  independently sourced FINAL_TEST remain undone. The existing ultraembedded
  task is DEV-observed; it may test integration but not supply a new unseen
  denominator or independent DeltaMemory effect.
