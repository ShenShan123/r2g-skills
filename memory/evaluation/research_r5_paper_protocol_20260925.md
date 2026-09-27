# R5 论文实验协议 v1：方法固定，最终数据尚未冻结

执行依据为 [Revision5](../docs/TEHM_R2G_Revision5_RTL测试判定力_受限绑定与独立迁移Pilot方案_2026-09-24.md) §10–19。本文不是论文结果，也不授予 FINAL_TEST、production、在线学习、模型调用或公开发布权限。它固定现有 deterministic controlled-action 路线的计量与数据划分；完整 Agent 三策略另行冻结，不能把这里的 no-action 对照称作 Agent。

## 1. 研究问题、固定对象与判定责任

问题：在同一软件、任务、预算和 oracle 下，合法 TRAIN Memory 增量是否改变新来源任务的可验证修复结果？无效、拒绝、UNKNOWN 和伤害都是允许结果，不要求出现 FAIL/PASS/FAIL。

- 方法软件：`2ce921a599c406ab63331561a182d6f4f1bef8cb`，现有 v7 bounded binder/operator。
- Memory：`r5-train-m0-gen5-r1`，build report `sha256:f31072bf2c233974c9fbbf4bb4c30539942f07a57a5b6b7e9022611e90f3206b`。TRAIN 为 researcher-assisted，不声称在线自主发现。
- 三态：实际 load M−/M+/Mremove；删除完整知识、资产、关系及索引增量后重建 Mremove，不能仅屏蔽检索 ID。
- 同代码 transform-only/no-history：不读 Memory，最多调用同一个已冻结 primitive 一次。不能为了突出 Memory 而削弱这个对照。
- 公开机制分类、参数与接口说明可供查询；具体错误位置、gold patch、clean counterpart、私有测试与 expected vectors 不进入 source-only consumer。
- 资格验证器决定功能 FAIL/PASS/UNKNOWN；research envelope 记录事实；计量脚本只做一致性检查和算术。计量脚本不能生成准入权威或把哈希相同解释成语义正确。

实验仍复用现有 ResearchEpoch、DesignManifest、TaskContext、PolicyDecision、ScopedExecutionResult 与 CampaignReport 思路。新增 JSON 是实验层计量清单，不新增 canonical Memory schema，也不修改冻结核心。

## 2. 指标选择与范围

比较过的候选包括：clone 数量、native 子测试通过数、binder 命中率、task 修复率、配对 Memory 效果、健康误激活、保持义务受损和执行成本。前两项只描述获取/测试规模；binder 命中率仅作诊断；不能用它们替代方法结果。

| 类别 | 指标与精确定义 | 控制的问题与限制 |
|---|---|---|
| 主指标 | `VerifiedRepair@B = 预算内 target + 全部必需 preservation + native 均 PASS 的 task 数 / 全部预先纳入的方法 task 数` | 拒绝、缺 candidate、超时、编译失败、缺 arm 和 UNKNOWN 不得从分母消失；UNKNOWN 不算成功但不改写为功能 FAIL |
| 主比较 | 同一 task 的 M+ 与 M−、Mremove、transform-only 成功指示之差；按经审计 source group 分别计算，再对 group 等权平均 | 不把参数、子测试、三态、cold replay 当独立来源；同时保留 micro task rate 与每组原始分子/分母 |
| 驱动诊断 | 全量 route/selection/action 覆盖、拒绝理由；独立标注支持集合上的 conditional binding rate（仅有该标注时） | 没有 independent structural label 时不报告 binding recall；条件分母不能替代全量 repair 分母 |
| 安全边界 | 修改导致 preservation FAIL 的 task；健康输入发生实际源码修改的 false activation | 正确版本的同-DUT counterpart、不同机制的独立健康设计、同任务内部 preservation 三类分母分别报告 |
| 测量边界 | 构造探针的 DETECTED/MISSED/UNDETERMINED，加全部生成、编译无效、未激活、超时数量 | qualification probe 不进入方法修复分母；漏检原样保留 |
| 成本 | 获取、资格、DEV、TRAIN、策略、三态反事实、审计/恢复分栏；实际 model calls/tokens 与工具 wall time | 未记录的时间填 null/缺失，不填 0；同机 wall time不是跨机器成本模型 |

不设依据不足的收益率目标。当前 pilot 不足以支持“必须提升 X%”、统计功效或广泛安全保证。准入目标是证据完整、协议一致，而不是正结果。

## 3. 来源、角色与最终测试防火墙

所有任务按精确 repository commit、ordered source/test closure、参数/宏、种子、工具和 oracle 版本登记。Source group 按 fork、复制模块、generator、共享 DUT 与主要依赖审计，不按 owner 名推断。

角色为 QUALIFICATION / DEV / TRAIN / PILOT_TRANSFER / FINAL_TEST / HISTORICAL。已经用于方法开发、训练或方法评估的 exact scope/task 不能改名为 unseen FINAL_TEST；DEV 转 TRAIN 必须显式重新登记并经核心准入。旧五案不可精确恢复时仍为 historical_exact_replay_unavailable。

资格审查需要另看时间和答案可见性：方法已冻结、仅私有 evaluator 观察、方法未收到答案、未用于方法开发且观察后方法未变的 QUALIFICATION，可以进入原始暴露/来源/任务审查，不能仅因该角色标签自动拒绝。缺任一字段、冻结身份不符，或同时有 DEV/TRAIN/PILOT_TRANSFER/FINAL_TEST/HISTORICAL 历史时仍排除。调用方声明只改变预筛为 REVIEW_REQUIRED，不代替原始审计、未见范围证明或最终准入；final_test_ready 和 execution_authorized 始终为 false。此修正只在计量层，不修改冻结 gen5 runtime/binder/Memory。

当前 20 个已获取仓库进入“需逐 scope 检查既往暴露”的清单。这是保守复核清单，**不代表这些仓库每个文件均已看过，也不自动排除其中所有从未使用的范围**。若要使用其新 scope，须另存暴露/来源关系审查并冻结新的最终任务清单；不能仅修改本文件的 `final_tasks` 获得批准。新 owner 或 `fork=false` 也只能进入 REVIEW_REQUIRED，不能自动取得独立性或 oracle 资格。

最终数据冻结前必须具备：预选择序列和理由；既往暴露排除表；来源关系原始审计；scope 资格矩阵；opaque task ID；角色清单；实际 source/test/tool/seed 摘要；独立 build root；完整 attempt 登记；同软件、同预算、同 oracle 三态卡；第二副本和可运行恢复入口。冻结之后不得基于结果修改方法或重新选择任务。

当前 `final_tasks=[]`，机器检查明确返回 `final_test_ready=false` 和 `execution_authorized=false`。这是当前真实状态，不是填满占位符即可通过的 gate。

## 4. 样本量与停止规则

当前只有一个 gen5 constructed repair task，不能从它估计总体效果或为确认性实验设定功效。当前数值仅是范围明确的 pilot 描述。

后续采用两层规划，不把工作量上限冒充统计样本量：

1. 下一次资格预选择最多 2 个 scope，先用已有 corpus 中未用于当前开发的范围；仅在具体机制/原生测试缺口明确时定向获取。候选列表在调用 binder 前固定，拒绝结果不得触发替换为“更容易命中”的目标。
2. 初步论文可行性预算可设为最多 4 个这样的批次、最多 8 个 source candidates、每个设计最多 1 个预登记机制故障。**这是待最终候选框架冻结时确认的资源上限，不是 8 个合格任务、8 个独立来源或已证明足够的统计样本量。** 本文没有启动这些批次。
3. 实际 N 为固定候选框架中的全部合格、预纳入任务；同时披露获取/资格失败和未确定项目。未找到合法范围时报告可行性不足，不补参数变体、不无限续搜、不通过多 seed 追成功。
4. 对这个可行性规模只报告逐组配对结果、micro/macro 描述和全部失败原因；不报小样本总体显著性或功效承诺。若需要确认性总体收益结论，在看 FINAL_TEST 结果之前，另外冻结来源组采样框架、最小有意义效果、可用组间变异依据及样本量计算。当前这些依据未建立，因此该路线仍开放而不是伪造一个 power 数字。

停止以候选框架/资源上限或无合法 scope 为条件，不以达到若干成功样本为条件。修改代码、oracle、测试激励或预算，产生新 generation；原 final 结果保留，并进入已观察历史。

## 5. 执行与故障处理

当前方法每个 arm 最多一个 candidate/action，固定 source-only primitive，model calls/tokens=0。每个任务必须在 manifest 中事先冻结实际 compile/simulate 超时、依赖和 seeds，并对所有 view 相同；不同任务可有不同工具需求，不能把其差异隐藏在同一 scope ID 下。

所有候选先生成，随后 evaluator 执行固定 target/preservation/native；不将私有测试反馈用于当前 source-only binding 搜索。每个构建目录独立，编译摘要必须等于该 arm 输出 candidate。物理隔离隐藏 clean counterpart、Git、oracle、其他 arm、TRAIN DB 和网络；可信 evaluator 单独处理 TRAIN 权威与经过净化的 action handoff。

基础设施异常仅按预登记原因重试，保留旧 attempt 与累计成本。功能失败不能通过换 seed 重跑到通过。缺报告/零测试/矛盾为 UNKNOWN，native functional FAIL 不被 wrapper rc=0 覆盖。错误清单影响对应 campaign，不静默继续为同一个数据集换方法。

R5-7 的 No Persistent Memory / Legacy / TEHM Agent 比较仍未运行。只有确定同一 controller/model/prompt/观察策略并获得新的固定调用和 token 预算授权后才能启动；此处的零调用比较不替代它。

## 6. 已执行读数与可复核入口

`research_r5_paper_protocol.py` 负责分母/共享合约一致性与保守来源筛查；`research_r5_paper_protocol_checks.py` 的 44 个反例通过，属于 synthetic conformance，不是新实验。包括 UNKNOWN、缺 arm、重复 arm、健康控制混入、预算/软件不一致、冻结代码/Memory 摘要变化、观察过 scope 重标记，以及 macro/micro 分母区分。

B2 资格后对预筛作上述修正，当前 67 项检查通过，包含 23 项暴露元数据和拒绝优先反例。历史 44 项封存记录不改写。重新生成 gen5-readout-r3 后，四个数据文件与 r2 逐字节一致；只有 receipt 记录新的 bookkeeping 源码摘要，仍是旧 1 个方法任务、各 arm 0/1 修复，B2 资格探针未计入。

`research_r5_paper_gen5_readout.py` 重新调用已冻结的 pilot raw auditor，归一化真实 gen5 receipt。输出在 `_r5_pilot/paper/gen5-readout-r2/`：1 个 task，四个 arm 均 0/1 修复，所有配对修复差均为 0。原生子测试及两类健康控制未并入修复分母。readout receipt SHA256 `7fc158f180c5ece5bd720ea46403c20965042c721f94983be8217e529c410fd4`。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_paper_protocol_checks
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_paper_protocol --plan memory/evaluation/research_r5_paper_protocol_v1.json
```

方法定义与脚本可以本地提交；原始 corpus、私有答案包不提交、不 push。机器检查通过仅表示当前计量协议自洽；最终数据、独立来源数、Agent 比较和功效仍需各自证据，不能据此宣告 R5 全部完成。

## 2026-09-26 补充，不重写历史冻结

[样本规划 P1](research_r5_sample_planning_20260926.md) 提供可复算的条件性精度情景，非功效或已选预算；[C7](research_r5_s2_provider_c7_20260926.md) 已有真实 DEV 三策略调用，但不增加 FINAL 样本。用户随后明确当前优先正向修复而非预设提升百分点，因此当前不启动确认性大样本路线，也不拿旧失败调参后重标未见成功。冻结 gen5/F1 不变；任何新方法开发另立代次，独立新目标、真实 Memory 归因和最终数据要求继续保留。
