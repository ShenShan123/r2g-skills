# Physical-Repair Literature and Source Audit

## 结论

现有论文和开源项目可以明显缩短候选任务与候选动作的发现周期，但没有任何一项
能够直接提供可写入 R2G 的 Sky130HD promoted Recipe。外部结果只能作为
development prior；每个动作仍需在冻结的 `Sky130HD + 100 MHz + fixed-footprint +
strict signoff` 条件下经过自然失败复现、严格 A/B、全局非回归和独立 family 验证。

## 可直接借鉴的内容

| 来源 | 可借鉴内容 | 对本轮的用途 | 不能直接使用的原因 |
|---|---|---|---|
| DAC26 DRC Benchmark | repair rate、new-violation rate、connectivity preservation | 完善 DRC candidate 的全局非回归指标 | ASAP7/KLayout 局部版图任务，不是 Sky130HD RTL-to-signoff 任务 |
| EvoDRC | 按层/规则组织 repair skill、影响预览、失败知识演化 | 借鉴 symptom-to-action 组织和 negative evidence | 操作对象是局部 layout geometry，当前 R2G 主要是受限 flow/config action |
| DRC-Aid | 确定性规则引擎生成受限动作菜单，LLM 选择，DFS/backtracking 和 visited-state memory | 借鉴候选生成、回退和防振荡机制 | FreePDK45/Calibre，且未发现可直接复用的公开实现 |
| PostEDA-Bench | 75 个 PPA task 的 RTL/config、动作变量和统一评测方式 | 去重后作为 development 候选源与参数先验 | 实际只有 17 个 source closure；配置面向 ASAP7 和 QoR 目标 |
| ORFS-Agent | OpenROAD 参数搜索空间与 Sky130HD 支持 | 为 route/timing candidate 提供参数动作先验 | 多数结果是 QoR 优化；部分动作改变面积或任务边界 |
| CLOSER-Bench | Sky130 + OpenROAD 的预算化跨阶段 closure 协议、完整工具调用轨迹和 anytime 指标 | 约束本轮的工具预算、阶段恢复和轨迹记录方式 | 公开论文以评测协议为主，未提供可直接迁移的 Sky130HD repair catalog |
| Metrics4ML / OpenROAD AutoTuner | Sky130HD DOE 配置、逐阶段 metrics、可复现参数搜索空间 | 从历史轨迹筛选与故障机制相符的 route/timing 候选动作 | 目标主要是 PPA tuning；最优配置不是自然失败上的因果修复证据 |
| PDB Physical Design Database | Sky130HD/Nangate45/GF180 的 RTL-to-layout 配置、报告和布局元数据 | 补充候选 family，并审计不同配置对失败分布的影响 | Sky130HD 只保证 layout generation，且已有配置/版图不能作为本轮 baseline 答案 |
| HighTide / OpenROAD demo | 真实 RTL、可运行配置和多平台设计 | Expander 不足时补充候选 family | 已调优配置不能作为自然 baseline 或 promotion 证据 |

## PostEDA-Bench 审计

- 公开 PPA 部分共有 75 个 task，但按 RTL source closure digest 去重后只有 17 个
  family；其中 GCD 与 float multiplier 被大量重复使用。
- 17 个 family 中只有 9 个 task 目录带 source-level `LICENSE`；其余仅受 benchmark
  总体许可覆盖。本轮可作为 development source，但正式发布前仍需核对上游许可。
- 多数任务使用 ASAP7 专用参数，不能继承其 clock、die/core 或已调优配置。
- 本轮只复用 RTL bytes、top/clock 线索和候选 action 先验，重新建立 Sky130HD
  100 MHz baseline。
- virtual-clock、macro-required 和多时钟任务不进入第一波 fixed single-clock 筛选。

## 采用顺序

1. 对 PostEDA 去重后的顺序 RTL family 做 `synth + floorplan` 轻量资格筛选。
2. 合格 family 才运行完整 Default ORFS；默认失败需独立复跑，形成稳定失败后才进入
   repair ledger。
3. 在详细查看日志前按 RTL family 冻结 development/internal-validation split。
4. 当前 R2G 先在冻结 ledger 上运行，得到真实初始 repair rate。
5. 仅对 development 中 catalog-uncovered 的机制使用论文/开源项目提供的动作先验；
   每个机制最多三个非等价 candidate。
6. 通过严格 A/B 后冻结 Recipe，再到 internal-validation family 上验证迁移能力。

## 对工期的实际帮助

- 文献与 DOE 数据可以把候选搜索从“任意改参数”缩小到少量、机制匹配且有工程依据的
  bounded actions，减少无效完整 ORFS 次数。
- CLOSER-Bench 的主要价值是实验控制：区分快速代理信号与最终 closure，记录所有昂贵
  工具调用，并按固定预算比较恢复轨迹，而不是提供现成修复答案。
- Metrics4ML、AutoTuner 和 ORFS-Agent 可用来建立参数作用表；只有当自然 development
  失败的日志与作用机制匹配时，才允许将相应参数实例化为 candidate Recipe。
- PDB 和 PostEDA 可以减少“从互联网盲找 RTL”的时间，但仍需重新 materialize、固定
  source closure、跑当前 Sky130HD Default ORFS，并复现同一失败两次。

## 本轮边界

- 外部 benchmark 的已知最优参数、参考结果和 tuned config 不作为答案泄漏给 R2G。
- 不改变 10 ns 时钟，不关闭检查，不继承 ASAP7 footprint，不用面积放宽冒充
  fixed-footprint 修复。
- DRC 局部几何 ECO 是有价值的下一层能力，但只有在现有 config/pin action 无法覆盖且
  能实现可回滚、可哈希、可 A/B 的受限动作层后，才进入 Recipe catalog。
