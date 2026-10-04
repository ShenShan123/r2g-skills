# 实验二 Pilot 执行记录

## 2026-08-11 固定 100 MHz Family-Contract Smoke Pilot

- 已完成 4 种方法 x 4 个 fixture 的串行运行和独立 strict-signoff 复验。
- 最终锁定成绩为 GPT `4/4`、Qwen `4/4`、DeepSeek `2/4`、Full R2G `3/4`；
  四种方法均保持两个 clean sentinel 为 `2/2`。
- 两个 repair-needed fixture 都属于 `footprint_congestion`。GPT/Qwen 为 `2/2`，
  DeepSeek 为 `0/2`，Full R2G 为 `1/2`。DeepSeek 曾生成一个 clean 结果但未在 Token
  预算内锁定；Full R2G 在 `can_fifo` 上以 `catalog_exhausted` 结束。
- 完整范围、证据、成本、局限和实验基础设施修正见
  `experiment2_family_contract_smoke_results_2026_08_11.md`。
- 该轮只用于调试实验合同，不进入论文主成绩；旧的 v16 Fmax Pilot 与本轮固定目标
  repair Pilot 回答的问题不同，不应合并比较。

## 2026-08-08 v16 最终独立复验

- `32/32` 个 `method x RTL` 运行均正常结束，共锁定 27 个 strict-clean checkpoint。
  评分阶段从锁定副本删除旧 signoff 报告并重新运行独立验证，`27/27` 全部通过；未发现
  运行时误报 clean、报告复用或 checkpoint 内容漂移。
- `forencich_axi_interconnect` 的冻结 `280 x 280 um` footprint 无法容纳 1914 个顶层
  I/O pin，因此四种方法共同无合法 checkpoint。该题标记为 `fixture-invalid`，正式能力
  分母为其余 7 个 RTL，而不是将它计作四种方法失败。

| Method | Strict clean | Valid-fixture rate | ORFS flows | Wall time | Provider tokens | Mean normalized Fmax |
|---|---:|---:|---:|---:|---:|---:|
| GPT-5.5 Vanilla | 7/7 | 100% | 64 | 18.60 h | 1,657,733 | 1.000 |
| Qwen3.7-Max Vanilla | 7/7 | 100% | 68 | 17.93 h | 1,752,689 | 0.872 |
| DeepSeek-V4-Flash Vanilla | 6/7 | 85.7% | 54 | 10.13 h | 1,726,088 | 0.679 |
| Full R2G | 7/7 | 100% | 9 | 2.60 h | N/A | 0.700 |

注：表中时间和 Token 排除了无效 AXI fixture；normalized Fmax 逐 RTL 除以该 RTL
在本轮所有方法中的最高 strict-clean 频率，再取算术平均，失败项按 0 计。不同 RTL
的原始 MHz 不直接求平均。Full R2G 本轮不调用外部 LLM，因此 Token 记为 `N/A`，不能
解释为与 Vanilla 模型同一种计费调用的“零 Token”。所有方法人工干预均为 0。

- 可靠性方面，GPT、Qwen 与 Full R2G 在 7 个有效 fixture 上均达到 100% strict-clean；
  DeepSeek 唯一真实失败是 `ultraembedded_riscv_core`，一次运行已达到 route、DRC、
  timing、antenna 和 RCX clean，但 LVS mismatch，随后在日志定位和路径猜测中耗尽预算。
- QoR 方面，GPT 在 7 个有效 RTL 上均找到本轮最高 clean Fmax；Qwen 次之。Full R2G
  的归一化 Fmax 为 0.700，说明其当前策略明显偏向快速、保守地得到可交付结果，而不是
  在预算内积极搜索频率边界。
- 效率方面，Full R2G 仅运行 9 次完整 ORFS，约 2.60 小时；GPT、Qwen、DeepSeek
  分别运行 64、68、54 次。相对 GPT，Full R2G wall time 约减少 86%，但这部分收益与
  较低的频率搜索强度绑定，不能只报告时间而隐藏 QoR 差距。
- 当前 Pilot 支持的最稳妥结论是：结构化 R2G 系统能以显著更少的物理流程运行和时间，
  达到与最强 Vanilla 方法相同的 strict-clean 覆盖率；但若论文主张高性能优化能力，
  还需要改进或单独评估其 Fmax 搜索策略。
- 该结果仍不是正式论文终值。GPT 路由经过 AI帮帮网关且缺少可核验的 provider
  fingerprint，正式论文应准确披露为兼容网关路由，或改用可验证模型身份的官方/可信
  API 重跑。v17 还应修正 AXI footprint，并为所有 Vanilla 方法提供同一受限只读文件
  枚举接口，避免把 EDA 日志路径猜测混入 Agent 能力评分。

## 2026-08-07 v16 中期审计（非最终成绩）

- 当前完成 `29/32` 个 `method × RTL` 组合；最后一个 RTL 已完成 GPT-5.5，Qwen 正在
  执行第 7 次 ORFS，DeepSeek 和 Full R2G 尚未开始。独立 final grading 将在 batch
  结束后由 `r2g_exp2_v16_finalize_20260807` 自动启动，最终成绩以
  `reports/experiment2_pilot_results.json` 为准。
- 在已完成的前 7 个 RTL 中，GPT-5.5、Qwen、DeepSeek 和 Full R2G 分别锁定
  `6/7`、`6/7`、`6/7` 和 `6/7` 个 strict-clean checkpoint；四种方法共同失败的是
  `forencich_axi_interconnect`。这只是运行时锁定结果，不替代删除旧报告后的独立复验。
- AXI 失败不是可用于排名的正常能力题：`280 x 280 um` 固定 footprint 只按 cell area
  生成，却漏掉 1914 个顶层 I/O pin 的周长约束。OpenROAD 明确报告可用位置 1010、
  需要位置 1914，并要求 die perimeter 从 `1120 um` 增至 `2603.04 um`。Full R2G 将
  die 扩到 `769 x 769 um` 后物理实现和 DRC/LVS/timing/RCX 均可完成，但因修改受保护
  footprint 被评分器正确拒绝。因此 v16 的 AXI 单元应标记为 fixture-invalid，不能当作
  四种方法的共同失败。
- Vanilla 的只读接口还缺少受限文件枚举：GPT 在 AXI 的 50 轮中反复猜测不存在的
  backend/log 路径。虽然这次 AXI 即使找到日志也不能合法修改 footprint，但正式实验
  不应把 EDA 诊断变成路径记忆测试。v17 草案增加统一的 bounded `list_project_files`
  和最新 backend run 相对路径；该接口只返回名称/大小，不返回诊断或修复建议。
- 中期频率显示出清晰的效率/优化强度差异：GPT 在多个已完成设计上找到更高频 clean
  checkpoint，Full R2G 通常只执行一次其 Fmax 预测和一次闭环，因此更快但更保守。
  正式分析必须同时报告 strict-clean 成功率、逐设计归一化 Fmax、完整 flow 数、wall
  time 和 Token，不能只比较 MHz，也不能把提前失败造成的低成本解释为高效率。
- v16 的 cohort、task spec 和 evaluator 摘要在运行期间保持不变。v17 修订保存在独立
  cohort/task-spec 草案中；只有 v16 独立评分完成后才会启用，避免运行中更换试题或阅卷器。

## 当前 Campaign 状态

- 有效重跑 Campaign：`/home/yangao/r2g_exp2_signoff_pilot_2026_08_05_v16`
- 状态：2026-08-05 已在 GPT-5.5 Responses 和 Full R2G 两条独立 canary 均通过后，
  启动四方法串行后台队列。GPT-5.5 首轮 Responses function call、逐轮 Token 账本
  和第一轮完整 ORFS 均已通过启动检查。
- 后台会话：`r2g_exp2_pilot_v16_serial_20260805`
- 主日志：`/home/yangao/r2g_exp2_signoff_pilot_2026_08_05_v16/batch_all4_console.log`
- 资源：每个 `method × RTL` 最多 8 小时、4 CPU cores、10 次完整 ORFS；Vanilla
  另限 300,000 provider tokens、50 轮交互和 5 MHz 频率网格。

- `v9`：只作为预算校准，不计正式成绩。三个 Vanilla 方法在小型 UART 上分别使用
  409,084、424,854 和 510,444 Token，并执行 18--19 次 ORFS，证明旧版
  1,000,000 Token/80轮规则会鼓励低收益边界搜索。Full R2G 因只读工作 DB 退出。
- `v10`/`v11` canary：修复只读复制后，又发现环境 DB 与 runtime 默认 DB 分叉；最终
  将所有 Full R2G 入口绑定到每个 fixture 唯一的私有 DB/heuristics。`v11` 在 UART
  上得到 `480.1667 MHz` strict-clean checkpoint，独立复验仍全部通过。
- `v12`：全新 tmux 未继承 AI帮帮凭据，在任何 ORFS 或计费调用前停止，不计成绩。
- `v13`：验证凭据安全加载和实时 Token 账本后，发现 Full R2G 原生 Fmax 使用连续频率、
  Vanilla 使用 5 MHz 网格。为统一方法口径而主动停止，不计成绩。停止检查还发现
  `start_new_session` 子进程可能脱离 tmux；batch runner 已增加信号清理并用真实进程组验证。
- `v14b` canary：Full R2G 的原始预测为 `480.1667 MHz`，按统一规则向下量化为
  `480 MHz`；Agent 内部 strict-clean，锁定 checkpoint 删除旧报告后独立 strict
  signoff 仍通过全部 Gate。环境和默认 knowledge/heuristics 指向同一私有副本，冻结
  seed 保持 `0444` 且 digest 不变。
- `v15`：运行到 PicoRV32 block 时确认 AI帮帮只授权 Responses API，而旧 GPT route
  实际使用 Chat Completions。Campaign 随即停止，全部 GPT 结果作废，整轮不计正式成绩；
  已确认停止时无遗留 EDA 进程。
- GPT Responses canary：`/home/yangao/r2g_exp2_gpt_responses_canary_2026_08_05_v16`
  完成 46 轮 function-calling、10 次 ORFS，使用 200,914 provider tokens，在 660 MHz
  失败后提交 630 MHz checkpoint。删除旧报告后独立 strict signoff 仍通过全部 Gate。

- Campaign：`/home/yangao/r2g_exp2_signoff_pilot_2026_08_05_v8`
- 状态：基础设施无效，不计能力成绩。Qwen/DeepSeek 的 UART smoke 完成后，Full R2G
  合法更新运行时知识状态，却被旧 binding 规则误判为冻结种子变化；后续任务被统一
  拦截。修复采用只读种子、逐测例可写状态和逐测例 Full R2G runtime，下一轮从干净
  Campaign 重跑。
- 平台：Sky130HD
- 主样本：8 个固定、synth-qualified、纯标准单元 RTL（2 small、3 medium、3 large）
- 下一轮方法：GPT-5.5 Vanilla（AI帮帮工程链路）、Qwen3.7-Max Vanilla、
  DeepSeek-V4-Flash Vanilla、Full R2G
- 旧资源：每个 `method × RTL` 最多 8 小时、4 CPU cores、1 个并发 ORFS flow；
  Vanilla 另限 1,000,000 provider tokens。该预算已被 `v9` 校准结果取代。
- 调度：campaign 串行运行，避免 `run_orfs.sh` 固定 CPU `0-3` 时出现跨方法争用
- v8 主日志：`/home/yangao/r2g_exp2_signoff_pilot_2026_08_05_v8/batch_available_console.log`

## 已验证的闭环

- Full R2G smoke 在 `ben_marshall_uart_tx` 上完成 Fmax 搜索，并按所有方法共用的
  5 MHz 网格锁定 `480 MHz` strict-clean checkpoint。
- 从锁定副本删除旧 reports 后独立重跑 strict signoff，仍通过 flow、route、full DRC、LVS、setup/hold、antenna、RCX 和 same-run provenance 全部 Gate。
- Vanilla smoke 能读取冻结 RTL/配置，执行 ORFS，调用公共 validator，并在继续升频前保留已有 strict-clean checkpoint。
- 实验工具测试共 86 项通过，覆盖 source/footprint/clock mutation、timing exception、
  DRC/LVS/route/timing/antenna/RCX failure、foreign report、artifact mismatch、
  missing/NaN evidence、路径边界、可写工作 SQLite、统一 Full R2G knowledge 路径、
  Token 记录、Responses function-call 往返、Responses usage、统一频率网格和批处理
  停止时的进程组清理。

## Infrastructure Shakeout

以下目录只用于评分器和 runner 调试，不进入 Pilot 成绩：

- `v1`：发现凭据加载 API 使用错误，以及 Full R2G 意外使用空的共享 knowledge DB。
- `v2`：验证冻结 knowledge snapshot 的 Full R2G smoke 仍 strict-clean。
- `v3`：发现 batch console 的延迟创建目录问题，尚未启动模型或 EDA。
- `v4`：发现 Vanilla 不能读取冻结 RTL，且并行方法共享 CPU `0-3`。
- `v5`：GitHub TLS 在准备第一个仓库前中断，记为 infrastructure-invalid。
- `v6`：验证修正后的 RTL 只读接口；随后因 CPU affinity 争用停止。
- `v7`：验证串行调度；随后发现 model route 配置尚未由 campaign digest 绑定。

`v8` 证明仅隔离 `R2G_KNOWLEDGE_DB` 不足：部分旧模块仍会更新 Agent runtime 内按
相对路径定位的知识派生文件。修复后的 campaign 将冻结 seed 放到 runtime 外，只对
seed 做不变性校验，并为每个 Full R2G RTL 复制独立 Agent runtime。Campaign manifest
继续绑定 cohort、task spec、evaluator、controller、Vanilla runner、batch runner、
model route、Agent commit、ORFS commit 和只读 seed digest。
