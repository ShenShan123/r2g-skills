# R2G 正式实验一、实验二、实验三设计要点

状态：实验一在不计分 canary 修正停止原因、并发隔离、完整工具链与污染库绑定后，于
`2026-08-26T05:51:08-07:00` 重新冻结；实验二、实验三仍为讨论稿。

## 实验一：RTL Acquisition

冻结版本：`r2g-exp1-rtl-acquisition-v1`。正式运行开始后，方法集合、Prompt、预算、Gate、Runner 和评分器不得修改；任何修改都必须建立新的实验版本并从头运行。

### 目的

比较通用 LLM 与 R2G-Expander 从公开网络获取**可综合、可发布 RTL**的能力。主平台固定为 Sky130HD，终点为 synth-only；主实验只接收 Verilog/SystemVerilog。

### 实验分组

| 分组 | 方法 | 作用 |
|---|---|---|
| L1-L5 | GPT-5.5、DeepSeek V4 Pro、Qwen3.7-Max、GLM-5.2、Kimi K2.7 Code | 5 个 Vanilla LLM 基线 |
| R1 | R2G-Expander Cold | 空历史状态启动，测冻结系统在无预载搜索记忆时的能力 |
| H1-H2 | 一强一弱 LLM + R2G | 可选补充，只在 LLM 负责查询扩展、README 理解或 top/依赖歧义处理时有意义 |

主表采用 **5 LLM + 1 个 R2G Cold，共6组**。没有必要把5个模型全部再跑一遍 `LLM + R2G`；如果 LLM 只是启动 `rtl-expander`，不能形成新的有效对照。

R2G Cold 必须从空的搜索调度状态启动，不允许预载历史仓库、候选列表或搜索产出率统计。它可以使用公开的候选预检工具，但正式 evaluator 只在全部候选锁定后运行，结果不能反馈给方法用于替换失败候选。

### 样本与预算

- 每组由一个顶层 runner 一次启动并自动完成100个唯一 RTL；内部仍保留
  `4批 × 25个` 的锁定检查点，而不是人工启动四次。
- 每个检查点结束后立即写入只读 submission manifest。下一批只接收上一批的
  `(repo, commit, top)` 与 RTL-family 排除表，不接收正式 evaluator 的结果，也不能
  根据前一批得分调整 Prompt、策略或候选。
- Vanilla LLM 的四批使用相互隔离的新会话；R2G Cold 仅在方法开始时初始化一次空状态，随后在四个批次间连续扩展搜索 frontier。
  顶层 runner 负责累计数量、去重和资源，不允许方法自行遗漏已发现记录。
- 唯一性同时检查 `(repo, commit, top)` 和 RTL family，避免 fork 或同源变体重复计数。
- 每批锁定后不能换人，也不能把后一批的剩余额度借给前一批；不足100个的名额按失败计算。
- 六种方法统一使用同一个已认证 GitHub 凭据，并由 campaign acquisition lease 串行执行。
  可以同时启动六个后台窗口排队，但一次只能有一个方法搜索、clone 和综合；等待租约的
  时间不计入该方法预算，从而避免共享 API 配额和 CPU 争用污染完成率及时间指标。

| 每批25个的资源 | 上限 |
|---|---:|
| 墙钟时间 | 6小时 |
| Vanilla LLM Token | 2,000,000 |
| Vanilla LLM 最大轮次 / 单轮最大输出 | 100 / 4,096 Token |
| 搜索请求 | 120 |
| CPU / 同时综合 | 4核 / 1个 |

一次完整运行的总上限等价于原四批预算，即24小时、8,000,000 Vanilla LLM Token
和480次搜索请求；每批仍分别受上表约束，未使用预算不能跨批转移。R2G 外部 LLM
Token 为0，但使用相同的时间、搜索、clone、综合和CPU限制。正式冻结前先用一个
不计入论文成绩的小型 canary 验证 Prompt、JSON schema、计数、停止条件、许可证解析、
clone 和 synth-precheck 链路；canary 通过后冻结代码、Prompt和预算，再从空目录开始正式实验。
Canary campaign 必须在 manifest 中永久标为 `non_scoring_canary`，其缩小目标、短
revision batch 和诊断语料不能用于正式成绩。正式 campaign 固定为 `formal`，并绑定
可追溯 ORFS commit、PDK 路径和只读 benchmark contamination registry 的完整 digest。
工具链固定为 ORFS `a5ff7ef7` checkout 自带的 Yosys `0.64`、OpenROAD
`26Q3-318-g6b9d7fb806` 和 `/home/yangao/.conda/envs/eda/share/pdk`。Manifest
同时绑定可执行文件绝对路径与版本；任一 commit、路径或版本不匹配时必须在方法启动前
fail closed，不能回退到系统 `/opt` 工具。
自然耗尽公开轮次或 R2G 内部 finalization 失败属于方法失败，缺失名额照常计分；只有
可确认的外部 provider 故障或人工中止才允许不计分重跑。
API 预检使用与正式运行相同的单轮最大输出上限，以同时检查模型身份、工具调用、usage
返回和网关预扣额度；小请求可用但正式额度不足时不得标记为 ready。

### Prompt 核心

所有 LLM 获得语义相同的 Prompt，并明确告诉它们评分规则和剩余预算：

```text
从公开代码仓库寻找100个唯一、开源、可综合、允许公开发布的
Verilog/SystemVerilog RTL。任务由runner一次启动，按4个连续批次执行，每批最多
锁定25个；批次之间不得重复。每个候选必须提交固定的仓库URL和commit、可综合
top module、完整有效的编译输入闭包、仓库内许可证文件路径、规范SPDX标识和
provenance。你必须维护已发现、已检查、已拒绝和已提交候选的结构化记录。

主实验候选在统一 Sky130HD 预检下必须满足 `100 <= mapped cells < 100000`；
过小或超大设计只能记入审计日志，不能占正式提交名额。每批至少覆盖12个独立
仓库，同一仓库最多提交4个候选，并尽量覆盖不同功能类别和三档规模。

可以使用统一的搜索、Git、终端、Yosys/ORFS synth-only 和候选预检工具；可以修复
top 选择、source/include/package/define 闭包、编译顺序和构建配置，但不能改变 RTL
功能。每批达到25个合规提交，或达到该批时间、Token、搜索请求中的任一上限时立即
停止并锁定manifest。锁定后不得替换候选，也不能看到正式evaluator结果。四批全部
结束或总计锁定100个候选后，runner终止并输出最终JSON结果。

评分以独立evaluator为准：综合通过但许可证缺失仍算最终失败；重复设计、错误top、
不完整编译闭包、不可复现commit、规模越界或provenance不完整也都不能获得最终合格分。
```

### 判分

最终成功以 Publishable Qualification 为准；Technical Qualification 用来区分“RTL 本身不可综合”和“RTL 能综合但不能合法发布”。

| 指标 | 含义 |
|---|---|
| Submission Completion / 100 | 在预算内按 schema 锁定的唯一、in-scope 候选数；空缺名额直接计为失败 |
| Technical Qualification / 100 | repo/commit 可复现，top 正确，编译闭包完整，属于 Verilog/SystemVerilog，并且独立 Sky130HD 综合成功、网表非空、没有 unresolved module |
| Publishable Qualification / 100 | Technical 全部通过，同时不是重复设计、provenance 完整、仓库内许可证存在且 SPDX 判定一致；这是论文的主要成功率 |
| Diverse-qualified yield | 每批只对最终合格 RTL 计算 `effective repository count / 25`，主表报告四批均值及离散程度；同时报告100个结果的独立仓库数和RTL-family数 |
| Token、时间、费用 | 报告完整100-candidate运行的总量及每批分布；资源效率不能补偿资格失败 |

附表再报告：独立仓库/RTL family 数、最大仓库占比、规模和功能分布、方法间重复率、总共得到多少不重复 RTL，以及失败原因。

具体计分示例：

- 综合和许可证都通过：Technical、Publishable 都通过，最终成功；
- 综合通过但许可证缺失或 SPDX 不一致：Technical 通过，Publishable 失败，**最终仍算失败**；
- top/闭包错误、综合失败或存在 unresolved module：两项都失败；
- 少于100 cells 或达到100000 cells：不符合主实验规模门，提交后按资格失败；方法可在锁定前继续寻找替代候选；
- RTL 可综合但与其他候选同源重复，或 provenance 不完整：Technical 可以通过，Publishable 失败；
- 不足100个的空缺名额：两项都失败。

原始搜索可以产生超过100个候选，但它们只是方法内部候选池，不是正式提交，也不能
把“发现数”写成“合格数”。本实验不再单设规模能力附加实验；Expander 的供给能力由
Publishable Qualification、完成100个提交所需时间、合格来源多样性和失败漏斗共同体现。

---

## 实验二：ORFS/Signoff Repair

### 目的

将实验一所有满足主规模范围的合格 RTL 去重后，在统一 Sky130HD、100 MHz 和 Default ORFS 条件下建立公共题库，比较不同方法能否把真实物理失败修到 strict clean。过小设计缺乏结构价值，`>=100000 cells` 的设计进入单独 large-design 附加实验，不进入主体 baseline；该上限来自既有 Pilot 中四个 ORFS 超时样本均超过约109k cells 的观察，可显著减少只因规模耗尽预算的 Inconclusive。

### Baseline 三分类

每个 RTL 独立运行两次 Default ORFS；结果不一致时运行第三次，只有 `2/3` 一致才分类。

| 类别 | 定义 | 用途 |
|---|---|---|
| Baseline Clean | 可重复 strict clean | clean sentinel，检查修复方法是否引入回归 |
| Repair Challenge | 可重复出现相同的非环境物理失败 | 实验二主要修复题 |
| Ineligible / Inconclusive | 环境/输入问题、结果不稳定、只有超时或不在公共动作空间内 | 不进入修复成功率分母，但保留数量和原因 |

第三类不能叫“不可修”：它只表示当前协议无法公平判断。只要失败可重复、工具检查真实存在且公共动作空间允许处理，就可以进入 challenge。

### 公共题库

不预设 Repair Challenge 数量。实验一去重池完成 baseline 分类后，所有满足定义的 Repair Challenge 全部进入实验二，最终直接报告实际题数 `N`。不提前寻找或验证 clean 解，也不根据 R2G、LLM 或已知 Recipe 的成绩筛题；如果所有方法都无法修复某个 RTL，就诚实记录为共同未解决。

Baseline Clean 不进入修复成功率分母，只按冻结的分层抽样规则选取一部分作为 non-regression sentinel。题库同时报告 pin placement、DRC、routing/congestion、timing、PDN、LVS 等失败分布、设计规模和仓库来源；hard macro 单独报告。

正式题库必须在 Agent 和 knowledge `K0` 冻结后建立。历史上用于开发 Recipe、Prompt 或评分器的 RTL 不进入正式主成绩。

### 对照组

| 方法 | 说明 |
|---|---|
| Default ORFS | 不诊断、不修复 |
| GPT-5.5 Vanilla | 通用 LLM |
| DeepSeek V4 Pro Vanilla | 通用 LLM |
| Qwen3.7-Max Vanilla | 通用 LLM |
| GLM-5.2 Vanilla | 通用 LLM |
| Kimi K2.7 Code Vanilla | 通用 LLM |
| Full R2G | 完整 diagnosis、Recipe、memory、A/B 与 signoff gate |

实验二共比较 Default ORFS、5个 Vanilla LLM 和 Full R2G。所有智能方法处理完全相同的 Repair Challenge。

Full R2G 对每个 RTL 都从同一个只读 `K0` 独立开始，不能在实验二测试集内部边跑边学习。持续学习能力留给实验三。

### 给模型的输入与权限

所有方法获得相同 RTL、top、100 MHz 配置、原始 Default ORFS baseline 日志、公共 Gate 结果、动作边界和资源预算。

LLM 可以读取日志、修改 allowlist 内的物理设计参数、运行 ORFS 并提交 checkpoint；不能修改 RTL、top、平台、主时钟、SDC 目标、检查集合或 signoff deck。

不提供 failure family 标签、人工根因、R2G Recipe、参考修复参数或其他模型结果。

### Prompt 核心

```text
请在固定的 Sky130HD、100 MHz 条件下，将给定 RTL 修复到严格 signoff-clean。
首先阅读未经修改的 Default ORFS baseline 日志并自行诊断。只能在公开边界内修改
allowlist 中的物理设计参数，并且最多运行规定次数的完整流程。

成功要求全部 ORFS 阶段完成，route、DRC、LVS、antenna 违例均为0，setup/hold
时序通过，RCX/SPEF 完整，所有产物 provenance 一致。不得降低频率、放宽约束、
修改 RTL 或使用旧报告。第一次达到合法 strict clean 后立即锁定；预算耗尽仍未
成功时，提交最终失败结果。
```

### 资源与判分

每个 `method × RTL` 建议最多：4小时、4核、1个并发 ORFS、4次 full-flow-equivalent、50轮交互。LLM Token 上限应在修复日志读取工具后通过 canary 统一确定，不能按模型单独调整。

最终主要比较：

| 指标 | 含义 |
|---|---|
| Repair success rate | Repair Challenge 中达到 strict clean 的比例 |
| Non-regression rate | Clean sentinel 保持 strict clean 的比例 |
| Token、时间、费用、ORFS次数 | 成功所需成本 |
| Invalid/regressive actions | 非法动作、约束越界和引入新问题的比例 |

所有方法处理同一批 RTL，使用配对比较。macro 和基础设施无效运行单独报告。

---

## 实验三：R2G 消融与持续学习

### 目的

实验三不再比较不同 LLM，而是回答两个问题：R2G 的 Recipe、memory、ranking 和 A/B lifecycle 分别有什么贡献；A组经验能否改善同类但独立的B组 RTL。M0-M3评价当前已经实现的学习闭环；是否增加能够提出新 Recipe 的 M4，必须在正式冻结前单独决定。

当前 R2G 学习的是“已有动作中选哪个、按什么顺序和参数执行、是否 promotion”，不会自动发明新的修复算法。因此实验三主要验证**成功率、效率和安全性是否改善**，不能预设学习后一定能解决原本无解的问题。

### 先做动作覆盖审计

正式实验前，先用 development/Pilot RTL 将问题分为三类：

| 类别 | 含义 | 预期学习效果 |
|---|---|---|
| Catalog-solvable but misranked | 已有动作能够修复，但当前排序或参数选择不好 | 可能提高成功率和效率 |
| Catalog-solvable and already solved | 当前 R2G 已能稳定修复 | 主要减少时间和尝试次数 |
| Catalog-uncovered | 现有动作集合没有可行修复 | M0-M3基本无法解决；用于判断是否值得开发M4 |

该审计只用于说明 R2G 的能力上限和完善实验指标，不能根据结果挑选对 R2G 有利的正式 RTL。正式结果应同时报告三类的实际数量。

### 分级消融

| 模式 | 启用能力 | 主要比较 |
|---|---|---|
| M0 | 确定性诊断和基础动作 | 无 Recipe 的基础能力 |
| M1 | M0 + 冻结 Recipe Library | `M1-M0`：Recipe 贡献 |
| M2 | M1 + memory、negative evidence、learned ranking | `M2-M1`：记忆与排序贡献 |
| M3 | M2 + A/B、promotion、shadow、demotion | `M3-M2`：受控验证与生命周期贡献 |
| M4（条件项） | M3 + 受限的新 Recipe 提议器 | `M4-M3`：是否扩展可修复问题边界 |

如果 M3 没有真正产生 candidate、A/B trial 或 lifecycle transition，只能报告 `not activated`，不能把 M2=M3 解释为 A/B 没有价值。

M4 不是简单接入一个 LLM API。当前系统已经具备日志、candidate lifecycle、A/B 和 promotion 基础，但修复动作仍主要来自硬编码目录。M4 只有在下面的闭环完整实现并冻结后才进入正式实验：

```text
结构化日志与状态
→ 固定模型提出受限 JSON Recipe
→ schema、allowlist、参数范围和 no-op 校验
→ 隔离沙盒运行与全局非回归检查
→ 独立 RTL 上的 A/B 验证
→ promotion、shadow、demotion 或淘汰
```

第一版只允许生成已有安全动作的**新组合和新参数**，不允许任意 shell/Tcl、RTL 或 SDC 修改。新 Recipe 必须记录不可变 payload、版本、hash、effect fingerprint 和模型来源；未通过 A/B 的 candidate 不能进入 live auto-apply。若这些条件未完成，正式实验只运行 M0-M3，并将 M4 列为后续工作。

### A/B 样本划分

从实验二同一个 Repair Challenge 池中，在查看各方法成绩前预先划分：

- A组：允许写入隔离知识状态，用于 adaptation；
- B组：不参与学习，只用于 held-out evaluation；
- A、B匹配 failure family、规模和失败严重度，但 repo、RTL family、fork 和源码闭包必须独立；
- 每个要声称具有迁移能力的 failure family，至少需要A组2个、B组2个独立 RTL；数量不足时只报告个例，不作迁移结论；
- B组加入少量 Baseline Clean sentinel，检查学习后是否破坏原本 clean 的设计。

实验二仍可以评价全部 Repair Challenge，但实验三的 A/B 身份必须提前锁定。实验三 adaptation 期间不能读取B组日志或实验二对B组的模型结果。

### 三阶段运行

```text
冻结初始知识状态 K0
→ M0-M3在A组独立运行；若M4已冻结则同时运行M4，形成各自K1
→ 在B组分别比较K0与K1
```

M0/M1不积累跨设计学习；M2更新 memory/ranking；M3执行完整 ingest、A/B 和 promotion/demotion；若启用M4，其策略提议只能发生在A组，B组只能读取A组冻结后的已验证状态。不同模式的数据库完全隔离，B组始终只读，一个B组 RTL不能影响下一个。

正式实验不允许人工补 Recipe、手工写知识库、手工 promote 或中途修改代码。未知问题记为未解决；如果因此开发新功能，应冻结新版本后重新运行。

### 判分

| 指标 | 含义 |
|---|---|
| Held-out Learning Gain | B组 `Success(K1) - Success(K0)` |
| Mode Contribution | M1-M0、M2-M1、M3-M2 的配对结果 |
| Repair efficiency | 首次 clean 所需 ORFS 次数、时间和无效动作数 |
| Safety | clean sentinel non-regression、全局回归、有害 promotion 和 negative-evidence 拦截 |
| Learning activity | Recipe 命中、candidate、A/B trial、promotion/demotion 的实际次数 |
| Repair-frontier gain（仅M4） | M4 相对 M3 在 Catalog-uncovered held-out RTL 上新增的 strict-clean 数量 |
| Proposal cost（仅M4） | 模型 Token、候选数、校验淘汰率、沙盒和A/B额外成本 |

所有模式使用与实验二相同的 strict-clean Gate、动作边界和单 RTL 预算。M3 的 A/B arm 消耗必须计入时间和 ORFS 次数，不能作为免费后台成本隐藏。

### 结论边界

如果 K1 只减少时间和尝试次数，结论应写成“经验改善了策略选择效率和安全性”，不能写成“Agent 学会了新的修复算法”。只有 M4 在未参与适配的独立B组上修复了 M3 无法解决的 Catalog-uncovered 问题，才能声称修复能力边界得到扩张；只在A组成功、只复述已有 Recipe，或通过放宽约束获得 clean 均不算。

## 冻结前需要完成

1. 完成 `rtl-expander` 输出到现有 R2G 输入格式的适配并跑 canary。
2. 确定 Experiment 1 的 clone/synth-precheck 上限。
3. 让评分器分别输出 Technical 与 Publishable 结果。
4. 修复 Experiment 2 日志翻页和 Token 统计问题，再冻结统一 Token 上限。
5. 根据正式 Experiment 1 并集记录 Experiment 2 的实际 Repair Challenge 数量和类别分布。
6. 完成 Experiment 3 的动作覆盖审计，并确认哪些 failure family 具备至少 `A=2、B=2` 的独立 RTL。
7. 验证 M0-M3 的能力隔离、K0/K1 快照和B组只读审计确实生效。
8. 根据 Catalog-uncovered 的数量和重要性决定是否开发M4；若启用，先完成受限Recipe DSL、通用执行器、提议校验、沙盒隔离、版本/hash绑定和全局非回归测试，再冻结模型、Prompt和Token预算。
