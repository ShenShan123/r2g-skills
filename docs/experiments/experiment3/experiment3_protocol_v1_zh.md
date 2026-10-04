# 实验三：冻结提案库与 Recipe 生命周期消融

状态：资源与方法协议已冻结；正式启动仍以 campaign 的机器审计全部通过为准。

## 研究问题

在固定 Sky130HD、100 MHz、相同 ORFS 工具链和相同修复动作空间下，比较：

- M0：无学习策略；
- M1：使用冻结 Candidate Bank，固定顺序；
- M2：使用同一 Candidate Bank，以 A 组证据排序；
- M3：只执行通过折中 promotion 门的 Recipe；
- Pure LLM：在 B 组逐题重新调用模型修复。

主要验证 R2G 的候选结构化、记忆排序和 Recipe 生命周期是否能把 A 组经验迁移到不连接 LLM 的 B 组。

## 数据划分

25 个稳定 Repair Challenge 只能按 baseline 协变量划分：

| Split | DRC | Setup timing | Total |
|---|---:|---:|---:|
| A-propose | 3 | 3 | 6 |
| A-validation | 3 | 3 | 6 |
| B-heldout | 7 | 6 | 13 |

另选 4 个 Baseline Clean sentinel，只计算回归，不进入 25 题修复率分母。

同一 repo、fork、源码闭包或 RTL lineage 不得跨 split。划分器只使用失败族、baseline 严重度、设计类别、mapped cells、baseline 时间和源码身份；不得读取 Recipe、candidate、模型修复结果或 Experiment 2 成绩。

## A-propose

三个提案模型读取相同的 A-propose baseline 证据。每个模型每问题域每轮最多提出 2 个 raw proposal；结构化去重后每轮最多执行 4 个，每问题域 Active Candidate Bank 最多 8 个。固定运行 3 轮，提前达到 Token 或候选上限时停止。

每个模型使用独立的累计 Token 账本。API 调用前按 Prompt 字节数、协议开销和最大输出做保守预算预留，成功后改按 provider 返回的实际 usage 入账；provider 未返回 usage 时保留预留值作为保守成本。API 已返回后立即落盘不含原始回答正文的 receipt（usage、响应哈希和调用身份），随后再做 schema 校验。进程恢复时，已经有 receipt 的调用只恢复账本而不得再次调用模型；格式错误的响应计入调用数和 Token，但不进入 Candidate Bank。

本实验把一次有模型响应的逻辑 API 调用定义为一个 model turn。A-propose 每模型每域最多 3 turn、两域合计最多 6 turn；Pure LLM 每模型每题最多 3 turn。仅因连接失败且没有取得模型响应的传输重试记为基础设施重试，不形成学习 turn；相关失败仍写入账本。

冻结资源上限为：A-propose 每模型跨两域累计最多 60,000 Token；Pure LLM 每模型每题累计最多 20,000 Token；单次响应最多 2,048 output tokens。每个 EDA trial 固定 4 核和 7,200 秒墙钟上限，初始只并行 2 个 trial。完整机器可读口径绑定 `experiment3_resource_limits.json`。

Runner 只允许 schema 中的配置动作。每次 candidate 在独立项目副本运行，返回失败签名、WNS/TNS、DRC、route/LVS、运行时间、动作差异及回归。LLM 不得读取 A-validation 或 B。

动作空间绑定 `sky130hd_100mhz_fixed_task_repair_action_policy.json` 的内容哈希。只允许该政策明确枚举的 ABC、placement timing repair、hierarchical synthesis 与 pin-side 参数；禁止修改 SDC、时钟、die/core footprint、利用率和 signoff check set。

探索轮次只用于调整 candidate，不计 promotion 票。探索结束后冻结 payload 和 SHA256；只有冻结版本的正式复跑可以形成 lifecycle evidence。

## Candidate Bank

Candidate 必须：

1. 通过 schema、allowlist、参数范围、no-op 和固定 100 MHz 保护；
2. 绑定不可变 payload/hash 和来源模型；
3. 至少在一个适用的 A-propose RTL 上产生目标指标改善或 A/B win；
4. 不产生约束破坏、工具链破坏或 clean sentinel 回归；
5. 全部失败、无效或只有不完整基础设施结果的 proposal 被淘汰，但保留 negative evidence。

M1 与 M2 使用完全相同的 Candidate Bank。M1 按冻结顺序最多尝试 top-3；M2 使用 A-only evidence 排序后最多尝试 top-3。

## 折中 M3 Promotion

冻结 candidate 的 A-propose 正式复跑与 A-validation A/B trial 共同构成 promotion evidence：

```text
independent_wins >= 2
and wins > losses
and validation_wins >= 1
and clean_sentinel_regression == false
```

同一 RTL family 的重复 trial 只形成一票；不完整 provenance 和 inconclusive 不计票。`validation_wins >= 2` 另记为 `strict_validation_supported`，但不是 M3 的唯一上线门槛。

M3 在 B 组只执行 `operational_promoted=true` 的 Recipe，不执行 candidate。

## B-heldout

B 组运行前冻结 Candidate Bank、Promoted Bank、排序证据、数据库、Prompt、runner commit、工具版本和随机种子。M1/M2/M3：

- 不连接 LLM；
- 不生成或修改 candidate；
- 不 promotion/demotion；
- 不更新共享数据库；
- 只写 run-local 审计日志。

Pure LLM 不读取 A 组 bank、其他模型对话或 B 组先前结果，并使用相同动作空间、CPU、墙钟时间和 ORFS Gate。

Pure LLM 同样按模型、按题使用独立累计账本和幂等调用身份；网络调用失败可在预算内重试，已取得 provider 响应的尝试不得因 runner 中断而重复消费。

## 指标

- B 组 overall strict-clean success；
- Candidate coverage 与 Promoted coverage；
- 有匹配策略条件下的 conditional success；
- top-1/top-3 success、平均尝试数和墙钟时间；
- clean sentinel 回归率；
- A-propose 与 Pure LLM Token 成本；
- DRC 与 setup timing 分组结果。

若某个增量未实际激活，例如每个问题域只有一个 candidate 导致 M2 无排序差异，必须标记 `not activated`。
