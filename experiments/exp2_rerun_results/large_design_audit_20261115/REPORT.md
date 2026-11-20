# 实验 2 接手核查与大设计采集诊断

核查时间：2026-11-15 12:01（UTC+8）。此文接续用户提供的交接记录末尾任务：调查 R2G 获取大设计的困难，保留当前实验，不重启。IDE 中打开的 N45 CLOSEOUT 属于其他历史实验，本次没有修改。

## 已确认的任务与边界

- 当前正式规格是 v5：四组、每组 200 个设计，70 small / 90 medium / 40 large；每组 200M tokens、48 小时、1,600 次模型调用、960 次搜索、4 核。中间提出过的 100M 版本已回退，不能凭交接中的某一条中间消息修改现有规格。
- small 为 100–999、medium 为 1,000–9,999、large 为 10,000–99,999 个 SKY130HD mapped cells。generic cells 不直接决定正式尺寸分类。
- 原生组在 `exp2-v5b-20261114`；有效 R2G 组在 `exp2-v5s-20261114`。v4、最初 v5 和中间 R2G 重启版本不参与此次正式汇总。
- R2G+GPT 使用 OpenRouter；Codex 使用用户 ChatGPT Pro；保留现有模型路线。
- 当前实验保留原有采集与判定结果。下述对照分析只用于离线诊断，不向运行中的规划器输入原生组仓库清单或评测反馈。

本地实验代码工作树 `/home/yangao/r2g-exp2-rerun`，分支 `exp2-v2`，读取时 HEAD 为 `e80fe617`。该工作树内旧 `experiments/exp2_v2/DESIGN.md` 仍写 100 个设计和 20M tokens；当前规格应以绑定的 v5 JSON 和执行清单为准。

## 实验状态

以下使用 [snapshot.json](snapshot.json)，时间为 12:01 UTC+8。R2G 运行中，文件和各数据库的读取不是全局原子快照。

| 组 | 提交或已完成轮次的选择数 | small / medium / large | 状态 |
|---|---:|---|---|
| Codex | 200 | 70 / 90 / 40 | target_reached，提交已锁定 |
| DeepSeek Harness | 200 | 70 / 90 / 40 | target_reached，提交已锁定 |
| R2G + DeepSeek | 107 | 55 / 38 / 14 | 第 4 轮 discovery |
| R2G + GPT | 104 | 52 / 39 / 13 | 第 4 轮 precheck；表内为第 3 轮落盘选择 |

GPT 组截至快照已完成 304 次 precheck，其中原始 publishable-qualified 为 139（63 / 54 / 22）。这些是当轮仍在积累的检查结果，不能把 heartbeat 中尚未更新的 selected_count 当作实时选择数，也不能直接将原始 qualified 数量等同于经配额、去重、仓库上限筛选后的提交数。DeepSeek 组完成 213 次 precheck，107 个 qualified。

两个清单中协议、规格、schema、预检与实现文件的绑定哈希均匹配。原生组锁定文件的摘要与 manifest 一致，内容与各 runner 的 submission 一致；再次调用现有 `validate_submission` 和 `submission_score_eligibility`，两组均无错误且可评分。**这只说明提交有效，尚不是独立 scorer 的最终合格数。**

Codex 的 `method_result.json` 仍保留最初因 20M schema 被拒的错误；manifest 中的已锁定提交和当前重新校验均已通过。没有将旧错误文件重写为新结论。

原生组资源记录：Codex 85,584,704 tokens、459 调用、77 搜索；DeepSeek Harness 92,768,319 tokens、598 调用、227 搜索。Codex 起止时间约 2.62 小时；DeepSeek Harness 的起止时间差为 **4.98 小时**，但 `method_runtime_seconds` 仅约 **4.47 小时**，差 **1,815 秒（30.25 分钟）**。结合已有 incident，停滞和恢复期间的时间未完整进入累计字段；不能直接把 4.47 小时当作从启动到完成的用户墙钟时间。保留原字段，报告中另列起止墙钟及差额。资源字段中的 `human_interventions=0` 同样不能抹去已有人工恢复和中途修订记录。

磁盘快照 `/data` 剩余约 167.4 GB（十进制）。本次没有清理文件、重启、重新综合或调用付费模型；没有新建定时任务。

## 发现一：已复现的许可证分类缺陷直接损失大设计

证据见 [control_traces.json](control_traces.json)，补查时间 12:00 UTC+8。

完整链条如下：

1. `recover_license_evidence.py:detect_full_text` 看到 BSD-2 与 BSD-3 共有的 redistribution 前言，就一律加入 `BSD-3-Clause`。
2. 再合并 provider 正确的 `BSD-2-Clause`，证据成为 `[BSD-2-Clause, BSD-3-Clause]`。两者同属 permissive，Expander 仍给 PUBLIC_EXPORT_ALLOWED，掩盖了内部标识不一致。
3. `build_experiment1_r2g_submission.py:candidate_from_expander_record` 仅在 identifiers 恰好一个时填写 SPDX；这里因两个标识而填 null。
4. 公共 precheck 在真实 LICENSE 中观察到 BSD-2，但候选未声明 SPDX，因此给 `license_evidence_incomplete`。

GPT 组补查时，14 个技术合格候选符合上述完整错误模式，其中 7 个 mapped-large：

| 仓库 / top | mapped cells |
|---|---:|
| secworks/poly1305 / poly1305 | 23,669 |
| secworks/blake2s / blake2s | 16,843 |
| secworks/aes / aes | 21,708 |
| secworks/xchacha / chacha | 15,496 |
| secworks/sha256 / sha256_axi4 | 11,439 |
| secworks/sha512 / sha512 | 24,365 |
| secworks/blabla / blabla_core | 12,998 |

这些记录证明 R2G 能找到并综合一部分大设计，但在自身证据转换中损失了它们。**7 个是受缺陷影响的候选数，不是已经新增的正式槽位数**；修复后的去重、仓库上限、配额和独立评分仍须另行验证。

已生成 [proposed_bsd_license_fix.patch](proposed_bsd_license_fix.patch)，只修正文识别：规范空白，结合 BSD 正文与非背书条款区分 BSD-2/BSD-3。没有修改正在运行的实现，也没有修改本地实验实现源文件。

[verify_license_patch.py](verify_license_patch.py) 使用当前实现中的纯恢复函数，在临时目录验证 4 份实际 LICENSE（poly1305、blake2s、aes、sha256）：旧版复现双标识，候选补丁恢复唯一 BSD-2。还检查 BSD-3、换行正文、真实混合证据、缺失许可证、仅元数据、MIT/Apache/ISC/GPL 行为；结果在 [patch_verification.json](patch_verification.json)。`git apply --check` 在实验工作树通过。此验证没有重跑综合或改变正式判定。

## 发现二：规划查询的召回与反馈存在明显问题

两组各调用规划器 4 次、生成 32 个查询，但实际日志显示：

| 组 | 已执行规划查询 | 其中零结果 | 返回仓库条目合计 |
|---|---:|---:|---:|
| R2G + DeepSeek | 24 / 32 | 22 / 24 | 3 |
| R2G + GPT | 32 / 32 | 31 / 32 | 50 |

GPT 唯一非空规划查询是 `user:secworks language:Verilog license:bsd-2-clause`；它带来了上述受许可证缺陷影响的仓库。返回条目数不是独立仓库数，也不能与最终大设计贡献直接相加。

查询大量组合多关键词、license、stars、更新时间和 `-fpga` 等条件。日志支持“当前查询召回很低”，但没有逐条件消融，尚不能断言某一个条件单独造成了零结果。DeepSeek 第一轮的 8 个查询 attempts=0，不能把它们写成查询已经执行但没有命中。

代码层面，规划器的 feedback 查询了 `results_seen`，却没有将该字段放进模型反馈；零搜索结果与“已有结果但尚未获得合格设计”混在一起。系统提示仍偏向 medium IP blocks，最终目标却包含 40 个 large 槽位。当前 planner 只在一次完整补充轮开始时调用；它提出查询以后，Expander 可能持续处理许多批次才再次收到反馈。

还有一个固定策略差异：R2G GitHub 搜索强制 `sort=updated`，通常一页 50 条；原生工具未指定 sort，单次最多返回 5 条。因此两者即使写出类似查询，也不必观察到相同仓库；排序策略需要在独立 pilot 验证，不能仅比较查询文字。

## 发现三：发现覆盖、许可证与编译闭包都构成损失

截至快照，DeepSeek / GPT frontier 分别有 8,365 / 8,085 个仓库。Codex 正式提交的 40 个 large 来自 27 个仓库，其中 R2G 两组分别仅知道 2 / 8 个；DeepSeek Harness 的 40 个 large 来自 24 个仓库，其中 R2G 两组知道 4 / 5 个。这里以**实际提交的 large**为对照，不将原生 history 中额外合格但未提交的设计混入。

这说明覆盖不足，但不能据此量化“发现”对总差距的因果贡献。GPT 已知的一部分对照仓库已经下载，之后仍发生许可证与编译闭包失败；仅说“主要是搜索”不够准确。比如 `dpretet/axi-crossbar` 的两个较大 top 在公共检查中出现 `compilation_closure_incomplete`，而该仓库的一个子模块可以通过。

corpus 中 generic 1k–200k 范围的 QUARANTINE 设计，DeepSeek / GPT 分别 93 / 89 个。LICENSE_FILE_ABSENT 分别记录在 87 / 80 个上，LICENSE_UNRESOLVED 为 88 / 85 个；原因可重叠。这些不是 mapped-large 数量，也不是可以直接解除隔离的待释放数量。根目录抽样未看到许可证，与缺失证据吻合；不能因此给无许可证仓库补造许可。

## 发现四：放宽超时、仓库大小与 generic 阈值不是首选

- 新快照已出现 1 / 2 次约 1,200 秒的 precheck 超时，因此交接末尾“没有超时”的旧结论已过时。不过它们仍不足以解释当前发现与证据转换的广泛损失。
- 64 MiB 上限确实排除了部分已知大仓库；但大量对照仓库根本未入 frontier，也存在小于上限且下载成功后被误拒的仓库。先放宽上限无法解决这些已确认问题。
- generic×7 的估计与实际 mapped 分箱，仅在 115/181（DeepSeek）、158/260（GPT）个可配对样本上一致，约六成。它适合排序，不适合作为正式尺寸判定。
- 当前 `generic >= 200000` 被称为“certainly oversized”的跳过依据来自小 pilot 的经验，不能当作映射后单元数的数学保证。后续版本应将它标为启发式预算策略，并单独检查可能的漏采，当前实验不改阈值。

## 后续顺序

1. 保持当前两组 R2G 继续运行；完成后锁定提交，再按原冻结协议独立评分。报告人工恢复、实现/schema 修订和各臂启动时差；不将终止有效性当作最终合格率。
2. 优先在独立后续版本应用本次 BSD 修复，补充真实双许可、嵌套许可证与文件覆盖关系的完整测试。保持公共 gate 不变，不用“任选一个 permissive 标识”的方式绕开真实冲突。
3. 在不使用本次对照仓库清单的独立 pilot 中试验查询逐步放宽、排序混合、生产仓库邻接扩展；给规划器明确反馈 attempts/results_seen/已下载/可发布/各尺寸净槽位收益，并区分待处理与零命中。
4. 将补充目标从 generic-family 总量逐步转为经过闭包与许可证验证后的尺寸槽位收益；缩短反馈间隔。先验证编译闭包恢复，再考虑增大仓库下载和综合预算。
5. 下次正式比较前冻结新实现，统一从起止时间报告墙钟，同时列出模型调用、下载、综合、停滞/恢复分项。原始日志保留，修正口径写在派生报告中。

## 复查入口

- [audit_remote.py](audit_remote.py)：203 上只读采集器，SQLite 使用 mode=ro；不调用模型、下载或综合。
- [probe_controls_remote.py](probe_controls_remote.py)：只读校验锁定提交、跟踪对照仓库和现有失败证据。
- [snapshot.json](snapshot.json)、[control_traces.json](control_traces.json)：此次采集证据，包含路径和摘要。
- [proposed_bsd_license_fix.patch](proposed_bsd_license_fix.patch)、[verify_license_patch.py](verify_license_patch.py)、[patch_verification.json](patch_verification.json)：未部署的候选修复及验证。

本次仅添加本目录的诊断产物；没有推送、修改 campaign、覆盖签核记录或重启任何实验。
