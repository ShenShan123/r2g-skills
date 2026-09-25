# R5 RTL TRAIN scoped Knowledge preflight (2026-09-24)

Status: `RAM_ONLY_CORE_REPLAY_NOT_PERSISTENT_M_PLUS`. This is a bounded
research preflight, not a read-only Memory snapshot, Asset authority, held-out
transfer, production promotion, or paper result.

## Frozen inputs and replay

- TRAIN executions: `axis-register-skid-train-r1` and
  `zipcpu-skid-payload-train-r1` under
  `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/`.
- Source-lineage audit: `_r5_pilot/source-locks/train-lineage-v1.json`,
  audit digest `sha256:ec0f9b1d9ac4e68a097e668f6a5245cd0504c5d4b13f4f58263a049396d1dfe2`.
- Frozen preflight receipt: `_r5_pilot/memory/knowledge-preflight-r1.json`,
  file SHA256 `936548b74a2aecf2b02f37ac1fe2ae0ccc5d84b171da76782acdcb7f56a23a53`.
- Cold replay command (from repository root):
  `PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_rtl_scoped_checks --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/knowledge-preflight-r1.json`.

The adapter re-runs each frozen TRAIN verifier and the lineage audit, checks
preregistration roles and source-only action replay, and compares reconstructed
canonical records with stored transition facts. Only an isolated RAM database
with explicit training membership can enter scoped learning replay; persisted
PASS fields alone have no admission authority.

## Observed core gates

The preflight passed 14/14 checks, including file-backed admission refusal,
acquisition digest mismatch, swapped acquisition, held-out membership refusal,
and forged Knowledge authority refusal. Four distinct canonical transitions
formed two valid controlled L2 pairs. The two original repositories provided
an eligible `L3_REPLICATED_EFFECT` for the common buffered-payload obligation;
their native test implementations and test denominators are **not** asserted
equivalent. The strict Knowledge authority ledger was eligible for
`mk_af65b3bdd383c36215f0@1` on causal path
`causal_path_8e7e762e94e6b5b7`. The no-action baseline controls were excluded
from the derived intervention.

This supports a researcher-assisted, DEV-reused TRAIN mechanism claim only.
Neither case is an unseen target. The ZipCPU backpressure target uses an
evaluator-private augmented oracle, not the repository's native formal
verdict. `oracle_complete` covers only the declared target and preservation
obligations in each frozen TRAIN task, not full-design signoff.

## Unmet R5-4 and later gates

No executable RTL Asset has yet passed independent validation, audited
cross-lineage binding, and actual rollback verification. No read-only M−/M+
snapshot or dependency-complete delta manifest exists. Therefore GM, T, and
the M−/M+/Mremove attribution gate remain open. The next operation is to
record independent Asset validation and rollback evidence without treating
caller-provided booleans or the Knowledge preflight as Asset authority.
