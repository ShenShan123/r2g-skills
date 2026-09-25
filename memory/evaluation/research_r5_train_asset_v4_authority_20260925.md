# R5 generation-2 v4 TRAIN Asset source binding and strict authority

Date: 2026-09-25. Scope: researcher-assisted, reused DEV-as-TRAIN evidence;
no unseen transfer, online evolution, production promotion, or GitHub publication.

## Result

- The v4 source-bound Asset template now re-derives its action from only the
  target's buggy RTL and explicit public context. The already-observed
  `riscv_fetch` source is exercised as DEV only, alongside both TRAIN sources.
  The RAM-only source replay and promotion negatives pass 33/33.
- Generation-2 TRAIN Asset preflight cold-replays two raw source candidates:
  both target and preservation oracle verdicts are PASS. The tasks share
  measurement contract
  `sha256:cca722761a5980910f9dd7deedfd5dbcc473797ba285f8ac88e4f57395c666f8`
  but carry distinct oracle-instance witnesses. This step does not execute a
  fresh oracle and leaves the Asset draft.
- A new preregistered v4 source-rollback campaign restored each faulted TRAIN
  source from a pinned backup after staging the candidate. Fresh evaluator
  runs returned target FAIL and preservation PASS for both TRAIN tasks. It is
  an Asset source rollback, not Memory `Mremove`.
- Strict v4 Asset authority is eligible only in an in-memory DB after raw
  TRAIN acquisition, source-lineage, candidate validation and the new rollback
  are independently replayed. The positive and eight negative/guard checks
  pass 9/9. Forged rollback digest, forged lineage metadata and non-strict
  promotion are rejected. The Asset remains draft; no M+ was exported.
- The existing v3 strict authority check still passes 9/9. No model/API call
  or GitHub push occurred in this stage.

## Cold-replayable evidence

Pilot root: `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot`.

| Artifact | SHA-256 or receipt digest | Role |
|---|---|---|
| `memory/asset-validation-v4-preflight-r1.json` | `sha256:edc36aa40f5e390fc8652e540f2dc53b88f6440e8df9ad3d3d1ea3ccd681362e` | Reused TRAIN candidate validation |
| `training/asset-rollback-v4-r1/preregistration.json` | `sha256:bf7e0ee6e4f2c0ddb23a837f277d047b99e3d9878b5c9486fed325ec0090f6b3` | Pre-execution plan lock |
| `training/asset-rollback-v4-r1/receipt.json` | `sha256:deea70aa3d3bcf86b7515a4d31681349d3e0c94c37dcf7579a5554fc26c37fd3` | Fresh source rollback; receipt digest `sha256:9b4683b4d2fda2f08a64a7330a14a5fa4cb132e5d517580a9967461f46dace9d` |
| `memory/asset-authority-v4-ram-r1.json` | `sha256:2c350197fce29590361dffbf360999f3dffc643952391abb805a03bcd4684004` | RAM-only strict authority; receipt digest `sha256:390b20bd3a4422ee4c3ae937a674ac763d1c48c8716dc4ff4b1878f350ee3969` |

Cold verification commands (from repository root):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_asset_binding_v4_checks
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_train_asset_preflight_v2 --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/asset-validation-v4-preflight-r1.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_train_asset_rollback_v2 verify --work /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/asset-rollback-v4-r1
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_train_asset_authority_v4_checks --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/asset-authority-v4-ram-r1.json
```

## Remaining R5 gates

1. Bind the generation-2 shared measurement contract and strict TRAIN
   Knowledge/Asset authority into a new immutable M−/M+/Mremove generation.
   `Mremove` must remove the delta and its dependent state, then match M−.
2. For the already-observed fetch DEV source, preregister and validate a
   target-specific evaluator witness. Do not reuse either TRAIN oracle digest
   as a target answer or call this an unseen FINAL_TEST.
3. Freeze a genuinely independent, unobserved source lineage and its oracle
   scope before any answer-free transfer claim. Route/select/bind/execute and
   preservation must be measured under identical software and budgets across
   all three Memory views. If qualification fails, report the denominator as
   unavailable rather than changing source roles or gates.

This stage establishes TRAIN-side Asset authority mechanics and evidence, not
DeltaMemory attribution or paper-scale empirical benefit.
