# Repair-Needed RTL 任务池构建体系

状态：通用建池方法、六类 registry 和多类真实修复对已验证；正式 prospective pool 尚未冻结  
主平台：Sky130HD  
固定目标：除 timing 专题外统一 100 MHz；timing 使用逐题预注册且修复前后不变的频率；完整 ORFS、strict signoff  

## 一句话结论

不能再靠“不断找 RTL，然后逐个跑完整 ORFS”来碰运气。可靠方案是建立一个三层任务池：

1. **自然 repair pair**：真实默认配置稳定失败，独立合法修复后 strict clean；
2. **真实修复对重放形成的受控 challenge**：把已验证的真实修复效果反向施加到另一个
   strict-clean 设计上，制造可控、可复现但不冒充自然失败的题；
3. **clean sentinel**：原本 clean 的设计，用来检查 Agent 是否过度修复或引入回归。

自然样本保证真实性，受控 challenge 保证数量、难度和类型覆盖，sentinel 保证系统不会
“见题就改”。三类结果必须分别报告，不能合并成一个含糊的成功率。

## 1. 先把对象定义正确

`repair-needed` 不是 RTL 源码本身的永久属性，而是下面这个完整任务实例的属性：

```text
Task = RTL closure + commit + top + platform + toolchain + clock target
     + initial physical configuration + strict check set
```

同一个 RTL 在 40% utilization 下可能失败，在 25% 下可能 clean。因此，池中的基本单位
必须是 `repair-needed task instance`，设计名称只是它的一部分。

正式任务只能在以下条件同时成立时入池：

- commit、top、全部 RTL/header/readmem 依赖和许可证证据已冻结；
- Sky130HD、100 MHz、工具版本和初始配置已冻结；
- 两次独立运行得到相同的非环境物理失败；
- 一个与受测方法隔离的确定性 runner 在公共合法动作域内达到 strict clean；
- synthesis、floorplan、placement、CTS、route、finish、完整 DRC、LVS、setup/hold、
  antenna、RCX 和 provenance 全部通过；
- 修复没有修改 RTL、时钟目标、平台、检查集合或 signoff deck，也没有混用旧报告。

### 1.1 “strict clean”在本实验中的精确定义

Repair-needed 实验评价的是**预注册固定目标频率下的完整物理 signoff**，其机器字段为
`strict_clean_scope=fixed_target_physical_signoff`：ORFS 六阶段完成，route、完整 DRC、LVS、
setup、hold、antenna、RCX、run/report/artifact binding 全部通过，并且运行前重新核验 RTL
closure、SDC、config、附加 Tcl 和 protected-task digest。该任务不搜索 Fmax，所以不要求
`reports/fmax_search.json`。

这与 graph 发布路径中的 `publication_strict_clean` 分开记录。后者还要求 acquisition promotion
与 Fmax/constraint provenance，适用于正式数据集发布；前者适用于 Experiment 2/3 的固定题目
signoff 对比。两种 clean 不得混写。若某个 repair task 后续要发布 graph，必须再通过后者，不能
拿 fixed-target signoff verdict 代替发布门。

### 1.2 25/17 到底是什么标准

`CORE_UTILIZATION 25 -> 17` 只是一条已经由真实实验验证过的 **footprint/congestion
repair probe**，不是 repair-needed 的通用定义，更不能拿来同时证明 DRC、pin、timing 和 antenna
五种能力。25% 基线连续两次失败、17% witness strict clean，只能说明该任务对 cell footprint 或
placement pressure 敏感。若 route 和 DRC 也随面积放宽一起清零，它们仍属于同一个
`footprint_congestion` effect family，不能拆成三类样本重复计数。

全局不变的标准是：两次同签名非环境失败、相同 protected-task digest、一次合法且 strict-clean
witness。变化的是每个 failure family 的症状指标和合法动作。

### 1.3 五类以上 repair-needed 如何定义

| Failure family | 两次基线必须满足 | witness 允许改变什么 | witness 必须证明 | 明确禁止 |
|---|---|---|---|---|
| `footprint_congestion` | placement overflow，或 route/DRC 压力稳定大于 0 | `CORE_UTILIZATION`、`PLACE_DENSITY` 等 footprint/placement knob | route=0、DRC=0、其余 strict gates 不回归 | 改 RTL/频率/deck；把同一面积修复重复算作 route 或 DRC 专项 |
| `pin_perimeter` | 同一 `PPL-0024`，并记录 pin 数、可用 pin 位置和工具要求的 die perimeter | 仅有界 `DIE_AREA/CORE_AREA/margin/aspect ratio` | PPL 消失且全 strict clean；**die perimeter** 为工具要求的 1.0-1.2 倍 | 改端口、删 pin constraint、用无限大 die 获胜 |
| `rule_specific_drc` | 完整 DRC deck 下同一非 antenna rule class 稳定大于 0，且 route/LVS/antenna/timing 不存在并发失败 | routing layer adjustment、cell padding、IO pin region、合法详细布线或 PDN-via repair 参数 | 同一 footprint 下该 rule class、总 DRC 和 route 均为 0 | 改 `CORE_UTILIZATION/DIE_AREA`、BEOL-only DRC、换 deck |
| `timing_closure` | route 完成后 setup 或 hold WNS 在同一预注册频率下连续为负，且 route/DRC/LVS/antenna 均 clean | `repair_timing` 对应的 setup/hold sequence、margin、gate cloning 等 | 同一 SDC digest 和频率下 setup/hold WNS 均 >= 0，其他 gates 不回归 | 降频、改 period/uncertainty、加 false path、删 timing check |
| `antenna_closure` | antenna 连续大于 0，全部 DRC 均可归因于 antenna，且 route/LVS/timing clean | diode/routing repair 开关和有界迭代次数 | antenna=0、完整 DRC=0、LVS clean | 关闭 antenna/DRC 检查、换 deck、只看 route 日志里的中间值 |
| `pdn_floorplan` | 同一 `PDN-0185`，并记录 available width、offset 与 total strap width | 有界 die/core/margin，保持 PDN policy | available width 达到 `2 × offset + total strap width` 且不超过该最低要求的 1.2 倍，并最终 strict clean | 禁用 PDN、换弱化 PDN Tcl、无限扩大面积 |

以上分类按**故障症状和 effect fingerprint**，不按 Recipe 名称。两个 strategy 名字不同但最终
配置差异相同，只算一个 effect；一个面积动作同时改善 route 和 DRC，也只算 footprint 一类。
论文正式声称支持某一类之前，该类至少需要 3 个独立设计的 natural repair pairs。数量未达到
时可以列为 development evidence，但不能写成已经具备稳定泛化能力。

## 2. 为什么采用混合任务池

公开研究给出的共同经验是：高质量工程 benchmark 既要有真实来源，也要有可执行且独立的
验证证据。

- [Phoenix-bench](https://arxiv.org/abs/2605.15226) 为真实硬件问题保存 developer patch、
  fail-to-pass、pass-to-pass、固定 EDA 环境和校验和；这对应本体系中的失败重现、clean
  sentinel、strict verifier 和 provenance binding。
- [SWE-bench Live](https://arxiv.org/abs/2505.23419) 使用自动化、持续更新的任务构建流水线，
  并为每个实例固定可复现环境；这说明正式池应由自动 pipeline 持续补充，而不是人工挑几
  个“好看的例子”。
- [FIXREVERTER](https://www.usenix.org/conference/usenixsecurity22/presentation/zhang-zenong)
  通过反转真实修复模式构造更真实的受控缺陷；本体系对应地只允许反转已经被多个自然
  repair pair 验证的物理配置效果。
- [EDA-Schema-V2](https://arxiv.org/abs/2605.06952) 通过 clock、core utilization 和 aspect
  ratio 的系统参数 sweep 生成 7,776 个 stage-resolved OpenROAD 实例，说明物理难度可以
  在保护任务目标的前提下系统标定。
- [OpenROAD AutoTuner](https://openroad-flow-scripts.readthedocs.io/en/latest/user/InstructionsForAutoTuner.html)
  支持参数 sweep、智能 tune、并行试验、固定 seed 和逐 trial timeout；这些能力适合用在
  **建池与校准**，不应偷偷提供给某一个受测方法。
- [CircuitNet](https://circuitnet.github.io/intro/overview.html) 将 cell density、pin 配置、
  RUDY 和 congestion 作为早期 routability 特征；这些特征适合做昂贵 strict run 前的风险
  排序，但不能直接充当 repair-needed 标签。
- [PostEDA-Bench](https://arxiv.org/abs/2605.06936) 将手工/脚本生成的单规则 DRC 题与真实
  full-flow 后残留的 DRC-Reasoning 题分开评估；本体系对应地把 controlled task 与 natural
  residual 分层，绝不把两者合并成一个自然故障成功率。
- [CLOSER-Bench](https://arxiv.org/abs/2607.16632) 将硬件 closure 视为受预算约束的连续决策
  问题，并记录 simulator、synthesis、STA 和 P&R 调用；这支持本体系同时冻结任务、动作预算
  和工具成本，而不是只报告最终是否 clean。

因此，只用自然失败会数量不足、类别失衡；只用人为困难配置又会缺少外部真实性。二者分层
组合，比单一路径更科学。

## 3. 任务池的四个队列

### A. Natural Repair Queue

来源是未知结果的 prospective RTL 或公开项目，不根据 Full R2G 成绩挑选。默认配置连续
两次出现同一物理失败，独立 feasibility runner 使用预注册动作 strict-clean 后，才能进入
自然 repair pool。这是论文中外部真实性最高的一层。

### B. Controlled Replay Queue

先从自然 repair pair 中提取规范化 effect fingerprint，例如：

```text
same RTL/task goals
CORE_UTILIZATION: 25 -> 17
DRC: 36 -> 0
all other strict gates: non-regressive
```

只有当同一 effect family 至少得到 3 个独立设计支持后，才允许在**没有参与生成该规则**的
strict-clean RTL 上反向重放。不能对所有 parent 机械使用同一强度；应先用冻结的早期风险特征
选择接近物理边界的 parent，再在 development-only 阶段按预注册小阶梯寻找“最轻但稳定失败”
的 challenge。受测 Agent 只能看到冻结后的挑战任务和合法动作域，不能看到 clean parent、
难度搜索轨迹或 golden action。

Controlled challenge 必须满足：

- parent 在相同 RTL、clock、platform 和 checks 下 strict clean；
- challenge 只改变一个预注册 effect family；
- challenge 连续两次稳定失败，且失败不是 environment/frontend 问题；
- hidden parent 或独立 witness 再次 strict clean；
- parent/challenge 的配置、产物和验证摘要均以 digest 绑定；
- 结果标记为 `controlled_replay`，永远不计入自然失败率。

### C. Clean Sentinel Queue

从不同仓库、规模和设计类型中保留原本 strict-clean 的任务。Sentinel 用来测：

- 是否错误应用 Recipe；
- 是否增加无意义 rerun；
- 是否破坏 DRC/LVS/timing/area；
- 是否因 memory 中的相似症状而过度迁移。

### D. Near-Miss Development Queue

稳定失败但当前公共动作域无法 strict-clean 的任务进入 near-miss，不进入正式得分。它们用于
发现新的修复族；只有新增动作经过独立验证、冻结，并在下一版 benchmark 中重新建题后，才
能转入正式池。不能在看到某道正式题后临时扩大动作域。

## 4. 从大量 RTL 到正式任务的筛选漏斗

### L0：冻结协议

先固定工具链 digest、平台、initial configuration、合法动作、每层预算、来源 quota、停止规则和
随机种子。footprint/pin/DRC/antenna 主筛统一使用 100 MHz；timing task 则在 development split
中为每个 RTL 预注册一个固定边界频率。之后才打开 prospective 候选结果。

### L1：源码资格检查，秒级到分钟级

只保留 Verilog/SystemVerilog、明确许可证、固定 commit/top、完整 compilation closure、真实
主时钟且不依赖未提供物理模型的 hard macro。这里失败的是 acquisition/frontend 样本，不是
物理 repair-needed 样本。

### L2：Synth-only 与静态风险特征

提取 mapped cells、面积、top-level port bits、`IO bits / sqrt(area)`、时钟数、memory/macro、
综合 timing margin 和设计类型。优先 interconnect、bridge、MAC、memory controller、复杂控制
通路；极小易布线和明显超预算设计降低优先级。

风险模型只能决定“先跑谁”，不能决定标签。Development split 上训练或调权后必须冻结；正式
池保留至少 20% 分层随机探索，避免只发现模型熟悉的失败类型。

### L3：便宜的物理代理

先跑 floorplan、placement 和 global route，采集 pin capacity、placement overflow、RUDY、
global-route congestion、early timing 和最早失败阶段。无风险候选进入 sentinel 候选；出现
物理风险的候选才进入昂贵 full flow。

### L4：一次完整 Default ORFS

- strict clean：进入 sentinel 候选；
- environment/frontend/synth failure：分流，不纳入物理 repair pool；
- 非环境物理失败：进入重复性验证。

### L5：重复性与可修性验证

用不同 run ID 和干净项目副本重跑失败。症状一致后，由隔离的 deterministic feasibility
runner 按预注册动作序列搜索；找到 strict-clean witness 才入池。探索产生的最佳配置不会暴露
给被评估 Agent。

对 controlled replay，L5 还要执行受预算约束的边界选择：每个强度至少两次独立运行，选择
第一个产生相同非环境物理失败的强度；如果在 operator 的最大修复成本内没有这种边界，就把
parent 保留为 sentinel，而不是继续无上限加压。

### L6：去重、分层与冻结

同时按以下身份去重：

- `(repo, commit, top, closure digest)` 设计身份；
- `(platform, clock, initial config, checks)` 任务身份；
- normalized failure signature；
- normalized effect fingerprint。

同一 repo、SoC 子模块族或等价 effect 不能靠改名反复计数。

## 5. 如何保证“数量够”和“类型丰富”

数量不能只定一个总数，还要定 coverage quota。建议主池至少覆盖：

| 维度 | 建议约束 |
|---|---|
| 来源 | prospective 开源 RTL、独立 benchmark、公开/历史真实 repair pair 分层记录 |
| 规模 | small、medium、large 均有样本，medium 可占较高比例但不能独占 |
| 失败族 | floorplan/pin/PDN、placement/routability、route/DRC、timing/CTS、antenna/signoff |
| 动作族 | geometry/footprint、placement、routing、timing/CTS、antenna；无证据族不强行声称支持 |
| 仓库 | 每个 repo 在同一正式 split 中不超过 2 个，且设计族不能跨 adaptation/test 泄漏 |
| 难度 | easy、moderate、hard 按 clean 所需最小动作成本标定 |

正式最小规模建议：

- Experiment 2：12-16 个 repair tasks，至少一半 natural，另加 4 个 sentinel；
- Experiment 3：8 个 adaptation + 8 个 evaluation，设计族完全隔离，另加 sentinel；
- 若自然样本不足，用 controlled replay 补充，但自然与受控成绩分表报告；
- 若某个失败族没有至少 3 个可信实例，缩小论文 claim，而不是制造低质量样本凑 quota。

筛选预算应由**正式 prospective Pilot** 的命中率决定。当前历史样本经过风险筛选和人工追踪，
不能用 `admitted / screened` 估计自然发生率。Pilot 完成后，用 repair-needed 命中率的 95%
Wilson 下界规划最大筛选量，并预注册停止条件：达到 quota，或达到最大 screened count 后如实
报告该类型稀缺。

## 6. 类型扩展的可靠顺序

1. 先从知识库和旧日志中按 `strategy × symptom × platform` 提名真实案例；
2. 在当前冻结工具链重新执行“双失败 + strict-clean witness”，旧日志本身不算正式证据；
3. 从公开 OpenLane/ORFS 项目的配置修复 commit/PR 中挖掘 before/after pair；
4. 每个新 effect family 累积至少 3 个独立自然 pair；
5. 在 held-out clean parents 上做小规模逆向重放并测有效率；
6. 只有重放成功率、重复性和非回归率达标后，才进入 controlled operator registry。

推荐优先补齐的族是：

- `pin_perimeter`：优先运行高 `IO bits / sqrt(area)` 的互连与 mux/demux；从 `PPL-0024` 提取
  pin 数、可用位置和所需 perimeter，再用不超过工具最低要求 1.2 倍的显式几何验证；
- `pdn_floorplan`：优先运行极小 mapped-area 设计；从 `PDN-0185` 提取 available width、offset
  和 total strap width，witness 只补到 `2 × offset + total strap width` 的 1.0-1.2 倍；
- `rule_specific_drc`：先按 rule class 和 marker 坐标聚类。若 marker 集中在 die 边界，优先
  测试有界 IO region 动作；若分布在内部，再测试 routing/padding/PDN-via 动作。面积变化一律
  分流到 footprint family；
- `antenna repair`：只接收全部 DRC 都能归因于 antenna 的基线，再测试 diode/routing iteration；
- `timing/CTS`：在 development split 中用二分或小阶梯找到“默认略负、合法优化可能转正”的
  固定频率，随后冻结 SDC；基线和 witness 间禁止降频、放宽 uncertainty 或增加 false path。

## 7. 防泄漏与公平性

- Development、Experiment 2、Experiment 3 adaptation、Experiment 3 evaluation 按 repo/design
  family 先切分，再做任何调参；
- Experiment 3 的 evaluation RTL 不进入先前 knowledge DB；
- controlled operator 可以来自 development natural pair，但 parent design 不能进入同一 evaluation
  split；
- 所有方法获得相同初始任务、动作 allowlist、CPU、timeout 和 strict evaluator；
- Full R2G 的 golden witness、repair-pair inventory 和 feasibility 日志对受测进程不可见；
- 每个任务保存 source/config/toolchain/report digest 和所有 run ID；
- natural、controlled、sentinel 的成功率、成本和回归分别报告。

## 8. 入池 Manifest 最小字段

```text
task_id, stratum, repo_url, commit, top_module, closure_sha256, license
platform, toolchain_digest, clock_port, target_frequency_mhz
initial_config, initial_config_sha256, protected_inputs_sha256
strict_clean_scope, fixed_target_sdc_sha256, publication_strict_clean
baseline_run_ids[2], failure_stage, normalized_failure_signature
witness_run_id, witness_config_sha256, normalized_effect_fingerprint
route/drc/lvs/setup/hold/antenna/rcx verdicts
artifact_manifest_sha256, source_split, design_family, difficulty_band
```

任何关键字段缺失、digest 不匹配、报告跨 run 混用或数值非法，都必须 fail-closed。

## 9. 方法本身的验收门槛

这套建池流程只有满足下表才可冻结；“找到了几个失败设计”本身不构成方法有效性。

| 性质 | 冻结前必须证明 |
|---|---|
| 数量可规划 | 在独立 prospective Pilot 上报告各层通过率，并用 repair-needed 命中率的 95% Wilson 下界反推正式筛选预算 |
| 自然样本可信 | 每题两次同签名失败、一次隔离 strict-clean witness、全部 digest/provenance 校验通过 |
| 受控题真实可修 | operator 至少有 3 个独立自然 pair 支持；held-out parent 上选择最小稳定失败边界；hidden witness strict clean |
| 类型不虚报 | 每个正式声称支持的 effect family 至少有 3 个独立自然 pair；不足时缩小 claim |
| 防止过拟合 | 至少 20% 分层随机探索；repo/design-family 隔离；clean sentinels 与 repair tasks 同时评分 |
| 成本可复现 | 保存每层候选数、CPU 时间、wall time、并发度、timeout、失败原因和停止条件 |
| 题目可作答 | reference baseline 的 wall time 不超过单题方法预算的三分之一，从而至少保留两次诊断后重跑机会；否则降为 near-miss |

每次筛选只能产生四种终态：`natural_repair_pair`、`controlled_replay_challenge`、
`clean_sentinel` 或 `near_miss/rejected`。任何无法由机器证据唯一归类的任务不得进入正式分母。

## 10. 当前原型验证

### 10.1 可执行基础设施

当前实验工具已经覆盖候选提名、任务冻结、真实运行、证据装配和 fail-closed 校验：

- `repair_needed_family_registry_v1.json` 冻结六类 failure predicate、动作 allowlist、禁止项和
  witness 条件；
- `build_repair_family_candidate_inventory.py` 只把历史记录当提名，不把旧结果直接当证据；
- `run_repair_family_probe.py` 冻结 repo/commit、完整编译输入、SDC、config、附加 Tcl 和任务
  digest，并在运行前重新核验字节；
- `assemble_repair_needed_family_evidence.py` 绑定两个 baseline 与独立 witness，并生成规范化 effect
  fingerprint；
- `validate_repair_needed_family_evidence.py` 对 task identity、run ID、重复失败、数值范围、动作域、
  全局无回归和 fixed-target constraint attestation 统一判定；
- `audit_repair_family_support.py` 从证据文件和 footprint inventory 自动按 `(repo, commit, top)`
  去重并重算 support matrix，registry 计数漂移时 fail-closed。

相关 30 个单元测试全部通过，覆盖 digest 篡改、run ID 重复、非法数值、降频获胜、超范围扩大
die、跨 family 动作和并发 signoff 回归等正反例。实验 runner 还修正了并行资源配置：`NUM_CORES`
控制每个 flow 的线程数，而会限制 CPU 编号范围的 `ORFS_MAX_CPUS` 不再被误作线程数使用。

### 10.2 真实证据支持矩阵

截至 2026-08-11，新格式 evidence 中有 12 个通过机器校验的 repair pairs；此前的严格 inventory
另保存 6 个 footprint pairs。两套来源分别报告，不把旧 inventory 冒充为新格式记录。

| Failure family | 独立设计数 | 3-design 门槛 | 当前结论 |
|---|---:|---:|---|
| `footprint_congestion` | 6 | 达到 | 可进入正式 family claim |
| `pin_perimeter` | 3 | 达到 | 可进入正式 family claim |
| `rule_specific_drc` | 3 | 达到 | 可进入正式 family claim |
| `timing_closure` | 2 | 未达到 | 有真实证据，但只能作为 development evidence |
| `antenna_closure` | 3 | 达到 | 可进入正式 family claim |
| `pdn_floorplan` | 1 | 未达到 | 仅 development evidence |

“达到”只表示满足当前建池的 3-design 支持门槛，不等于已经证明 Agent 在该类上的泛化成功率；
尤其 pin 的 3 个 design 来自 2 个 repository，正式 evaluation 仍必须按 repo/design family 隔离。

真实证据包括：3 个 `PPL-0024` perimeter 修复；3 个右侧 pin 分布引起的 `m3.2` DRC 修复；3 个
Nangate45 antenna 修复；AXI 460 MHz 与 JESD204B 172 MHz 的固定频率 setup 修复；以及 1 个
由 `PDN-0185` 数值下界推导出的有界 floorplan 修复。所有 witness 都通过完整 route、DRC、LVS、
setup/hold、antenna 和 RCX，且频率、SDC 与检查集合不变。

JESD204B timing 是关键正例：两次 baseline setup WNS 都为 `-0.0160414 ns`，仅设置
`SETUP_SLACK_MARGIN=0.2` 后变为 `+0.081974 ns`。SDRAM 286 MHz 和 310 MHz baseline 均 clean，
因此被拒绝而没有为了凑样本继续扫频。这说明 admission 规则能够阻止事后挑选边界。

### 10.3 负结果同样进入方法结论

- 历史 `simple_i2c` antenna 提名在当前工具链已连续 clean，属于 stale nomination；
- `axil_interconnect` antenna 与 `PPL-0024` 并发，属于 confounded failure；
- `udp_mux` antenna 从 272 降到 6，但 witness 不 clean，属于 near-miss；
- 多个 timing 点 baseline clean 或 witness 仍负，均不入池；
- 多个“极小 RTL” PDN 猜测实际 baseline clean，证明 PDN 候选必须从真实 `PDN-0185` 日志及其
  width/offset/strap 数值提名；
- 随机 clean parent 的统一 40% challenge 为 0/8 入池，证明固定强度逆向构造不是可靠扩池方法。

完整机器结果见：

- `repair_family_real_probe_results_2026_08_11.json`
- `repair_family_real_probe_results_2026_08_11.md`
- `repair_family_support_audit_2026_08_11.json`
- `evidence/2026_08_10/` 与 `evidence/2026_08_11/`

### 可重复执行入口

```bash
# 1. 运行全部 fail-closed tests
python -m pytest -q \
  tools/tests/test_audit_repair_family_support.py \
  tools/tests/test_assemble_repair_needed_family_evidence.py \
  tools/tests/test_validate_repair_needed_family_evidence.py \
  tools/tests/test_run_repair_family_probe.py \
  tools/tests/test_build_repair_needed_pool_inventory.py \
  tools/tests/test_build_controlled_repair_challenge_cohort.py \
  tools/tests/test_retrospective_repair_screen_classification.py \
  tools/tests/test_select_controlled_repair_challenge_boundary.py

# 2. 从不可变 screening records 重建可信库存
python tools/build_repair_needed_pool_inventory.py \
  --output-json docs/experiments/signoff/repair_needed_pool_inventory_2026_08_09.json \
  --output-md docs/experiments/signoff/repair_needed_pool_inventory_2026_08_09.md

# 3. 对冻结的难度阶梯选择最小稳定失败边界
python tools/select_controlled_repair_challenge_boundary.py \
  --input docs/experiments/signoff/controlled_boundary_real_evidence_jesd204b.json \
  --output docs/experiments/signoff/controlled_boundary_real_validation_jesd204b.json
```

## 11. 是否已经“解决”

**“如何科学构建 task pool”的方法已经形成并通过真实多类证据验证；正式 prospective benchmark
pool 仍未冻结。** 当前已经摆脱只靠 25/17 面积修复的单一任务池，六类均有机器定义和真实运行
覆盖，其中四类达到 3 个独立设计门槛，timing 有 2 个，PDN 有 1 个。

正式冻结前只做三件事：

1. 在结果未知的独立 prospective source pool 上运行一次完整 funnel，报告命中率、95% Wilson
   区间、wall/CPU cost 和各终态比例；
2. 补足第 3 个 timing pair 与至少 2 个 PDN pair，或者在论文中明确缩小这两类 claim；
3. 按 repo/design family 隔离 development、Experiment 2、Experiment 3 train 与 held-out split，
   然后冻结 manifest，禁止后续人工补位或根据方法成绩换题。

因此，后续不是继续盲目扩大搜索窗口，而是执行固定流水线：提名、廉价风险排序、两次基线、
隔离 witness、机器入池、按 family quota 补齐。新增样本可以持续进入候选区，但正式测试集一旦
冻结就不再变化。
