# Revision5：v3 skid Asset 准入的 fail-closed 修正

状态：**12/12 RAM-only 对抗检查通过；v3 source replay 兼容性得到核对，独立 lineage 未核验时 gate 明确拒绝。仍不是 TRAIN、合格 Asset、M+ 或 PILOT_TRANSFER。** 修正前 [DEV 预检](research_r5_asset_authority_preflight_20260924.md) 基于 commit `8671474`，证明同 owner 的 register/broadcast 曾被 `bound_design` 代理误计为两个 lineage；旧回执保留，不覆盖其历史结果。

现行 `memory/tehm/assets/lifecycle.py` SHA256 `bbb17aa667ff65a30e173b85d51c5d2aa64c98da2317e5e46397c3e7de1fad69`；本代 `research_r5_asset_authority_v3_gate_checks.py` SHA256 `3de2d0956893b1bc8ca42cadd1e67883b2b0d77c953cfee3c3cbd6c4a13f343c`。v3 Asset 的兼容性检查现在调用精确 `verify_skid_binding_v3` 重放注册模板、目标源码、公开参数和 bound copy；篡改 binding evidence 或缺失模板均不通过。对 v3 contract **或** v3 action domain，纯 lifecycle gate 不再从 `bound_design` 派生“独立来源”：当前没有绑定已审计的 lineage evidence，所以 `cross_lineage_verified=false`、`lineages=[]`，只把设计 ID 放入诊断 `design_ids_seen`。旧 alpha/guard 的现行规则未在这次 v3 定向修正中改写；不能据此声称已完成所有 Asset 类别的来源审计。

检查使用已观察 DEV register、broadcast、ZipCPU staged fault source，在内存 SQLite 注册 `draft` Asset。三个真实 DEV static validation 只有 `SHADOW_STATIC_PASS`，无独立功能 verdict；因此实际 gate 缺 `independent_verifier`、`cross_lineage_verified`、`regression_zero`、`rollback_verified`。同 owner 的前两项及三项合计都不再通过 lineage gate。另在**仅用于对抗测试的 RAM 副本**中把验证/保持/回滚字段合成为 PASS，并提供三个伪造的不同 lineage ID；即便如此，纯 gate 仍只缺 `cross_lineage_verified`，严格 authority ledger 的回执和冷复核均 `eligible=false`，promotion 被拒绝，Asset 保持 `draft`。这些合成 PASS 不是实验结果，未落盘、未进入 Memory。

复核入口：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_asset_authority_v3_gate_checks \
  --register-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/fault/stage/rtl/axis_register.v \
  --broadcast-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/fault/stage/rtl/axis_broadcast.v \
  --zipcpu-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-augmented-20260924-r1/fault/backpressure/stage/rtl/skidbuffer.v
```

这是**阻断错误准入的临时安全状态**，不是可产出 M+ 的完成状态。后续必须先让独立审计的来源组/关系回执与具体 source hash、角色、evidence row 绑定，并在 strict record 与冷验证两侧重算同一 lineage gate；只有取得合法 TRAIN oracle/preservation、真实回滚和至少两条合格来源时，才能另建新版软件 epoch 解除 v3 阻断。不得将本次 synthetic PASS、同 owner 两设计或 DEV augmented oracle 作为正向来源证据。
