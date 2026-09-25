# R5 v6 TRAIN rollback and strict RAM Asset authority gate

This phase reuses the two explicitly researcher-assisted TRAIN tasks
`axis_register` and `ZipCPU/skidbuffer`. Neither is an unseen target. The
observed SkillSurf DEV source and the earlier capture-side negative pilot
remain outside learner support. This report is not M+ construction, online
evolution, a new independent transfer, or production promotion.

## New v6 native rollback identity

At frozen code commit `6bbcb92ba63c5b008abb41248fe627d39c3c51ef`, the
v6 runner preregistered both TRAIN source/test closures and ten software
module digests under
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/asset-rollback-v6-r1`.
It regenerated each existing TRAIN candidate through the **v6** action, staged
the candidate, restored the pinned faulted source, and executed the original
target and preservation obligations in fresh native build roots. Both arms
were target FAIL / preservation PASS. The receipt then cold-replayed in a
separate process with exact equality to the saved JSON. Receipt digest:
`sha256:c54fb13d936ab36f73cf669a9d7fef43f57c87e3d054ceb9a63b7ac7a2a7917c`;
receipt file SHA-256:
`f84c406aaf3504d4ebe4bc92b880e714fb8b6a27911d9a0d5361de207f2c611f`.
This is a new v6 execution, not a renamed v5 rollback or Mremove.

## Dedicated evidence and strict RAM gate

`tehm.assets.r5_train_evidence_v6` requires the exact v6 rollback identity,
canonical TRAIN acquisition replay, distinct audited source lineages,
source-only v6 binding, independent target and preservation validation, and
exact TRAIN row metadata in the database-bound authority ledger. The pure
gate accepts only this evidence under `rtl.skid.temp_payload.v6.dev`; a
caller-provided PASS bit or a v5 rollback version does not open it.

The definitive RAM receipt is
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/asset-authority-v6-ram-r2.json`.
It passed 10/10 cases and cold-replayed byte-equivalently in a new process.
The two audited source lineages were `alexforencich/verilog-axis` and
`ZipCPU/wb2axip`. Forged rollback digest, v5 rollback generation, forged
lineage metadata, wrong profile, and non-strict promotion were rejected.
The eligible RAM authority receipt digest is
`sha256:0fa309367fb9c0b056bc3d4f303d9017374a9c6185ab6472df19d4ed21beaca4`;
the report file SHA-256 is
`77231e257b2ea1bc5883d040fdb1748502ef250e767b35ba7d82366c997e6cef`.
The Asset remained **draft** in RAM; no persistent M+ was constructed.

The initial exploratory `asset-authority-v6-ram-r1.json` passed 10/10 but
failed byte-exact cold replay because normal RAM Asset registration time was
included in the bound copy and derived digest. It was retained, not overwritten.
The r2-only check fixes its *RAM fixture clock* to
`2026-09-25T00:00:00+00:00`; production time behavior is unchanged. This
distinction matters: a functional PASS did not justify claiming a frozen
receipt until the time-dependent identity was controlled and retested.

After v6 gate integration, the missing-evidence RAM negative suite was
regenerated as `asset-core-r3.json` and cold-replayed 36/36, with cross-lineage
and rollback gates still false for forged input. Its SHA-256 is
`bd7a4897c3affd3cfdcc1eef9acd80706bc3c0eed300b316ef41aa0a7b6c69a4`.
The old v5 TRAIN authority regression also passed 10/10 after the gate change.

The next R5-4 step is a **new clean software epoch** and actual read-only
M−/M+/Mremove construction using this v6 authority, with a delta manifest
and cold restoration. The DEV transform-only control and any later fresh
target must use the same software and remain analytically separate from
Memory attribution. No model/API calls or GitHub push were made.
