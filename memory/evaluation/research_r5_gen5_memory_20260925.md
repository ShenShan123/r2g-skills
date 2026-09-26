# R5 generation-5 TRAIN Memory checkpoint

The three-source v7 TRAIN path now has actual read-only M−, M+, and Mremove bundles. Software is frozen at `2ce921a599c406ab63331561a182d6f4f1bef8cb`, retained in `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/software/frozen-gen5-2ce921a`. Preregistered epoch: `epochs/r5-train-m0-gen5-epoch-r1.json`, SHA-256 `0ca3f5aecfd0be3d595d42e44388a73584459818b07e55ddf9e9ac0986b75886`.

The builder consumed six canonical control/treatment TRAIN records across AXIS, ZipCPU, and LibSV, three L2 controlled pairs, the core L3 replicated-effect gate, strict Knowledge authority, and the raw-replayed three-source v7 Asset gate. M+ contains validated Knowledge `mk_cd826fd01df47e5af705@1` and candidate Asset `asset_c02fe05586a06be5b5db966e`. The increment is **145 rows across 20 tables**. Mremove removes the complete TRAIN dependency closure; its relevant semantic rows equal M−, with one baseline row retained in each.

Bundles: `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/r5-train-m0-gen5-r1/`. A separate process under the frozen software worktree cold-loaded all three, rebuilt the removal closure, reconstructed Knowledge content, replayed historical Knowledge authority and current Asset authority, and repeated actual TRAIN routing/selection. The verified results are:

| View | Rows | Route | Selection |
|---|---:|---|---|
| M− | 1 | NO_SKILL | NO_SKILL |
| M+ | 146 | CONSIDER | SELECT |
| Mremove | 1 | NO_SKILL | NO_SKILL |

Build report digest: `sha256:f31072bf2c233974c9fbbf4bb4c30539942f07a57a5b6b7e9022611e90f3206b`. The exact printed cold-verifier result is preserved at `memory/r5-train-m0-gen5-r1-cold-r1.json` under the pilot root. Bundle digests are recorded there. Generation-5 Knowledge preflight passed 25/25 and independent cold recomputation; v7 Asset preflight passed 10/10 including forged-lineage ledger/cold rejection. The v7 action and draft-Asset regressions passed 18/18 and 29/29.

This closes the generation-5 TRAIN Memory construction/reload subtask of R5-4. It provides no new target-side transfer or DeltaMemory result. All three sources are explicitly researcher-assisted reused DEV-as-TRAIN, and LibSV is excluded from unseen pilot/final roles. Source-group claims remain bounded by their pinned lineage/copy audits. R5-5/6 still require a separately preregistered eligible new target and actual three-view oracle execution under this fixed software/Memory epoch. The previous negative pilots retain their original identities. No model calls, production promotion, online learning, raw-corpus publication, or GitHub push occurred.
