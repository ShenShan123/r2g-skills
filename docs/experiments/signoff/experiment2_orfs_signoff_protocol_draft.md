# 实验二：ORFS/Signoff 外部对比协议草案

状态：Pilot 已实现，尚未冻结为正式论文协议。  
更新日期：2026-08-09

## 研究问题

在相同 prospective RTL、Sky130HD 工具链、预注册任务目标和资源预算下，冻结的
Full R2G 是否比 Default ORFS 和 Vanilla LLM 更稳定地修复可重复物理失败并交付
严格 signoff-clean 设计？该比较评价 R2G 的冷启动系统能力和跨设计泛化，不允许
利用本次测试中产生的跨 RTL 学习。

## 实验二、三共用的 Prospective 样本体系

实验二和实验三不分别临时找题。正式 Agent、初始知识状态 `K0`、工具链、候选规则和
预算冻结后，从实验一的 synth-qualified 输出一次性建立 master prospective pool，
再预注册为三个互不重叠的分区：

| 分区 | 用途 | 知识规则 |
|---|---|---|
| `E2` | 实验二外部方法比较 | 每个 RTL 均从相同 `K0` 独立开始，不跨题学习 |
| `A` | 实验三 adaptation | 按预注册顺序共享可写状态，从 `K0` 形成 `K1` |
| `B` | 实验三 held-out evaluation | 不参与学习，只比较 `K0` 与由 A 产生的 `K1` |

三组按 normalized symptom/action family、综合规模、失败严重度、初始
footprint 和设计类型分层匹配，但 repo、top 和 design family 必须互斥。同仓库模块、
fork、参数变体和共享主要 RTL closure 不能跨分区冒充泛化。分区 manifest、Git commit、
source closure、目标、工具链及 `K0` digest 必须在任何被测方法运行前锁定。

### 快速且中性的筛选漏斗

样本发现可以使用便宜信号排序，但正式入选不能依赖 Full R2G 是否成功：

```text
实验一 synth-qualified RTL
-> synthesis/place/global-route proxy 风险排序
-> Default ORFS 完整 baseline
-> 失败候选的独立重复
-> 预注册公共动作的独立 strict-clean feasibility
-> repair-needed pool / clean sentinel / rejected
```

- proxy 和历史统计只能决定先筛谁，不能作为正式 repair-needed 标签；
- 候选筛选用于构建定向 challenge cohort，而不估计全部 synth-qualified RTL 中
  repair-needed 的自然发生率；在运行前冻结风险分数、阈值、top-K 和规模/来源 quota，
  只有越过边界的高风险候选进入 Default ORFS；
- 阈值以下候选记录为 `not_screened_low_risk`，不消耗完整 ORFS、不计作 clean，也不进入
  repair success、recovery rate 或方法比较分母；
- 第一次 baseline strict-clean 的设计直接成为 clean sentinel 候选，不再做修复臂；
- source、top、clock、synthesis、环境或工具 collateral 失败直接排除；
- repair-needed 必须在两个独立 namespace 复现同一非环境物理 symptom；
- feasibility 必须由与被测方法无关的确定性 runner，使用对所有方法公开的同一
  action-domain 和 bounds 达到 strict clean；仅“存在一个动作名”不算可修；
- 高风险筛选总数、各 stratum 目标和停止规则预先登记，不能一直寻找直到 R2G 占优；
- 所有未入选、baseline-clean、不可修和资源耗尽候选均保留在筛选漏斗报告中，防止
  只展示成功题目。

当前历史 repair screen 只用于调试筛选器和动作域，属于 retrospective development
evidence，不能进入正式 `E2/A/B` 论文成绩。

## 已确认设计

### 固定输入

- 所有方法处理同一批固定、synth-qualified 的 Verilog/SystemVerilog RTL。
- 对同一个 RTL，所有方法使用相同的源码闭包、top、平台、工具版本、基础
  `config.mk`、受保护约束模板、计算资源和总预算。
- 主平台为 Sky130HD；Sky130HS 和 Nangate45 只用于后续小规模迁移验证。
- 正式实验在 Agent、Prompt、evaluator、知识库快照和工具链冻结后从干净目录重跑。
- `E2` 同时包含 repair-needed RTL 和少量 Default ORFS strict-clean sentinel；前者测
  恢复能力，后者测 non-regression，不能只挑已知 Recipe 擅长的问题。

### 对照组

| 方法 | 可使用的能力 |
| --- | --- |
| Default ORFS | 在预注册目标和原始配置上运行，不进行智能诊断、参数修复或跨运行学习 |
| Vanilla LLM | 与所有方法相同的受保护低层动作、原始日志和公共 Gate validator；在固定 100 MHz 目标下自行决定 allowlisted 参数、修复动作和重跑路径，首次 strict clean 后停止 |
| Full R2G | 完整结构化执行、状态管理、诊断、Recipe、memory、negative evidence、A/B 和 signoff gate |

Vanilla LLM 不得调用 R2G skills、Recipe、memory、diagnosis、ranking、知识库或高层
修复接口。所有方法共享的低层接口只负责读取状态、验证冻结的 100 MHz 约束、调整 allowlist
中的数值型 ORFS knob、执行完整 ORFS、调用公共 validator、锁定和提交 checkpoint。
这层接口不返回根因或修复建议，因此不会把 R2G 的学习与诊断能力复制给 Vanilla；
同时避免把 shell 拼写和路径格式差异误当成 signoff 推理能力。

Default ORFS 是中性的固定基线，也是 repair-needed 标签的来源。正式方法比较统一使用
100 MHz，不依赖 Full R2G 或任一 LLM 的成绩来决定任务难度或是否执行。它不能
继承其他方法修改后的 config、报告或中间产物。

### 任务目标

主任务不让各方法或各 RTL 自行选择题目难度。Sky130HD 主实验对所有 RTL 统一使用
`100 MHz`，即主时钟周期 `10 ns`。该目标直接继承实验一的 synth qualification，
在实验二候选筛选前已经确定，不通过 Default ORFS、Full R2G 或 LLM 的运行结果校准。
每个 RTL 另冻结初始 footprint policy 和公共 action bounds。所有方法必须在完全相同的
100 MHz 目标下修复同一个 baseline：

```text
Primary outcome = 是否在 100 MHz 下达到 strict clean
```

任一方法首次达到 strict clean 后立即停止并锁定 checkpoint，不再升频搜索。降低频率、
延长时钟周期或用其他方式放宽任务均不构成修复。Fmax 属于独立的 QoR 问题，不进入
实验二 Pilot 或正式主实验。wall-time、CPU、并发数、Token、EDA 调用和停止条件均冻结，
所有尝试写入审计日志。

为防止约束作弊，100 MHz 必须施加到真实主时钟并覆盖全部应计时寄存器路径。禁止
通过新增 false path、multicycle path、虚拟时钟、关闭检查或降低设计目标获得 clean。
evaluator 使用冻结的 10 ns 约束模板重建 SDC，并独立复验 timing 与完整
strict-signoff Gate。

各方法可以在共同 allowlist 内调整 placement、CTS、routing 和 timing-repair 数值参数，
并自行选择重跑和 checkpoint 策略。每个 fixture 的 footprint policy 必须预注册为固定
面积或有界 `CORE_UTILIZATION`；若允许有界面积变化，所有方法使用相同上下限，并报告
实际面积，不能靠无限扩张获得成功。RTL、top、功能、platform、library、主时钟、设计
检查集合和 signoff deck 均不可修改。禁止手工改写 DEF、netlist 或 report 伪造成功。

### 结果判定

Strict clean 要求 synthesis、floorplan、placement、CTS、route、finish 全部完成，
route violations 为 0，full-deck DRC violations 为 0，LVS clean，setup/hold 均满足
`WNS >= 0` 且 `TNS = 0`，antenna violations 为 0，RCX/SPEF 完整，并且 GDS、DEF、
ODB、netlist、配置和报告 provenance 一致。任何检查为 skipped、unknown、timeout、
缺报告或 digest 不一致，都不能计为成功。部分完成、放宽设计目标或引入全局回归同样
不能计为成功。

Area、power 和 IR drop 作为辅助 QoR 指标完整报告，但不进入统一 hard Gate。物理
footprint policy 及其上下限已冻结，不能靠越界增大面积获得 clean；power/IR-drop 在缺少统一 testbench、
切换活动率和电源电流模型时不作为所有 RTL 的严格通过条件。

所有方法获得同一个公共 `validate_checkpoint`。它与最终 evaluator 共享硬 Gate 判定
实现，只返回当前 checkpoint 的逐 Gate 状态、原始计数/时序值和证据路径，不提供根因
诊断、参数建议或 Recipe。调用产生的 EDA 时间和工具次数计入 campaign 预算。方法必须
在预算内锁定最终 checkpoint；锁定后才运行独立复验，看到最终评价结果后不能替换。

评分采用“合法性优先、性能其次”，不构造可以相互补偿的加权总分：

1. 任一 strict Gate 失败、未知或证据不完整，均不是 clean；
2. 只有在冻结的 100 MHz 目标下 strict clean 才计为 repair success；
3. 时间、Token 和成本不能抵消可信性失败，只在成功率之外分别报告效率。

### 反规避与独立证据

evaluator 必须检查并 fail-closed 阻止以下做法：

- 修改 RTL、top、功能综合网表、platform、library，或让物理 footprint 超出预注册 policy/bounds；
- 选错/遗漏真实主时钟、留下未约束寄存器路径，或增加 false/multicycle path；
- 放宽 uncertainty、IO 约束、检查集合、DRC/LVS/antenna deck 或超时判定；
- 将旧 run、其他频率或其他设计的 DEF/GDS/netlist/report 混入当前 checkpoint；
- 手工编辑报告、DEF 或网表，删除失败证据，或把 skipped/timeout/unknown 当作 clean；
- 隐藏失败尝试、额外计算或人工介入。

每个 checkpoint 必须由 run ID、RTL/综合网表 digest、完整配置 digest、目标频率、
DEF/GDS/ODB/SPEF digest、signoff report digest 和工具版本共同绑定。公共预检与最终
evaluator 共享判定代码，但最终 evaluator 从锁定产物独立重建受保护 SDC、重新读取
物理数据库并复验适用的 signoff 检查，不能信任方法自己的 success 字段。

在启动 Pilot 前，评分器本身必须通过固定的正向和对抗 fixture：真实 clean 应通过；
DRC/LVS/timing dirty、缺失报告、stale/mixed artifacts、修改 RTL/越界面积、错误时钟、
false/multicycle path、伪造 report 和 timeout 均应被拦截。评分器不得包含按模型名称、
方法名称或已知候选 ID 编写的特殊分支；其版本和测试 digest 必须在 campaign manifest
中绑定。

同一个 RTL 上的比较顺序为：

1. 在 100 MHz 下 strict clean 的方法胜过未 clean 的方法；
2. 双方均 clean 或均未 clean 时，能力结果记为平局；
3. wall-time、Token、完整 flow 数和无效修复次数作为独立效率指标，不合成为总分。

### 主报告

- strict-clean success rate；
- Default ORFS、Vanilla LLM 和 Full R2G 在统一 100 MHz 下的配对 win/tie/loss 与 recovery rate；
- clean sentinel non-regression rate；
- stage completion 和失败阶段；
- wall-time、EDA runtime、Token、API 成本、工具调用和人工介入；
- invalid action、无效修复和全局回归率。

逐 RTL 结果进入附表，主表使用 100 MHz strict-clean 成功率和配对结果。规模、失败类型
和 footprint stratum 分层报告，避免用设计组成差异掩盖方法能力。

## 尚待确认

- 正式实验的 RTL 数量、规模分层、default-clean/repair-needed 比例和 macro 子集数量；
- Vanilla LLM 型号、重复次数和预算；
- 独立 evaluator 的具体复验步骤和统计方法。
- master prospective pool 的候选上限、stratum quota 和 `E2/A/B` 正式样本量。

## Pilot 方案

- Pilot 从实验一现有、已经通过独立 qualification 的候选池中选择 8 个 RTL。
- 按冻结的 Sky130HD synth-only mapped-cell 数分为小型 2 个、中型 3 个、大型 3 个，
  具体区间为小型 `100-999`、中型 `1,000-9,999`、大型 `10,000+`；同时尽量覆盖
  不同仓库和功能，不使用 RTL 行数代替综合规模。
- 8 个 Pilot RTL 均为纯标准单元设计，不包含需要额外 LEF/LIB/GDS/CDL 的 hard macro，
  避免在协议调试阶段把 macro collateral 缺失误判为 Agent 缺陷。
- 入选 RTL 必须具有明确主时钟并能够进入 Sky130HD ORFS。
- 这 8 个 RTL 标记为 `development_seen`，只用于调试 Prompt、runner、预算、
  checkpoint、约束保护和 evaluator，不进入正式论文成绩。
- Pilot 可以修改实验协议和实现；正式 Agent 冻结后，必须根据届时冻结的抽样规则
  重新建立正式测试集，并从干净目录重跑。
- Pilot 仍可使用 development-seen 或 retrospective RTL 验证 runner；正式论文必须在
  最终冻结后重建 prospective master pool，并一次性划分 `E2/A/B`。
- Pilot 主方法固定为 GPT-5.5 Vanilla、Qwen3.7-Max Vanilla、DeepSeek-V4-Flash
  Vanilla 和 Full R2G；Default ORFS 对全部 fixture 使用预注册目标运行，而不是等待
  R2G 结果后再决定是否 replay。
- Default ORFS qualification 若与正式基线具有相同源码、配置、目标、工具和 evaluator
  digest，可指定其中一次作为第五种方法的基线记录；否则必须从干净目录重跑，不能复用
  不等价的筛选结果。主结果矩阵固定为 8 个 RTL × 5 种方法，共 40 个结果单元，其中
  32 个来自 4 种智能方法，8 个来自 digest-bound Default ORFS baseline。
- 另在查看各方法结果前预注册 2 个重复性检查 RTL：一个中型稳定样本、一个大型挑战
  样本；4 种智能方法分别额外重复一次，增加 8 个复现审计 campaign。Default ORFS 的
  两次独立资格运行已经用于验证可重复性，不再重复。主矩阵、复现审计和资格筛选的计算
  开销分别报告，不能把筛选成本隐藏在方法外。复现子集不承担正式置信区间结论。
- 每个主 campaign 固定 100 MHz，硬上限为 4 小时、4 个 CPU 核、同时最多 1 个 ORFS
  flow，且最多执行 4 次完整 flow-equivalent。首次 strict clean 后必须停止，不能把剩余
  预算用于升频或重复优化。
  Vanilla LLM 另设每个 RTL 300,000、每种方法 2,400,000 个供应商报告的 total-token
  上限和 50 轮交互上限；Full R2G 不调用 LLM
  时 Token 记为 0，但仍受相同 EDA wall-time 和计算资源限制。
- 实验二的 RTL、工具链和初始配置均已发放，不需要网页搜索；Vanilla 只读取本地冻结
  输入、原始 EDA 日志和公共 validator 结果。供应商不得附带隐藏的 EDA Agent、R2G
  memory 或未声明的高层工具。
- Full R2G 的每个 RTL 从同一个只读 knowledge/heuristics seed 独立开始；冻结种子与
  工作状态物理分离，完整性校验只约束种子。每个 `method × RTL` 使用独立可写的
  knowledge/journal/heuristics state；Full R2G 还使用逐 RTL 隔离的 Agent runtime，
  以覆盖仍按脚本相对路径访问默认知识文件的旧模块。Vanilla 从空隔离状态开始，测试
  RTL 之间不共享新证据，防止按执行顺序发生测试集学习或跨方法污染。运行结束记录
  工作状态的初始与最终摘要，但合法的学习写入不触发 campaign binding 失败。
- 任一硬预算先达到即停止；若曾达到 strict clean，保存首次合法 checkpoint，否则保存
  最终失败证据。Pilot 后只能在
  多数方法受到同一种预算截断的情况下统一调整正式预算，不能针对低分模型单独加额。
- 供应商 API 5xx/断线、主机或磁盘故障、预装工具损坏等具有机器证据的外部故障记为
  `infrastructure_invalid`，不进入能力成绩，并允许在完全相同条件下最多 2 次干净
  重跑。持续失败则报告 unavailable，不能更换 RTL 补位。LLM 生成错误命令/路径/
  配置、错误恢复，或设计物理不收敛、预算耗尽，均属于方法结果，不得按基础设施故障
  重跑。分类规则预先冻结，不能根据模型名称或最终分数人工调整。

## 正式实验的 Macro 要求

- 正式实验必须预注册一个独立报告的 Sky130HD hard-macro 子集，具体数量在正式样本
  规模确定时冻结；不能因为 Pilot 使用标准单元设计而省略 macro 能力评价。
- 每个 macro case 必须在实验开始前具备互相匹配的 Verilog model、Liberty、LEF、
  非空 GDS、CDL/SPICE 和 placement collateral，并先通过独立 capability canary。
- behavioral flop-array 替代属于标准单元降级路径，`GDS_ALLOW_EMPTY` 属于占位集成；
  两者都不能冒充真实 hard-macro strict-signoff 成功。
- macro 子集使用与主实验相同的 strict Gate、资源审计和 provenance 规则，并同时单独
  报告成功率，避免其平台 collateral 难度被标准单元样本平均掩盖。
