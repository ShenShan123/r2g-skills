# R5 S2 主链入口审查：旧 ORFS controller 不能直接迁为 RTL Agent

按 Revision5 §10.4、§16–19 收束 G2 后，重新检查现有 S2 的实际实现。**旧三策略入口是零模型的 ORFS 配置调参流程，不是待填 API key 即可运行的 RTL Agent。** 本阶段完成可重放的只读兼容性审查，未运行三策略 Agent、未授予执行权限，也不增加方法任务或 FINAL 样本。

## 已查实的可复用项与缺口

| 实际输入 | 查实结果 | 对 R5 S2 的含义 |
|---|---|---|
| `research_rc1_controller_v1.json` | `deterministic_skill`；No Persistent Memory / Legacy / TEHM 三策略；禁止 production 和学习 | 策略名称和安全边界可保留，不能冒充真实模型 controller |
| `research_rc1_budget_v1.json` | 模型调用和 token 上限均为 0 | 现有预算不授权模型；不能从其他阶段 push/目录授权推断调用权限 |
| `research_s2_proposal.py` | `flow.CONFIG_DELTA`，只允许 CORE_UTILIZATION、PLACE_DENSITY_LB_ADDON、ABC_AREA；消费 R4 S1 feedback | 不是 RTL 源编辑／受限 binding 的提案入口，不能换路径后直接用 |
| `research_s2_run.py` | ORFS synth→floorplan→place→cts→route→finish；明确 deterministic | 需独立的 RTL candidate/evaluator 接口；不把 ORFS 结果换标签 |
| Legacy baseline | database/schema/heuristics 三者与历史 manifest 完全一致 | 原基线可保全，不应换成空库；并不证明 RTL 适用性或目标无泄漏 |
| gen5 paper plan | deterministic controlled action；模型预算 0；历史 final_tasks 空快照 | 保持原身份，不在原文件中插入 Agent 数据或改写 F1 身份 |

Legacy 的 `build_query/retrieve/propose_activation` 已只读查看：它按 symptom 查找原有 recipes，activation 仍表达 config_edits/rerun/recheck。当前没有核验将 R5 公共 RTL 观察映射进去后的语义，故**没有运行人为编造的 query 来制造零命中**，也没有宣布 Legacy 对 RTL 没用。后续须预先固定公开观察映射、保留原检索算法与证据来源，再核对目标暴露。No Memory 的冷启动能力必须与其他策略共享；不得删去共同动作能力以制造差距。

## 可执行审查与验证

新增 `memory/tehm/evaluation/research_r5_s2_preflight.py`。它只读取显式列出的 11 个输入，校验 Legacy manifest 自摘要及三个实际文件，使用 AST 核对已人工审查入口中的动作域/ORFS 阶段，并记录全部文件摘要。不 import 或执行仓库 controller/backend，不打开 SQL，不调用 recommender、EDA、网络或模型。

复跑命令（输出文件必须不存在）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 memory/tehm/evaluation/research_r5_s2_preflight.py \
  --input-root /data1/zhangdy/Typed-Executable-Hardware-Memory \
  --output /tmp/r5-s2-compatibility-audit.json
```

输出 `valid=true` 仅表示本次有界审查成立，同时明确 `agent_ready=false`、`execution_authorized=false`。AST 字面量不是全仓库不存在其他实现的证明；文件哈希匹配也不授予独立性、功能适用性或答案安全性。

16 项离线反例被拒绝：controller 改称 LLM、删策略、production/在线学习/final learning、非零调用或 token、布尔值冒充预算整数、paper spend、manifest 摘要、Legacy 内容、动作域、允许编辑、ORFS scope、非空 WAL、链接输入。当前合法原始输入与副本审查一致。既有 67 项 paper bookkeeping 检查再次通过；均为软件检查，不是方法实验。

首次开发运行 r1 把非空 SQLite SHM 索引也当作未落盘内容而拒绝；现场 WAL 为 0 bytes，SHM 为 32768 bytes，数据库 SHA 与冻结值一致。r2 修正为拒绝非空 WAL 和任何链接 sidecar；仍只哈希文件、不打开 SQL。不删除 SHM/WAL，不改 baseline，保留完整 r1 输入和失败说明。

## 封存与实际隔离恢复

工作根 `_r5_pilot/agent/s2-preflight-r2/`，第二份 `/tmp/tehm-r5-s2-preflight-_5ke_gxe/`。包内保留 11 个输入、审查器、构建/反例检查脚本和输出。实际恢复只挂载第二副本与系统 Python，原仓库/corpus/网络不可见，重新执行审查器；完整 audit JSON 逐字节相同。两份归档、包内内容、解压文件清单和恢复输出经独立只读命令核验。它恢复的是本次**完整离线审查 case**，不是新的 RTL 模拟或 Agent 运行。

| 工件 | SHA256 |
|---|---|
| auditor | `0962b92ec6d561afa31cc437b27d17eb977e528f86bd40d7dd95eef3865a9324` |
| audit / recovered audit | `ee657b3dfcd8b85221f1e8e32fa5741461aba9adfdcf912ba5ca87383d9a26fc` |
| original evidence archive | `995e68569f5855a8c23ef11ed3285d70233e7c173ff5aeb6ef66637756386d5b` |
| r1 initial-failure supplement | `38cf749d827f7a3395675cce43998bba14aa208dc43e8e12cadcea7ca2263251` |
| r2 seal | `3bb3fd52e6300e34afbe21e72ee8864833e4f0e7349721cf6e2a5121947f293b` |

副本仍在同机 `/tmp`，不声称长久托管。原始私有数据库与 corpus 不提交 Git；只提交审查器、报告和索引。无真实模型、EDA、Memory 更新、upstream 修改或 push。

## 下一步与完成边界

1. 在新 S2 开发代次实现共享 RTL proposal/action/evaluator 适配：相同 prompt、候选动作能力、公共反馈和预算；模型只提案，执行器校验修改范围并独立判定。不能修改 frozen gen5 来解释旧 F1。
2. 固定 Legacy 的公共 context 映射、暴露审查及只读获取，TEHM 使用已有合法 TRAIN snapshot；不把完整数据库或 evaluator-private 答案挂给模型。
3. 用已登记开发任务做三策略隔离与预算执行演练，实测越权访问、耗尽预算、畸形回复、超时和 UNKNOWN；演练不冒充真实调用或未见 final。
4. 冻结 provider/model、每臂调用/input-output token 上限、任务与重试规则，取得对应明确授权后才启动真实模型。当前还不是只差授权的 READY 状态。

这是 R5-7 的实现前置，不是其完成；R5-8 的正式抽样与样本依据仍开放。R5 的目标未完成，既有 G2 DEV 反例和 F1 四视图 0/1 不因本次兼容性审查改变。
