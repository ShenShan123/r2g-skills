# R5 S2 C1：共享 RTL 提案、反馈过滤与预算状态机组件

接续 [S2 入口审查](research_r5_s2_compatibility_20260926.md)，本轮实现三策略共用的 RTL 提案边界，而非复用 ORFS CONFIG_DELTA。**C1 是离线开发组件，不是已接通的 Agent runner，不产生修复/迁移结果。** provider、真实 RTL evaluator、Legacy/TEHM 获取与完整三策略进程隔离尚未接入；`agent_ready=false`。

## 实现与信任边界

代码 `memory/tehm/evaluation/research_r5_s2_controller.py` 提供：

- `candidate_from_proposal`：只接收固定四字段 JSON（schema、base_digest、action、edits），动作是替换已声明 RTL 文件或 no_action。三策略共享完全相同能力，不削弱无记忆臂。重复 JSON key、模型自报 verdict、额外 metadata、旧 source digest、外部/隐藏/非 RTL 路径、重复编辑和无效内容均拒绝。最大 64 文件、闭包 256 KiB、响应 1 MiB；不截断后执行。
- `write_candidate`：在可信 runner-owned parent 下独占创建新目录，复制完整候选闭包，不覆盖源文件或已有候选；链接 parent 和文件/目录前缀冲突拒绝。父目录需由可信 runner 独占，不声称能防御同权限并发写者。HDL 内容仍可能含危险行为，必须由后续 evaluator 的物理隔离与原始 oracle 验证处理；路径校验不是 HDL 沙箱。
- `public_feedback`：只从可信 evaluator 结果投影 target/preservation/native 三个 verdict enum，不输出 gold、预期值、断言、文件路径或 raw trace。必需字段缺失、额外 obligation 或无效值整体 UNKNOWN；合法记录按 FAIL 优先、其次 UNKNOWN、否则 PASS。PASS/UNKNOWN 停止；已知 FAIL 可在预算内继续。此函数不认证输入来源，也不代替 evaluator 对 rc、日志和实际编译源的核验。
- `BudgetLedger`：独占新建单写者 JSONL，事件带 sequence/前序摘要/自身摘要并 flush+fsync。每次提案派发前预留输入计数加输出上限；无退款。pending 时不得重入，INVALID 消耗预算但不能调用 evaluator，TIMEOUT/ERROR 停止。有效提案必须绑定 candidate digest，测试必须先预留并匹配该 digest；不能重复测试同一次提案或在测试 pending 时再取提案。测试 PASS/UNKNOWN 后不再调用。

预算是**预留量而非实际 token 使用量**。输入 token 计数尚须由后续可信 tokenizer/broker 核验；组件不授予 provider 权限，也未实现跨进程总预算、wall-clock supervisor、provider usage 对账或 crash-resume。现有 ledger 不能重开覆盖；新路径不自动获得新的任务预算。上述剩余约束由未来 campaign/broker 层落实，不能把当前 Python 对象当完整资源隔离证明。

通用 RTL 文件提案与 TEHM asset 的 v7 source-only binding 是不同入口。C1 没有替换 v7 binder，也不允许把模型返回的编辑位置当作资产 binding witness。后续资产执行仍须经过真实 route/select 与重新绑定；这次新增软件不解释为 gen5 的 ΔMemory。

## 开发检查与实际恢复

`research_r5_s2_controller_checks.py` 的 16 组 unittest 通过，包含 27 种合法 verdict 组合、三策略同能力、no_action 非成功、重复/过期/越界提案、多文件内容校验、目录拒绝覆盖、反馈私有字段过滤、输入/总量/次数上限、pending/终态顺序、零退款、超时停止、candidate digest 绑定、测试上限、持久事件链和禁止重开。实际 token/model/oracle 均为 0；这些是 synthetic checks，不计作 RTL task 或经验收益。

开发中在首次冻结前补了“有效提案→匹配测试”状态约束，避免 INVALID 后也能预留测试；补多文件前缀检查时一次局部编辑暂时打断了内容校验循环，审阅后整体修正，并新增多文件/非法 UTF-8/合计大小反例。所有实际执行的测试均通过；不编造未运行版本的实验结果。一次编辑命令自动审批超时，明确重试一次后成功，与 RTL/model 运行无关。

工作根 `_r5_pilot/agent/s2-controller-c1/`；第二份 `/tmp/tehm-r5-s2-controller-1wo_jmmz/`。封存 core、checks、运行入口、封存脚本及第一次隔离测试的 launch/process/result。第一次和第二副本恢复都用 bwrap 隐藏原仓库、corpus 与网络，仅挂固定代码和系统 Python，重新执行全部 16 组检查。两个规范化 result JSON 逐字节相同；实际 launch/process 分别保留，不伪造相同执行身份。主代理另核对 tar 成员内容、两副本全文件清单和恢复输出。此恢复是完整组件检查，不是 RTL 仿真恢复或 Agent sandbox 的验收。

| 工件 | SHA256 |
|---|---|
| controller component | `04a85773fde6878dd32cfabf3f0c8c5fe6edcc8c89c2248d1693d488cdc22ea1` |
| component checks | `23a1b5bfaa3df0faee3516017902c2aa85605a3e38f214067673dd2311230d38` |
| normalized original/recovered result | `68bd31cb91ff7e758845db89b2454abb27988a429e2a50126fc5657ae0c5da57` |
| evidence archive | `004b8f42c897022f24daaee5cb38dec71274332cbfee9d222a2d7585cd249ffb` |
| seal | `420ac4ee9f2a32dc4b3c328c6115fc3efbfb2fd49796bd54f581071b214497b7` |

同机 `/tmp` 副本不代表异地或长期托管。现行 67 项 paper bookkeeping 检查也通过，不是全仓回归。没有 provider、EDA、Memory 更新、production、upstream 编辑或 push；冻结 gen5 和既有结果仍保持原身份。

## 下一步

先把上述组件接到已登记 DEV 任务的实际 private RTL evaluator，验证候选编译身份、端口 verdict、真实超时与隔离拒绝，再接三策略公共 prompt/Memory context。Legacy 保留冻结原算法，TEHM 保留合法 TRAIN provenance，不让 raw DB 或目标答案进入模型上下文。真实模型只在接口演练通过且 provider/model/预算明确授权后运行。

已向用户询问首批开发校准的模型选择与建议上限：1 个已登记 DEV 任务、三策略各最多两次，总调用 ≤6，每次 input≤8000/output≤2000、总预留≤60000 tokens、零重试。**这是待确认建议，不是已批准预算或冻结实验**，不得写回 gen5 的零调用计划。S2 及论文规模 R5-8 尚未完成，F1 的四视图 0/1 不变。
