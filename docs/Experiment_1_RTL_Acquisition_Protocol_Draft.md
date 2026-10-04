# 实验一：RTL Acquisition 对比实验协议

版本：`1.5`  
状态：Prompt、公共预检与独立评分器已对齐，完成一次干净运行后再冻结；此前 Pilot 均仅用于协议和实现调试  
修订日期：`2026-08-02`

每次 campaign 初始化时除绑定本协议、task spec、submission schema 和 seen 清单外，
还必须单独绑定 evaluator、Vanilla/R2G runner、submission builder、route preflight、
route configuration 和 manifest schema 的 SHA-256。这样本地尚未进入 Git 的 Pilot
实现也不会落在复现证据之外。

## 1. 实验目标

实验一回答一个独立问题：在相同任务、工具和预算下，冻结的 R2G RTL acquisition
是否比通用 LLM 更稳定地找到来源清楚、编译闭包完整、可综合且具有结构多样性的
开源 RTL。

本实验只评价候选发现、筛选、编译输入闭包和 synth-only qualification，不把后续
完整 ORFS、signoff 或 graph publication 成功率混入主结论。

## 2. 已确定的实验条件

### 2.1 被比较的方法

- `LLM-Vanilla`：每个参评 LLM 使用相同的通用搜索、Git、终端和 EDA 工具，但不得
  使用 R2G 代码、知识库、Recipe、历史修复规则或 closure 策略。
- `R2G-Pipeline`：在冻结 task spec、配置、代码和知识库快照下独立运行的 R2G
  acquisition pipeline。启动它的宿主 LLM不参与关键词选择、候选取舍或修复决策，
  结果归属于 R2G pipeline。
- 主实验暂不增加 `每个 LLM + R2G` 的组合条件。R2G 主路径主要是确定性执行，重复
  更换启动模型不能形成有意义的独立系统条件。

`Vanilla` 不是某个模型名称，而是“该模型只能使用统一通用工具、不能使用 R2G
专有能力”的实验模式。

R2G-Pipeline 的互联网发现由确定性脚本完成，不需要 LLM：它用冻结 seed keywords
调用 GitHub、GitLab 和 Gitee 的仓库搜索 API，按仓库元数据和固定规则排序，分批
clone 后扫描 `.v/.sv`，推断 top 和编译闭包，执行 Sky130HD synth-only、去重和质量
筛选，再进入下一轮。R2G 的模型 Token 因而为0，但搜索调用、Git流量、wall-time和
EDA计算仍按正常资源记录。实验必须关闭可选的 LLM patch 路径。

Pilot 使用以下7种方法，每种先提交25个候选：

| 方法 | 暂定模型/系统 | 定位 |
|---|---|---|
| OpenAI-Vanilla | GPT-5.5 | 国际闭源旗舰 |
| Anthropic-Vanilla | Claude Opus 4.8 | 国际闭源旗舰 |
| DeepSeek-Vanilla | `deepseek-v4-flash` | 高性价比/轻量路线 |
| Qwen-Vanilla | `qwen3.7-max` | 国内通用旗舰 |
| GLM-Vanilla | `glm-5.2` | 国内通用旗舰 |
| Kimi-Vanilla | `kimi-k2.7-code` | 代码与 Agent 专用模型 |
| R2G-Pipeline | 冻结的 R2G commit、配置和知识库 | 完整 acquisition 系统 |

这不是六个同档模型的纯排行榜：DeepSeek V4 Flash 明确作为效率条件，Kimi Code
明确作为代码专用条件。论文同时比较合格候选数、Token、成本和时间，并按模型定位
解释结果，不能把所有差异简单归因为模型厂商。正式运行前还必须通过各供应商 API
或所选聚合网关的模型列表确认准确 model ID。调用渠道按模型分别选择，以稳定、
可访问和可核验为原则，不强制全部使用官方 API或同一个聚合平台；但该渠道不能
附带额外的专属搜索、隐藏 Agent 或未声明的工具能力。有日期快照时优先冻结不可
漂移的 snapshot ID。若只提供滚动别名，则保存 `requested_model`、响应返回的
`actual_model`、调用日期、API提供方/网关和供应商原生版本指纹（若有），不额外
搭建复杂版本追踪系统。

### 2.2 候选数量和批次

- 每种方法提交 100 个唯一候选，分为 4 个独立批次，每批目标为 25 个。
- 唯一候选的主键为 `(repo URL, commit, top module)`。
- 修订后的下一批先作为新 Pilot。只有该 Pilot 后不再修改 prompt、schema、
  evaluator、工具或预算时，才可锁定为正式 Batch 1；此前版本的 Pilot 不计入正式
  成绩。
- 每种方法每批最多运行6小时；提交满25个 in-scope 候选或达到6小时，任一条件先
  发生即停止。6小时仍不足25个时按实际数量提交，缺少的位置记为未完成。
- 每个 Vanilla LLM 每批最多使用2,000,000个供应商报告的 total tokens。达到25个
  候选、6小时或2,000,000 tokens 中任一上限即停止。该上限由300,000-token Pilot
  校准后提高，避免实验主要测量模型能否在进入综合前幸存。R2G-Pipeline不调用 LLM，其模型
  Token为0；实际 API费用另行记录。
- 被测方法在任务开始前获知准确的 Token、搜索次数和 wall-time 上限；runner 在每轮
  确定性状态中报告已用量、剩余量和当前已通过 `validate_candidate` 的候选数。该公共
  预检依次执行 candidate schema、固定 source 身份、活动编译输入闭包、仓库内许可证
  与 SPDX 匹配，以及冻结的 Sky130HD 综合 Gate。只有全部通过的候选才获得提交资格，
  并在通过时立即持久化。该状态只公开资源、硬 Gate 和计分事实，不替模型规定搜索
  关键词、候选顺序或具体策略。
- 每种方法每批最多执行120次仓库或网页搜索请求，翻页按一次新请求计数；读取已发现
  仓库的页面、文件或 API详情不计为搜索。所有查询、页码和后端均写入审计日志。
- 不再单独限制 clone 数量或方法自行执行的 synth 次数；两者必须完整记录，并由
  6小时 method runtime 总上限约束。方法侧的自测结果不直接计入正式成绩，独立
  evaluator 对每个最终提交候选统一执行一次冻结的 Sky130HD synth-only
  qualification。
- 6小时只约束 method runtime，独立 evaluator 的统一 qualification 时间另行记录，
  不占用被测方法预算。
- 达到硬预算上限仍不足 25 个时停止运行，缺少的位置记为未完成，不能无限延长时间
  或事后补齐。
- 模型主动提交少于25个候选时记录为`submitted_early`，该运行可以计分，缺少槽位按
  失败处理。`provider_failure`和`operator_abort`只形成审计记录，不得锁定、评价或
  计入模型成绩；经过有界重试仍失败时，必须在相同冻结条件下从干净目录重跑。
- 方法可以在形成最终 submission 前发现或内部筛选超过25个原始候选。原始候选数
  不作为论文主指标；方法一旦形成25个满足自身提交条件的唯一候选，就按预先冻结的
  顺序锁定前25个并停止，不得在看到独立 evaluator 结果后替换候选。
- 批量展开可能使原始候选池短暂超过25个，这不算超额提交。正式 submission 必须
  最多包含25个唯一候选；不足的槽位会同时降低提交率、合格率和合格来源多样性。

### 2.3 语言边界

- 主实验只接受 Verilog/SystemVerilog RTL，包括 Chisel、SpinalHDL 或其他生成器
  已经生成并提交到仓库的 Verilog/SystemVerilog。
- 原始 VHDL、Chisel、SpinalHDL 及其他需要额外转换器或生成器的源码统一标记为
  `out_of_scope_language`。
- out-of-scope 项目不占 25 个有效候选名额，也不进入主成功率分母；必须保留 URL、
  commit、语言和发现方法，并单独报告数量与占比。
- 方法可以在原有冻结预算内继续搜索 in-scope 候选，但不能因为遇到其他语言而增加
  Token、wall-time 或搜索调用上限。

该边界来自实际 canary：简单 VHDL可经 `vhd2vl` 工作，但多文件闭包、package 和
GHDL-ORFS 集成尚未形成稳定能力；原始 Chisel/SpinalHDL也没有生成器适配链。因此
将其混入主分母会同时改变语言和工具链，削弱实验归因。

### 2.4 统一任务与工具

- 所有方法接收同一份冻结 `task_spec`，其中包含自然语言目标、输出 schema、多样性
  要求、基础 seed keywords、允许的平台、工具和预算。

冻结的核心任务文本为：

> 在给定预算内，从公开互联网仓库中寻找25个唯一、开源、可综合的
> Verilog/SystemVerilog RTL设计候选，用于 Sky130HD synth-only qualification。
> 每个候选必须提交固定 commit、top module、完整编译输入闭包、仓库内许可证相对
> 路径、规范 SPDX 和来源信息。候选只有通过公共 `validate_candidate` 的全部硬 Gate
> 才可提交；最终评分器会从干净 clone 独立重跑相同 Gate，看到结果后不得替换候选。
> 主指标为有效唯一提交数/25、独立合格数/25，以及只用独立合格候选计算的
> diverse-qualified yield。候选须满足 `100 <= mapped cells < 100000`；每批尽量覆盖
> 至少12个仓库且每仓库最多提交4个候选。缺失名额按失败计。排除 testbench、空壳、重复设计及
> 需要额外语言转换器的原始源码。允许从统一的12个起始类别自主扩展关键词，并须
> 记录全部查询和工具操作。

预算、工具权限、seed categories 和最终 JSON schema 由同一个 runner 以机器约束
附加，不为不同方法改写核心任务。

- 共同的12个起始类别为：`processor`、`controller`、`accelerator`、
  `interconnect`、`communication`、`cryptography`、`DSP`、
  `memory controller`、`UART`、`I2C`、`SPI`、`Ethernet`。
- 各方法可以自主扩展、组合或重写搜索关键词；扩展策略属于被测 acquisition 方法的
  能力。所有实际查询必须写入审计日志，实验人员不得根据中间结果追加关键词或提示。
  R2G如何扩展以冻结代码的实际行为为准，不为正式实验临时增加人工查询。
- 主实验统一使用 Sky130HD 做 ORFS/Yosys synth-only qualification，与后续
  ORFS/signoff、消融和 graph 主体实验保持同一平台。
- LLM可以采用自由策略，包括直接搜索、编写临时脚本或组合两者，不强制拆成不同
  实验组。
- 所有 LLM使用相同的标准仓库搜索、HTTP 只读、Git、终端、Yosys 和 ORFS
  synth-only 接口。
- 所有 Vanilla LLM 使用同一个 `validate_candidate` 公共预检。该工具与独立 evaluator
  复用同一套闭包、许可证和综合 Gate 判定代码；预检结果只决定提交资格，最终成绩仍
  以 evaluator 重新 clone 固定 commit 后的独立运行结果为准。
- 不允许使用供应商额外提供的专属 deep-research/deep-search 能力。实验比较的是
  LLM在统一工具环境中的能力，不是完整商业产品套餐。
- Vanilla LLM可以自行运行相同的 Yosys/ORFS 工具，但不能调用任何 R2G 脚本、
  memory、Recipe、closure、评分规则或知识数据库。
- 六个 LLM均开启供应商原生推理模式，并固定使用供应商推荐的默认推理强度；不使用
  最高推理档，也不在看到中间结果后临时调整强度。供应商返回 reasoning token 时
  原样记录，未返回时不自行估算。

### 2.5 Seen/Unseen 规则

- 不排除 R2G 开发和 debug 阶段曾出现过的仓库，也不因 `seen` 身份重新补位。
- 每个候选依据实验冻结前的历史清单标记 `seen_repo=true/false`。
- 同时报告 Overall、Seen 和 Unseen 三组结果，不能只选择表现更好的分层。
- R2G使用冻结的成熟知识快照；所有方法从空的下载目录和运行目录开始，不能复用
  旧 clone 或旧 EDA 产物。

该规则承认历史经验是 R2G 能力的一部分，同时让读者看清性能是否只来自已见样本。

### 2.6 多样性规则

- task spec 明确要求功能、规模、结构和仓库来源具有多样性；每批目标至少12个
  独立仓库，同一仓库最多提交4个候选。
- evaluator 统一报告：唯一仓库数、最大仓库占比、HHI、effective repository
  count、RTL/netlist 重复率、规模分布、功能类别和结构覆盖。
- 主多样性指标为 `diverse-qualified yield = 独立合格候选的 effective repository
  count / 25`。只对最终合格候选计数，不能靠提交来自许多仓库的无效 RTL 刷高。
- 超过每仓库4个的候选可以保留在方法内部候选池，但不应占正式提交名额。

## 3. 输出与计时边界

每个候选至少提交：

- `repo_url`
- `commit`
- `top_module`
- `rtl_files`
- `header_files`
- `include_dirs`
- `defines`
- `top_parameters`
- `readmem_files`
- `language`
- `license_evidence`
- `discovery_method`
- 方法自己的筛选理由和可选置信度

`seen_repo` 不由被测方法自行填写。独立 evaluator 根据 Pilot 启动前冻结并绑定
SHA-256 的历史仓库清单统一计算该标签。

最终结果通过统一的 `submit_candidates` JSON Schema 提交，包含 `candidates` 和
`out_of_scope` 两个数组。LLM在搜索、Git 和 EDA 操作过程中仍可正常进行工具调用；
只有最终提交必须是结构化 JSON。优先使用统一 function/tool schema，而不是分别
依赖各供应商语义不同的“JSON Mode”。若某个接口无法严格约束 schema，runner 只
允许一次不改变候选内容的格式修复，并将这次调用计入 Token 和时间。

计时分为两部分：

- **Method runtime**：收到 task spec 到提交最终 JSONL，包括 LLM推理、搜索、Git、
  临时脚本、clone，以及方法自行执行的 Yosys/ORFS。
- **Evaluation runtime**：提交后由独立 evaluator 执行的统一检查，单独记录，不计入
  method runtime。

同时保存输入/输出 Token、API 成本、搜索调用、Git 操作、工具运行次数和人工干预。

`license_evidence` 必须给出固定 commit 仓库内的相对文件路径和规范 SPDX 标识。
独立 evaluator 从已固定 commit 的本地 clone 读取该文件并要求声明与观测结果一致。
GitHub 网页或 raw URL 不作为可评分许可证证据，避免评分阶段受网络、限流、页面格式
和链接漂移影响。

网络请求采用统一的有界指数退避，短时TLS、连接重置、HTTP 429和5xx可以在同一
逻辑操作内重试。重试次数和错误写入事件日志；达到上限后停止，不允许无限等待。
由于响应丢失可能造成供应商侧不可见的重复计费，发生最终provider failure的整批
运行不进入成绩，即使过程中已经产生部分候选。

## 4. 独立评价

主 evaluator 不信任方法自己的“成功”标签。每个候选统一检查：

1. 仓库可访问，commit 可解析且固定。
2. 许可证和来源证据可追溯。
3. top、RTL、package、include、macro 和 readmem collateral 闭包完整。
4. 不进行人工语义修改时能够 parse、elaborate。
5. 使用统一 ORFS/Yosys synth-only 配置生成非空 mapped netlist。
6. 候选不是 testbench、空壳、重复设计或无结构价值的小片段。

主实验规模采用可复现的硬门槛：标准综合后满足 `100 <= mapped cells < 100000`；top
至少有一个输入和一个输出，且输出不能全部是常量或直接连线；设计中不能残留
unresolved module 或 blackbox，也不能是 testbench、仅实例化 DUT 的验证 wrapper
或空壳。低于100 cells 的候选仍保留并标记为 `trivial_design`，但不计入 qualified
success。达到100000 cells 的候选标记为 `oversize_design`，进入 large-design 附加集，
不进入主体实验。规模分布按 `100-999`、`1,000-9,999` 和 `10,000-99,999` cells 三档报告，
避免方法通过集中寻找容易综合的小模块获得表面高成功率。

正式 synth-only qualification 冻结为以下配置：

- 平台固定为 `Sky130HD`，并在每次实验记录中绑定同一份 ORFS、Yosys 和 PDK
  版本及其版本标识。
- 只读取候选最终提交的 top module、完整 source/include closure、defines 和 top
  parameters，不从方法侧旧工作目录继承隐含输入。
- 时钟周期统一为 `10 ns`；评分器自动识别 top 的时钟端口，无时钟设计使用
  virtual clock。
- 固定 `ABC_AREA=0` 和 `SYNTH_MEMORY_MAX_BITS=131072`。
- 每个候选只执行一次正式综合，单候选超时为1小时；方法侧自测和重试不计为正式
  qualification。
- 正式通过要求综合返回码为0、没有未解析模块，并由本次运行生成非空 mapped
  netlist。禁止人工语义修复，失败必须保留原始日志和失败分类。

论文主表报告三个能力指标，分母固定为25：

1. **提交完成率**：按 schema 锁定的唯一 in-scope 候选数 / 25。未在预算内提交的
   槽位按失败计入。
2. **最终合格率**：通过独立 qualification 的候选数 / 25。无效提交、未提交槽位
   和 qualification 失败均按失败计入。
3. **合格来源多样性**：独立合格候选的 effective repository count / 25。该指标
   同时要求合格产出和来源分散，不给来自无效候选的仓库计分。

Token、wall-time 和 API成本作为效率信息与这三个主指标并列展示，但不再增加新的
能力得分。原始发现数、方法侧综合数、provenance/closure失败类型、设计规模、
多样性、重复率、Seen/Unseen 分层、out-of-scope 语言和人工介入仍必须完整保存，
默认放入附录或诊断材料，用于审计和解释主结果，不占据论文主表。

R2G现有 `design_quality_score` 和 repository score 只作为方法内部筛选信号，不作为
独立 evaluator 的真值，也不进入三个主指标。

## 5. 公平性和可复现性

- 冻结 Git commit、知识库快照、task spec、seed keywords、模型 API ID、调用日期、
  工具版本、平台、预算和 evaluator。
- 四个批次使用相同规则独立运行，保存原始 JSONL、日志和中间产物。
- 四个独立的25-candidate batch 即为每种方法的四次正式重复，不再额外复制完整
  随机重复。每批从空工作区开始，同一方法后续批次不得重复此前已提交的候选。
- 不因看到 evaluator 结果而删除失败候选、替换困难候选或修改 top/source list。
- 同一方法的四批结果分别保留，并汇总均值、离散程度和95%置信区间；统计推断按
  batch 和 repository 处理相关性，不能把同仓库的多个 top 当成完全独立样本。
- 任何运行中协议偏离、API 故障或人工接管都必须进入审计记录。

## 6. 尚待逐项确认

实验方法现已冻结。六个 LLM 的准确 API snapshot ID、调用渠道和版本指纹属于
运行前 preflight 记录：在正式 Pilot 启动前核验并写入 execution manifest，不因
某个模型只能通过合理的聚合网关访问而改变实验方法。
