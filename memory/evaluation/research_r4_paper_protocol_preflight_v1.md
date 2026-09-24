# Revision4 R4-8：论文协议冻结前的门禁记录

状态：**DRAFT / NO-GO FOR FINAL TEST**（2026-09-24）。本文件不是正式 paper protocol 或 final campaign manifest；不触碰、运行或读取任何拟留出设计的修复结果。依据是 [Pilot P1–P4](research_r4_pilot_tables_v1.md) 与 [R4-7 RETAIN](research_r4_shadow_evolution_retain_v1.md)。Research Runtime RC1 已具备开发期三策略执行链，但只有两个 development 设计、两组 source lineage，三策略 task success 均为 2/2；不足以从这些结果估计论文正式样本量或宣称收益。

## 已可写入未来预注册的方法边界

- 方法必须清楚命名：现有冻结 controller 是 `deterministic_skill`，0 model call、0 token。No Persistent Memory、隔离 Legacy、只读 TEHM M0 共用 controller、候选上限 B3、同一任务 oracle 和 hard execution budget。不能将此写成 LLM Agent 结果；若将来选真实模型，须另定 provider/model、显式调用与 token 上限，再开新 epoch。
- 任务成功主指标应按全量预注册任务 `TaskSuccess@B`，把 preflight exclusion、已知硬件 FAIL 与基础设施 UNKNOWN 分开；成对 benefit/harm/neutral/unknown 和 memory-use coverage 另报。若想报告面积或时序改善，应在看到 final 结果前冻结同设计、同阶段、同约束下的 utility、可行性约束和容差；目前尚无可引用的 QoR 主指标。
- 各策略候选、预算、stop/retry、source split、审计和缺失数据处理必须在 final execution 前封存。实际 EDA/model/审计与 offline 建库成本分开；design lineage/source group 为聚类单位，同设计参数变体不算独立样本。
- Pilot 中用过的 S0 八案、S1/S2 两个构造任务、M0 两组训练来源以及 Legacy exact-top 历史暴露，不得重新贴上 unseen final held-out 标签。受控 challenge 的结果须与自然 failure prevalence、strict signoff 分开陈述；负结果保留在完整分母中。

## 当前不能冻结的事项

1. **正式测试名单及独立来源认证。** 只读 shortlist 中 `fpu_floating_multiplication` 和 `simple_i2c_slave` 仍为 `PROPOSED_NOT_RUN / declared_metadata_unverified`。它们是可继续核验的候选，不是被接受的 final split；需要来源、许可、filelist/top/dependency/SDC、可运行 scope 的只读确认，并从完整 inventory 再选更多独立来源。不得先看 final 修复或 QoR 结果再挑样本。
2. **正式任务及主指标。** S2 的 cold-start 已 2/2 PASS，task-success 在现有构造挑战上没有区分度；自然 S0 两类 FAIL 又不被当前 M0 覆盖。需要先在开发样本定义可比且有信息量的任务；若选择 QoR，必须先有预注册目标和容差。不得在 final 后改主指标。
3. **样本量与不确定性。** Pilot 只有 2 个外部 source group，不能据此宣称统计显著性或准确估计相关性。正式样本量/分层需在来源核验与任务范围确定后冻结，报告 paired unknown 并按 lineage 聚类。
4. **Agent 范围。** 当前只有零模型 deterministic controller。若论文问题要求 LLM Agent，对应 provider/model、固定调用与 token 预算以及额外开发期校准须先获单独授权；现有 P3 不能冒充那项实验。

因此 R4-8 的“正式 paper protocol、主表布局、final manifests 均已冻结”验收尚未满足；G4 当前为 **NO-GO**。安全下一步是只读核验候选来源和适配器、用 *development* 任务确定区分性与 QoR utility，然后在任何 final outcome 出现前冻结新的 protocol/split/预算并独立审计。不得把本文件升格为已冻结协议。
