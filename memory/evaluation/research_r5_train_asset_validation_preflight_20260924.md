# R5 RTL TRAIN Asset validation preflight (2026-09-24)

Status: `TRAIN_REUSED_DEV_ASSET_VALIDATION_PREFLIGHT_ONLY`. No Asset authority
was recorded, no status changed from `draft`, no M+ was constructed, and no
held-out transfer or fresh oracle execution occurred.

The RAM-only runner `tehm.evaluation.research_r5_train_asset_preflight`
cold-replays the two frozen TRAIN executions, registers one draft v3 skid
rewrite template, independently rebinds it from each faulted RTL source and
public context, executes the parser-backed action, and compares its exact
candidate bytes to the previously executed TRAIN candidate. The target and
preservation verdict callbacks are only accepted for that exact candidate;
the faulted source is rejected as a wrong candidate. The verifier is
independent of the rewrite generator, but it **reuses** the TRAIN test runs;
it is not an additional native rerun.

Frozen receipt: `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/asset-validation-preflight-r1.json`,
file SHA256 `c83a83e5caf3ec00980a4bdbc7b055315d08227a659e6646e8f4265709832379`.
Cold replay:

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_train_asset_preflight \
  --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/asset-validation-preflight-r1.json
```

All six preflight checks passed. The pure lifecycle gate sees schema,
static rewrite, independent TRAIN verifier replay, compatibility, and
preservation as true. It correctly refuses Asset eligibility because
`cross_lineage_verified=false` and `rollback_verified=false`. Although the
separate source audit identifies two distinct bounded TRAIN source lineages,
the Asset lifecycle and strict ledger do not yet bind that audit to specific
validation/binding evidence rows. A caller cannot fill that gap by attaching
lineage strings or `verified=true`; actual audited binding and rollback
replay must be implemented and checked first.

The ZipCPU target remains an evaluator-private augmented oracle, not a native
formal PASS. This preflight is researcher-assisted TRAIN reuse, not an unseen
target, full RTL correctness claim, or paper effect estimate.
