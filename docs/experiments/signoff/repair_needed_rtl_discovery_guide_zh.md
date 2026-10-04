# Repair-Needed RTL 候选发现与正式入选指南

状态：候选发现辅助指南；正式标签以 `repair_needed_task_pool_construction_system_zh.md` 和
`repair_needed_family_registry_v1.json` 为准  
更新日期：2026-08-11

## 目标

这份说明解决一个实际问题：如何从大量开源 RTL 中，快速找到“默认 ORFS 无法严格
clean，但在不降低任务难度的前提下可以被合法修复”的设计。它不是通过失败数量挑最难的
RTL，而是寻找既能稳定复现问题、又保留可修复空间的 challenge case。

## 什么才算 Repair-Needed

一个 RTL 只有同时满足下面四项，才能正式标记为 `repair-needed`：

1. RTL、commit、top、编译闭包、主时钟、Sky130HD、100 MHz 和初始配置已冻结；
2. Default ORFS 在该 failure family 预注册的初始配置下，两次独立运行复现同一种非环境
   物理失败；
3. 独立确定性 runner 使用预注册的公共合法动作后达到 strict clean；
4. route、完整 DRC、LVS、setup/hold timing、antenna、RCX 和 provenance 全部通过。

下面这些情况不能算：源码或综合失败、工具环境错误、只失败一次、修改 RTL、降频、关闭
检查、混用旧报告，以及虽然失败但任何合法动作都无法 strict clean。

`CORE_UTILIZATION 25 -> 17` 只适用于 `footprint_congestion` 探针，不是 pin、DRC、timing、
antenna 或 PDN 的通用入池标准。各类症状、动作和 witness 条件必须从机器 registry 读取。

## 当前实验已经观察到的规律

当前机器化库存从保留的 screening records 中识别出 60 个不同设计，其中 6 个满足严格
入选条件。由于这些候选来自多轮自适应开发筛选，`6/60` 不能作为自然发生率或论文最终
成功率，也不能直接用于估计正式筛选规模。

| 已确认 RTL | 规模 | 默认 100 MHz 失败 | 独立合法修复 |
|---|---|---|---|
| `eth_mac_mii` | medium | 36 DRC | util 17% strict clean |
| `can_fifo` | medium | 10 DRC | util 12% strict clean |
| `nt35510_apb_adapter_v1_0` | small | 2 DRC + 1 route | util 17% strict clean |
| `ultraembedded_sdram_axi` | medium | 60 DRC | util 17% strict clean |
| `mor1kx_ctrl_prontoespresso` | medium | 98 DRC + 2 route | util 17% strict clean |
| `logikbench jesd204b` | medium | 12 DRC | util 17% strict clean |

从这些结果可以得到五条实用结论：

1. **中型 RTL 是目前最有效的筛选区间。** 过小设计大多默认 clean；过大设计更容易在
   source、synthesis 或资源预算阶段失败，未必是有价值的物理修复题。
2. **互连、协议桥、MAC、内存控制器和控制通路更值得优先。** 它们通常具有较多总线、
   较高 pin pressure 和更复杂的局部布线关系。
3. **当前最稳定的 repair-needed 症状是 DRC/route 拥塞。** 已确认案例都能通过预注册的
   footprint/density 动作恢复，和 R2G 当前主要修复能力匹配。
4. **高 I/O pressure 能找到物理困难设计，但不能单独判定可修。** 一些 AXI crossbar 和
   Ethernet 设计在 util 17% 仍无法放置，只有 util 8% 才可能恢复；它们可能“太难”，
   需要先冻结更宽的公共动作域才能进入正式实验。
5. **综合 Fmax 接近 100 MHz 是辅助信号，不是标签。** 它能提示 timing 边界，但无法证明
   route/DRC 可修；必须继续走真实 ORFS 和 strict signoff。

## 推荐的高效筛选漏斗

### 第 0 层：先冻结规则

在查看正式候选结果前，固定平台、目标频率、各 family 初始配置、合法动作序列、候选上限、
来源 quota、风险分数和停止规则。不能发现某个 RTL 需要 util 8% 后，再单独给它增加动作。

footprint 类可以预注册 `17% -> 12% -> 8%` 一类有界 feasibility 序列；其他 family 必须使用
自己的合法动作域，例如 pin perimeter、同频 timing repair、antenna iterations 或 PDN 最小宽度。
任何序列都应在正式候选池运行前决定；所有方法必须获得相同 bounds。

### 第 1 层：秒级源码资格检查

只保留：

- Verilog/SystemVerilog；
- 明确许可证、固定 commit、top 和完整编译依赖；
- 有真实主时钟，优先单主时钟；
- 不依赖尚未提供 LEF/LIB/GDS/CDL 的 hard macro；
- 无动态缺失的 include/readmem 文件。

这一层失败的 RTL 属于 acquisition/frontend 问题，不能送入 repair-needed 统计。

### 第 2 层：Synth-Only 和风险排序

对资格合格的 RTL 运行一次 Sky130HD synth-only，记录：

- mapped-cell 数和综合面积；
- top-level port 数和总 bit 数；
- `io_pressure = top_level_port_bits / sqrt(synthesis_area_um2)`；
- 主时钟数量、memory/macro 情况；
- 可获得时的综合 Fmax；
- 设计类型：interconnect、bridge、MAC、memory controller、protocol/control block 等。

推荐优先级：

| 优先级 | 特征 |
|---|---|
| 高 | 1k-10k mapped cells；互连/控制类；I/O pressure 位于候选池前 20%；Fmax 约 70-150 MHz；历史中出现过真实 placement/route/DRC 症状 |
| 中 | 规模合适但只有部分风险信号，或 Fmax/端口统计不完整 |
| 低 | 极小且端口简单、明显默认易布线，或极大且大概率超出预算 |
| 排除 | 缺依赖、错误 top、无有效时钟、unsupported macro、synth failure、environment failure |

风险分数只决定“先跑谁”，不产生 repair-needed 标签。低风险候选应记录为
`not_screened_low_risk`，不能直接写成 clean。

### 第 3 层：便宜的物理代理

优先只跑 floorplan、placement 和 global route，记录：

- pin placement capacity/error；
- placement overflow 和 density；
- RUDY/global-route congestion；
- setup timing 是否已经远离 100 MHz 目标；
- 失败阶段和机器可验证的错误类型。

默认早停规则：

- frontend/synth/environment failure：排除；
- 到该阶段均无风险：降为 clean-sentinel 候选，不立即跑三次完整流程；
- 出现 placement/route/DRC 风险：进入完整 Default ORFS baseline。

### 第 4 层：正式入选

```text
Default ORFS baseline
-> baseline strict-clean：clean sentinel
-> 非环境物理失败：独立重复 baseline
-> 症状不一致：排除
-> 症状一致：独立运行预注册 feasibility action
-> strict-clean：repair-needed
-> 仍不 clean：hard/unresolved near-miss
```

只有最后一步通过的设计才能进入实验二/三的 repair-needed 分母。第一次 baseline clean 后
立即停止；没有必要继续提高频率制造一个人为失败。

## 如何扩大正式样本量

### 候选来源

候选应分层记录，避免全部来自同一种代码风格：

1. 实验一 synth-qualified 自然候选；
2. 独立开源项目中的互连、MAC、总线桥、控制器和处理器子模块；
3. LogikBench 等固定 benchmark suite，并区分 human/AI source；
4. 历史失败日志只用于改进筛选器和建立 development case，不直接冒充正式 prospective
   held-out 结果。

### 建议规模

当前开发数据的严格命中约为一成。正式准备时可以先按目标 repair-needed 数量的约 10 倍
收集 synth-qualified RTL，再通过风险排序只让前 40%-60% 进入物理代理。必须同时预注册
“目标 quota”和“最大筛选数”：达到任一条件即停止，不能无限寻找直到某种方法占优。

例如目标需要 `M` 个正式 repair-needed，可先准备约 `10M` 个 synth-qualified 候选，
排序后对约 `4M-6M` 个做 placement/global-route proxy，再对约 `2M-3M` 个做完整重复
baseline 和 feasibility。该倍率应在下一轮独立 Pilot 后冻结，而不是根据正式成绩回调。

### 并行执行

- synth-only 可使用 4-8 个窗口，每个 4 cores；
- 物理代理使用独立 CPU affinity、独立 flow variant 和 campaign 目录；
- 一个 fixture 内保持顺序执行，避免 baseline/repair artifact 串线；
- 只让真实物理失败进入第二次 baseline 和 strict signoff，可节省最多的时间；
- 每一步都原子写入 result JSON，tmux 只负责后台运行，最终判断只读取持久化证据。

## 实验二和实验三如何分配

正式入选完成后，再按 symptom/action family、规模、失败严重度和设计类型匹配划分：

- `E2`：比较 Default ORFS、Vanilla LLM 和 Full R2G；每题从相同冻结知识 `K0` 开始；
- `A`：实验三 adaptation 集，允许按预注册顺序形成 `K1`；
- `B`：实验三 held-out 集，只比较 `K0` 与由 A 产生的 `K1`。

`E2/A/B` 的 repo、top、fork、参数变体和主要 RTL closure 必须互斥；高度相似模块不能跨组
证明泛化。候选入选只能依据中性 Default ORFS 和公共 feasibility runner，不能依据 Full R2G
是否成功。

## 正式运行前检查表

- [ ] Agent、工具链、知识库 `K0`、task spec 和 evaluator 已冻结；
- [ ] 候选来源、风险特征、排序规则、top-K、quota 和最大筛选数已注册；
- [ ] 目标频率、各 family baseline 和公共 action bounds 已冻结；
- [ ] 所有 source identity、closure digest 和许可证证据完整；
- [ ] low-risk、baseline-clean、不可修、超时和环境失败均保留记录；
- [ ] repair-needed 有两次一致 baseline 和一次独立 strict-clean feasibility；
- [ ] E2/A/B 在任何被测方法运行前完成不重叠划分；
- [ ] screening cost 与正式方法比较 cost 分开报告。

## 当前可复用工具与数据

- 76 候选风险排名：`experiment2_repair_candidate_ranking_v22.json`
- LogikBench 250 候选清单：`logikbench_repair_risk_inventory_v1.json`
- LogikBench 风险排序：`logikbench_repair_risk_ranking_v1.md`
- 风险排序工具：`tools/rank_experiment2_repair_candidates.py`
- LogikBench scout：`tools/logikbench_repair_scout.py`
- 正式重复/feasibility runner：`experiments/run_experiment2_retrospective_repair_screen.py`

最重要的原则是：**用便宜代理提高命中率，用独立重复证明失败真实存在，用预注册合法修复
证明它不是无解题；风险排序负责省时间，family registry 约束因果，strict signoff 才负责给
标签。**
