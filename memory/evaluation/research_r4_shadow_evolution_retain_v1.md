# Revision4 R4-7 Shadow Evolution：RETAIN M0

判定日期：2026-09-24。决策为 **RETAIN** `m0-route-b-003`；未生成 M1、Delta-M、shadow mutation 或 production update。这是按 [Revision4 设计](../docs/TEHM_R2G_Revision4_接口收口与真实设计Pilot方案_2026-09-17.md) §15 和 R4-7 的“无合格 signal 则 RETAIN”分支执行，不是一次演化收益实验。不能把未执行的 P13/P14、非目标回放、Delta-M 删除或 rollback 记为 PASS。

## 输入与独立验证

- 只读 M0 的 `closed_loop/tehm.sqlite` 位于仓库同级的 `tehm-campaigns/r4-real-pilot/memory/seed-readonly/m0-route-b-003/`，当前 mode `0444`，SHA256 `a9d4e83a402432e2e62e39d4dd1a51d4657bf46496ace79656353176f96d5dfd`，与 S1/S2 前的冻结指纹相同；无 SQLite `-wal`/`-shm` sidecar。bundle digest `0c35ad0319ac936eb8afd295d3f326a08d844c18758e822d35d9f85e6102ddea`。
- S0 [八案完整路由审计](../../../tehm-campaigns/r4-real-pilot/campaigns/r4-s1-memory-route-coverage-post-m0-20260923/audit/fd51674-r1/coverage.json) digest `sha256:624ae1a11274905e908da6cc2f60fb4118ddb5c7a628e0f27e362a4b412e3f3c`。两条自然 FAIL 是 `gcd / routing_congestion` 和 `verilog_axis_ll_axis_bridge / pdn_geometry_infeasible`，各自 `NO_SKILL / NO_MATCH`，但属于**不同机制**；它们是 Pilot 诊断，不是已声明可学习的同机制多来源 training transitions。
- S1 构造挑战在 AXI、USB 两个 development 来源各获得一次 `placement_density_infeasible` control FAIL → TEHM-bound treatment PASS，原始配对见 [P1–P4 报告](research_r4_pilot_tables_v1.md)；这些结果默认不回流静态 M0，且不是演化负信号。
- S2 [六任务独立核验结果](../../../tehm-campaigns/r4-real-pilot/campaigns/r4-s2-deterministic-development-20260924/s2-runs/v1-once/run-summary.json) 的三策略均 2/2 PASS；TEHM 实际候选 2/2 PASS。零次观察到成功型干扰只可写成 `0/2`；没有预注册 QoR utility，也没有完整 P12 四臂 harmful counterfactual，**不能据此证明不存在 QoR 干扰**。S2 是 development，且 Legacy 两设计均有 exact-top 历史暴露。

## Reason/admission 门禁核查

| 候选 reason | 当前证据 | 结论 |
|---|---|---|
| `STATE_SHIFT` | 没有不可迁移的 typed `StateShiftReceipt` 与绑定 `NO_SKILL/STATE_SHIFT` 的同案 route；S0 仅有 `NO_MATCH` | 不派生 reason |
| `MEMORY_INTERFERENCE` | 没有 no-memory 成功而 memory arm 已确认有害的完整 P12 paired counterfactual；S2 成功型对照均 PASS，QoR harm 未定义 | 不派生 reason；不是风险为零 |
| `CAPABILITY_GAP` | S0 的两个 NO_MATCH 不属于同一机制，每个机制仅一条；没有该机制的至少两条独立 learner-eligible failure transitions、typed gap receipt 与多来源验证 | 不派生 reason；保留待补的机制缺口 |

上述判断按 `tehm.evolution.reason_derivation` / `tehm.evolution.admission` 的输入要求逐项核查。**没有合格 reason，不能人为造 receipt 再触发 admission。** 因此本轮 P13/P14、M1 构建、target/non-target replay、Delta-M 删除对照和 rollback 均为 `NOT_RUN_NO_QUALIFIED_SIGNAL`，不是失败也不是通过。M0 保持原始只读字节；S1/S2 无在线学习。未来若取得独立 training signal，应在新 epoch 先冻结 baseline/treatment raw evidence，再按 §15.2 的完整顺序运行，不得使用 final held-out 作为 update-validation。
