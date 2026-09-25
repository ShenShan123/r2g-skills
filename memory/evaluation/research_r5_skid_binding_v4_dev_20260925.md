# R5 DEV v4: observed fetch-buffer source-only binding

This is a **new DEV generation**, developed after the bounded negative
`ue-riscv-fetch-pilot-r1` transfer. It does not revise that pilot's 0/1 verified
repair or zero DeltaMemory gain. The ultraembedded task is now observed DEV,
not unseen FINAL_TEST. No model/API call, new Memory bundle, or production
authority was involved.

## Boundary and evidence

The v4 binder keeps the three v3 TRAIN/DEV shapes and adds one tightly scoped
`riscv_fetch` fetch-buffer shape under public `SUPPORT_MMU=1`. It receives only
the already-staged faulty RTL, public configuration, and frozen draft template.
The new branch requires a unique module, exact buffer width and capture
structure, the instruction/PC/fault output relationships, exactly three
buffer/valid writes, and no unexpected preprocessor directives. It rejects
unsupported context, absent/ambiguous shape, extra writes, stale or tampered
binding. Its proof is **syntactic only**.

The r2 DEV candidate is at
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/fetch-v4-candidate-r2`.
Source-only conformance: **22/22 PASS**, including two frozen TRAIN candidate
replays. The candidate differs from the staged fault in one RHS only:
`fetch_instr_o` selects `skid_buffer_q[31:0]` while `skid_valid_q` is set.
Candidate SHA-256 is
`aadb484ee05359d72e874e30e2656350a31e56c7ccae03bffa1c94d1a2aa76b5`.

The unchanged evaluator-private research-augmented Icarus oracle, *not* an
upstream native test, gave the following raw outcomes:

| Source | Target buffered instruction | Non-target preservation |
|---|---|---|
| Previously staged fault | FAIL at assertion 7 | PASS, 10 assertions |
| v4 DEV candidate | PASS, 15 assertions | PASS, 10 assertions |

The cold audit in `dev/fetch-v4-candidate-r2/audit-r1.json` replays the
source-only checks, hashes the exact candidate and raw oracle streams, and
cross-checks the earlier pilot fault receipts. Audit SHA-256:
`95c4e9bc7327ce0cadecfc742bc63b02f211fe0f0acabfba2b4abed682065592`.
The binder code SHA-256 is
`4b53c1384c8c87149e0fb5e59130c09a7441e18090a7f04f9817fbc9030c99aa`;
conformance code SHA-256 is
`e5ef0905c625dc7b1eabd445341dd1110b544aa2704beadc2357fa78f72bdffe`.
The previous v3 48-case adversarial suite and R5 TRAIN scoped core 14-case
authority preflight also passed, without changing their frozen code.

Reproduce the source-only r2 generation only into a **new** empty DEV path;
do not overwrite this receipt:

```sh
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_skid_binding_v4_checks \
  --output /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/fetch-v4-candidate-r3
```

## Next gate

This v4 module is a DEV-only binder/action proposal; it is **not yet** a
registered Asset source-binding contract or frozen runtime. The v1 shared
measurement digest still contains TRAIN-specific oracle instances, so the
current M+ must continue to abstain on the external task. A separate
oracle-instance witness and shared semantic contract must be validated before
a new TRAIN-derived M-/M+/Mremove generation can be built. Then a *different*,
preregistered target with qualified target/preservation oracle is required
for a new transfer/DeltaMemory comparison. Replaying this observed DEV repair
through a new Memory view would be development validation, not an independent
transfer result.
