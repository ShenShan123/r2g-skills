# R5 v9 APEX 绑定 DEV 结果（2026-09-27）

在已封存 APEX augmented DEV oracle 上，新增纯 source-only 的 `research_r5_skid_binding_v9.py`（SHA256 `b0645acd4b4a071290bd26ef659f06aaada05e23ea4a70afb265b1e1154e8c72`）及 9 项检查（SHA256 `841f390fb12f124ff13fff62673a68f6eca1708217d19ce8fd769020e3541cd8`）。受限完整 token grammar 在唯一错源 drain 上 `BOUND`，健康源 `NO_MATCH`；C1、额外 writer、错误 context、歧义、词法陷阱、篡改或陈旧 witness 均拒绝。测试还禁止绑定函数内文件／网络 I/O。此 grammar 不等于通用 RTL 支持；alpha-renaming 不算跨设计迁移。

实际动作只输入 APEX buggy DEV 源、Asset 模板与公开 context，输出一次 RHS 修改和 `functional_verdict=NOT_EVALUATED`。候选在全新构建根编译并重跑 augmented target／preservation，均 PASS；完整 action、日志、9/9 文件哈希与 witness 关联见 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/v9-apex-r1/candidate/receipt.json`，SHA256 `e992a8e91233467ca7a8b98317090ce7a416bfc9dceee02ea1e4248737031832`。候选与 DEV clean 源哈希相同仅是事后 evaluator 核对，不是运行时答案输入。

结论限于 `DEV_ONLY`：未验证隔离的目标 agent 工作区、未见外部来源、Memory 三视图或论文修复率。gen6 软件仍由 `frozen-gen6-f51f8fc` worktree 独立冷验；v9 需另行冻结、取得 TRAIN 准入并预登记新目标。无模型调用和远端发布。
