\# Experiment 2 Recipe Readiness Goal

## 目标

在冻结的 `Sky130HD + 100 MHz + Default ORFS + strict signoff` 条件下，补齐当前 Full R2G 缺少的高价值 Recipe，并用独立任务证明其可迁移性，使系统达到实验二的启动门槛。

本阶段允许 Terra、人工分析和网络资料辅助**提出候选策略**，但候选只能在 development 数据上产生。最终能力验证必须由冻结后的 Full R2G 独立运行，不调用外部 LLM、不接受人工干预。

## 当前起点

- 现有五任务 Full-R2G 记录为 `3/5 strict-clean`，但其中 `serv` 的
  `density_relief` 将 `CORE_UTILIZATION=25` 改为 `17`，而
  `pin_perimeter_floor` 也会增大 die/core。它们只能作为“有边界的面积恢复”
  轨道成绩，**不能**直接计入本文件定义的 fixed-footprint 成绩；详见
  `2026-08-24-fixed-footprint-integrity-audit.md`。
- `pin_perimeter_floor`、`density_relief` 等已有 Recipe 可以解决部分 pin/DRC 问题。
- USB 与 Hazard3 仍为右侧边缘 `m3.2` 问题；现有 `pin_side_rebalance` 可能可迁移，但它属于**已有 Recipe 的 scope 扩展**，不能计作新 Recipe。
- broad setup timing 仍是主要 catalog gap；已有 ABC、hierarchy 和 `SETUP_SLACK_MARGIN=0.2` 筛选尚未形成 strict-clean promotion，不得改名后重复试验。
- 本地 A/B 路径隔离修复及其测试已经存在；后续 evidence 必须绑定当前 campaign 的真实 arm 目录。

## 两条工作轨道

### Track A：提高现有 Catalog 的覆盖率

对于“已有 Recipe 效果可能有效，但 lifecycle scope 不匹配”的失败：

1. 在新的、独立 RTL family 上验证该 Recipe。
2. 通过严格 A/B 后，只扩展它的适用 scope 或 predicate。
3. 记录为 scope-transfer，不计作“新增 Recipe”。

优先检查右边缘 `m3.2` 与 `pin_side_rebalance` 的可迁移关系。固定五任务只能用于最终回归，不能用于生成 promotion evidence。

### Track B：产生真正的新 Recipe

对于当前 catalog 没有等价 effect fingerprint 的稳定失败：

1. 根据日志、报告和物理机制提出新的受限动作。
2. 每个 failure mechanism 最多筛选三个非等价候选。
3. 只有通过独立严格 A/B 的新 effect 才算新增 Recipe。

优先级为：

1. 可迁移的 residual DRC / detailed-route interaction。
2. route congestion 或 route non-convergence。
3. broad setup timing，但不得放宽 10 ns 时钟。
4. 有足够自然样本时再处理 PDN、antenna 或其他稀有问题。

## 执行清单

### Phase 0：冻结与安全检查

- 记录 Git commit、dirty patch digest、知识库 digest、ORFS commit、PDK/tool versions、action-policy digest。
- 固定 `Sky130HD`、10 ns、die/core footprint、完整 DRC/LVS/route/antenna/timing/RCX 检查集合。
- baseline manifest 必须绑定 `DIE_AREA`、`CORE_AREA` 或 `CORE_UTILIZATION` 的
  初始几何表达；final evaluator 必须比较其 canonical geometry digest。任何
  geometry delta 在本 fixed-footprint track 中均为不合格，不能只因 signoff
  clean 而计为 repair success。
- 每个 flow 固定 4 cores；并发重型 ORFS 不超过 2 个，避免资源竞争污染结果。
- 运行 A/B isolation、provenance、lifecycle 和 judge 相关测试。
- 代码、知识或工具链一旦变化，受影响 evidence 必须重跑，不能跨版本拼接。

产物：`frozen_snapshot.json` 和测试日志。

### Phase 1：构建自然失败 Ledger

- 来源顺序：已有 source-bound 历史任务 -> 实验一候选池 -> repair-needed pool -> Expander 定向补充。
- 以 `(repo, commit, top, RTL family)` 去重；同一家族的变体不能伪装成独立证据。
- baseline 连跑两次；结果冲突时跑第三次，多数结果作为稳定标签。
- 只接收：源码闭包完整、时钟约束完整、100 MHz 固定任务、工具环境正常、失败可重复。
- 排除：输入错误、缺依赖、无有效时钟、macro-required、结构性不可实现、环境失败、随机不稳定、超大资源异常。
- 对每个任务记录全局结果向量：ORFS stage、route、DRC class/count、LVS、antenna、setup/hold、RCX、constraints、artifact binding。

产物：`failure_ledger.jsonl`，并标记 `catalog_covered`、`scope_gap`、`catalog_uncovered` 或 `ineligible`。

### Phase 2：缺口审计与候选筛选

- 按 normalized symptom、物理位置、阶段和 effect fingerprint 聚类，而不是按项目名称聚类。
- 先选择至少有两个独立自然 RTL family 的机制；样本不足时调用 Expander 定向补充。
- 先做低成本阶段筛选，只有目标指标明显改善且无早期回归时才进入完整 ORFS。
- 每个机制最多三个非等价 effect；no-op、等价改名、超界参数、任务放宽立即淘汰。
- 所有失败候选写入 negative evidence，防止后续重复消耗。

每个 candidate Recipe 必须声明：

- `strategy_id`、effect fingerprint 和适用 scope；
- allowlisted 参数及硬边界；
- 预期修复的 symptom；
- 受保护的不变量；
- rollback 方法；
- 禁止条件和已知负面证据。

产物：候选卡片与 fast-screen 结果。

### Phase 3：严格 A/B 与 Lifecycle

每个 promotion 至少满足：

- 两个独立自然 RTL family；
- 两轮可重复 A/B；
- 每轮包含 2 个 A runs 和 2 个 B runs；
- A/B run ID 不同，真实存在，并归属当前 trial、arm、design、platform；
- `provenance_complete=true`；
- A/B 之间只有目标 Recipe 的 effect；
- B 的目标问题改善，并且没有 route、DRC、LVS、antenna、timing、RCX、约束或 artifact provenance 回归；
- no-op、任务放宽、混杂修改和不完整 evidence 一律不得 promotion。

通过后只从验收 evidence 重建一次 learner/lifecycle；失败则写入 negative evidence，不得换名重试。

产物：A/B manifests、global-vector diff、promotion 或 rejection 记录。

### Phase 4：独立能力验证

在 Recipe 和知识库冻结后，选择未参与候选设计或 promotion 的 family-deduplicated cohort：

- 至少 10 个合格 repair-needed 任务；
- 尽可能覆盖至少 4 类自然 symptom；
- Full R2G 全自动运行，外部 LLM Token 为 0，人工干预为 0；
- 同时重跑固定五任务，但它们只用于 regression，不计入新 promotion evidence。

报告：总体和分类 strict-clean repair rate、catalog-exhausted rate、ORFS 次数、wall time、资源、外部 Token、人工干预、95% Wilson interval。

## Experiment 2 启动硬门槛

只有同时满足以下条件，才建议启动正式实验二：

1. 至少 1 条**真正新的** Sky130HD Recipe 完成严格 promotion。
2. 独立验证 cohort `N >= 10`，Full R2G strict-clean `>= 7/10`。
3. 固定五任务按 fixed-footprint 重算后不低于其冻结基线，目标达到 `>= 4/5`。
   现有 `3/5` 仅可作为 bounded-area-recovery 的历史参考，不能作为本门槛的
   起点。
4. 无 provenance、数据泄漏、任务放宽、混杂变量或全局回归等 P0 问题。
5. lifecycle/ranking 只让正确 scope 的 promoted Recipe 自动应用。
6. 所有关键测试、manifest 和知识库统计一致。

“覆盖四类 symptom”作为 readiness 的重要目标单独报告。若在冻结的搜索预算内自然样本不足，不得人工制造或放宽门槛；应诚实报告样本稀缺，并将正式实验结论限制在已覆盖的 symptom 范围内。

## 停止与回退规则

- 同一机制的三个非等价安全候选都失败：停止该机制，记录边界，转向下一高价值缺口。
- 失败来自工具、输入或结构性不可实现：移出 Recipe 训练集，不把它学习成设计修复策略。
- 目标改善但任一同级或更严重指标回归：判为 regression，不进入正证据。
- 无法达到启动门槛：输出真实能力边界和下一轮缺口，不降低 strict signoff，不修改题目，不针对验证集补 Recipe。

## Terra 完成时必须提交的证据

- 冻结快照及 digest。
- family 去重的 failure ledger 和 cohort split。
- catalog gap audit。
- 所有 accepted/rejected candidate 及 negative evidence。
- A/B arm ownership、配置 diff、global result vector 和 provenance manifest。
- promotion 后的 lifecycle/learner diff。
- 独立验证和固定五任务回归报告。
- 明确结论：`READY` 或 `NOT READY`，以及对应硬门槛逐项结果。
