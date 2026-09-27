# TEHM R5 真实 RTL 实验：当前进展与阻塞点

> 截至 2026-09-27；编写前的主树基线为 `publish-memory@317c8a3`。执行依据是
> [Revision 5 方案](../docs/TEHM_R2G_Revision5_RTL测试判定力_受限绑定与独立迁移Pilot方案_2026-09-24.md)。
> 本文是状态与决策记录，不是新的实验、FINAL 结果或运行授权。不同软件代次、
> 来源角色及测试范围不得合并记账。

## 一句话结论

资格判定、受限 binder/action、真实 DEV 原生测试和一代合法 TRAIN Memory
分别有可复核证据；**尚未在同一冻结代次上完成“合法 TRAIN Memory →
预选独立未见目标 → 实际三态修复与 ΔMemory 归因”**。因此目前可以报告
有界 DEV 修复与历史负结果，不能报告 answer-free cross-lineage transfer、
TEHM 相对 transform-only 的收益或论文规模修复率。当前优先推进 I²C v2
的 TRAIN 准入及第四来源的未见目标，而非继续优化三个已观察 DEV 案例。

## 阶段进展与证据边界

| R5 门槛 | 已完成的有界事实 | 尚未完成 / 不能推出 |
|---|---|---|
| R5-0 / GQ：资格 | [QF-1-r4](research_r5_qf1_and_dev_20260924.md) 索引 10 个固定仓库版本和三项负控；axis 9/9 与 AES 20/20 在各自指定故障上 `DETECTED`，UART RX 2/2 为 `MISSED`。旧 binder 扫描 249 个文件、0 命中。 | 不是 10 个合格 repair task、249 个可修复结构或总体 mutation 检出率；历史 wrapper/依赖锁缺项不能补造。 |
| R5-1：verdict | cocotb JUnit v2 与 Secworks native-summary v1 已覆盖缺报、零测试、退出码/原生功能汇总矛盾；功能 `ERROR` 不被 rc=0 掩盖。[QF-1 回执](research_r5_qf1_and_dev_20260924.md) | 每个新目标仍需按实际 source/test/tool/seed 重新登记判定范围与负控。 |
| R5-2/3 / GD：skid 开发 | AXIS→ZipCPU→LibSV/PULP/mux 的有界动作开发、负拒绝及原生/增强 oracle 已形成 gen6 v8 软件身份；它不是通用 skid buffer 修复器。[gen6 v8](research_r5_gen6_mux_v8_20260926.md) | 不能用新版本 binder 能力增长解释成旧 Memory 增益。 |
| R5-4 / GM：skid TRAIN | [gen6 v8 三视图](research_r5_gen6_m0_v8_20260927.md) 用三个重新登记的 reused-DEV TRAIN 任务通过核心 Knowledge/Asset 准入；M− `NO_SKILL`，M+ `CONSIDER/SELECT`，Mremove `NO_SKILL`；Mremove 与 M− 语义等价。冻结软件 worktree `f51f8fc` 可冷验。 | 这只是 TRAIN 内查询/选择预检；没有未见目标候选、fresh oracle 或 ΔMemory 修复效应。三份文件来源组不自动构成统计独立来源。 |
| R5-5 / GT：skid 目标 | 预选 C1 clean native PASS，但源码审查发现双入口 selector 缓冲机制与 v8 单槽 payload 动作不匹配，判为 `UNQUALIFIED`；后续定界发现批次也无合格元数据候选。[C1 结论](research_r5_gen6_c1_disposition_20260927.md)、[发现批次](research_r5_gen6_source_discovery_batch2_result_20260927.md) | 没有把 C1 补造成 fault，也没有 gen6 跨来源 T/A；负结果保留。 |
| R5-2/3 / GD：I²C v2 DEV | alexforencich、ZipCPU、freecores 三份已观察故障，v2 source-only binder 均唯一 `BOUND`、干净态均 `NO_MATCH`；7/7 对抗检查、冻结 v1 10/10 回归。freecores 新生成候选在 Icarus 原生范围内 PASS，32 文件 audit 冷审有效。[v2 结果](research_r5_i2c_nack_binding_v2_dev_result_20260927.md) | freecores 在 v1 冻结 probe 曾 `UNSUPPORTED`，随后明确改列 v2 `DEV_OBSERVED`；本次正结果不是新目标迁移。v2 未接 Memory 或 production。 |
| R5-4 / GM：I²C v2 TRAIN | 尚未建立。ZipCPU 原生 clean `SUCCESS!`，但原 bench 对缺席设备 NACK 状态无指定判定力，已记 `PENDING_ORACLE`。[筛查结果](research_r5_i2c_train_source_screen_result_20260927.md) | 不能把 gen6 v8 的 M0 与 I²C v2 action 拼成同一代次；也不能把 DEV PASS 直接升为 TRAIN Knowledge/Asset。 |
| R5-6 / GA：T/A | 尚无同一新目标上实际加载 M−/M+/Mremove、重新 route/select/bind、分别构建候选并执行 fresh oracle 的完整配对。 | 当前没有可报告的 ΔMemory attribution；transform-only 是否同样成功必须实测。 |
| R5-7：Agent | [C7](research_r5_s2_provider_c7_20260926.md) 对一个已观察 DEV task 执行 3 次真实 DeepSeek V4.1-Flash 调用及 3 次原生评估，三策略首轮均 PASS，TEHM 没有观察到额外收益。 | C7 不使用模型提出的 Memory action，不进入未见目标、归因或 FINAL 分母；既有调用授权不自动扩展到新任务。 |
| R5-8 / GP：论文 | [协议 v1](research_r5_paper_protocol_20260925.md) 和[样本规划](research_r5_sample_planning_20260926.md) 已固定指标、完整分母、来源聚类、停止与信息隔离规则；历史 gen5 F1 为 1 task、四臂各 0/1、差值 0。 | `final_tasks=[]`，`final_test_ready=false`；来源总体/抽样、确认性目标与统计独立性未冻结。精度情景不是功效或已获执行预算。 |

上述阶段中，gen6 v8 TRAIN、I²C v1/v2 DEV、gen5 F1 与 C7 分属不同软件／任务角色。
它们相互提供工程教训，**不能拼接为同一条已完成的 T/A 结果**。

## 当前阻塞点（按关键路径排序）

1. **I²C v2 缺合法 TRAIN Memory（GM）**。若重用三个已见 DEV 来源，须先明确改列 researcher-assisted TRAIN，在 v2 软件身份下重新登记并实际执行 fault→candidate→source-rollback，完成原始 oracle、来源/谱系、canonical/Knowledge/Asset 严格准入及 delta 冷审。ZipCPU 原 bench 的 NACK 缺席地址义务不合格；若需新增 augmented oracle，应单独冻结其版本与判定力，不能拿 clean `SUCCESS!` 代替。任何来源关系不能被独立核实时，只能报告有界来源组，不能升级统计独立性。
2. **缺预选的第四个未见 I²C 目标（GT）**。alexforencich、ZipCPU、freecores 都已用于 v2 开发；freecores 的 v1 拒绝与 v2 DEV PASS 均保留。新目标必须在绑定器运行和答案可见前固定 commit、源码/test closure、参数、指定故障/保持义务、原生敏感性和既往暴露/共享代码关系。无匹配、拒绝或不合格 oracle 必须留在资格/方法账本，不按成功重选。gen6 skid 的 C1 结构不符是另一条线的相同警示。
3. **未做同代次的 T/A 与 transform-only 配对（GA）**。目标资格和 I²C v2 M0 都到位后，冻结同一 runtime/controller/oracle/binder/catalog/预算，分别实际加载 M−、M+、Mremove 并重新运行所有 view；另以同代码、不读 Memory 的 transform-only 对照测量“代码自己会修”的比例。目标、保持、native 三项同时 PASS 才是 VerifiedRepair；UNKNOWN 和拒绝留在预纳入分母。即使三视图全 PASS，也不能声称 Memory 增益。
4. **证据保全尚缺独立故障域（R5 §19.4）**。gen6 detached worktree 与主仓库共享 `.git` 和本机存储；I²C DEV 原始包及 C7 恢复也主要在同机目录。发布或删除唯一原始数据前，仍需第二份可校验副本及异路径完整 case 恢复；仅有 Git 提交、Markdown 摘要或同机 worktree 不满足该门槛。[软件恢复边界](research_r5_gen6_replay_worktree_20260927.md)
5. **论文最终样本与新模型预算尚未授权/冻结（GP，后续门槛）**。若只做当前正向可行性 pilot，可先报告逐任务和逐来源完整结果，不预设提升百分点；若要总体收益或完整 Agent 比较，须在新 FINAL 观察前冻结来源抽样、效应/精度目标、资源与调用/token 上限，另获新真实模型调用授权。不能使用 C7 剩余额度追求新的正结果。[论文协议](research_r5_paper_protocol_20260925.md)

## 建议的下一次阶段性交付

优先完成 **I²C v2 TRAIN generation**：先补齐/筛掉不合格 NACK oracle，显式重登记三个已观察来源并对每个来源重新执行动作与回滚，按核心 gate 构建 v2 M−/M+/Mremove；在同一阶段附上来源谱系审查、代码/输入摘要、失败记录、冷验与 transform-only 定义。只有 GM 成立后，再预登记第四来源目标并运行 T/A。若训练来源或目标不满足门槛，交付清楚的 `NO-GO` 与原始原因，不用修改已观察目标或拼接 gen6 Memory 来制造正结果。

本状态整理不启动模型、EDA 或下载，不修改任何冻结结果、上游 checkout、
Memory snapshot 或生产权限；本地主树历史脚本清理只退役无现行调用的独立入口，
仍被委托/兼容链导入的旧编号模块保留。[最近清理记录](tehm_tree_cleanup_20260927_r6.md)

## 本次核对范围

本次读取方案、阶段报告、当前 Git 状态和三个关键冻结回执的现存字节摘要：
gen6 M0 build report SHA256 `da2a9b1853bc9a0306f783301cd1ea66de9270ea2a2e1cca80286bd80c9d960b`，
freecores v1 资格 audit SHA256 `1e90f120f7dd432427751125d626b3f301adb67076886e165beb91153079cf69`，
I²C v2 DEV audit SHA256 `823149b38301de969cb9969641a0f107dd9fd7792f8f945337eba554034be0d6`。
没有在本次重新运行全部历史 simulator、完整 raw auditor、gen6 M0 verifier 或
远端 Git ref 核验；表中相应实验状态依其已冻结报告，而非声称本次新执行。
