# TEHM 文件树清理续记（2026-09-24）

本次继续按用户要求清理迭代入口，不重写冻结证据或历史 verdict。

- 用户已自行删除仓库外的整个 `/data1/zhangdy/tehm-campaigns/`。此前清理记录中“保留 r4-real-pilot 原始产物”和“verify-s2-pilot 重放通过”仅是删除前的历史状态；当前已不能据此复核 S0/S1/S2 原始 flow 与事件链。该目录不是本仓库 Git 跟踪内容，不能从本仓库恢复。
- 删除 `memory/scripts/` 中除 `research_pilot.py` 外的 143 个 Git 跟踪的历史实验入口。它们与当前 Revision4 统一入口没有 Python 导入依赖；不再承诺 README、旧 manifest 和设计文档中提到的历史命令可执行。
- 一并删除 `memory/tehm/evaluation/orfs_interference_attribution_replay.py`：该旧回放模块直接导入其中两个已删除的脚本，且当前统一入口和其他运行时代码不引用它。避免留下一处已知的悬空导入。
- 保留 `memory/tehm/` 其他通用运行时/安全检查、`memory/evaluation/` 冻结文本、`memory/fixtures/` 和 `memory/docs/`；这些不是本次“仅保留当前脚本”的删除目标。旧文档及结果引用继续作为历史记录，而非当前可重放性声明。

删去的 Git 跟踪文件可从本次删除前的提交恢复；外部 `tehm-campaigns/` 的删除由用户执行，不在此提交中。

## 2026-09-26：退役 R5 一次性入口

从主工作树移除以下 4 个历史入口，共 768 行：

- `memory/tehm/evaluation/research_r5_asset_authority_preflight.py`：修复前的 DEV gate 诊断；当前 authority gate 及对抗检查保留。
- `memory/tehm/evaluation/research_r5_train_asset_preflight.py`：第一版 TRAIN 验证预检；后续版本及真实 authority 消费链保留。
- `memory/tehm/evaluation/research_r5_broadcast_dev_probe.py`：已完成的单案例 DEV 故障探针，不是当前 TRAIN/迁移入口。
- `memory/tehm/evaluation/research_r5_s2_preflight.py`：旧 ORFS S2 接口兼容性检查；当前 RTL controller/provider 及其检查保留。

删除前工作树干净。仓库 Python、shell、JSON/YAML/TOML 中没有对这些完整模块名的外部调用；
另核查外部 pilot 的引用，历史文件清单及已完成封装脚本不作为现行入口。
外部冻结代码、原始结果、失败记录和 Memory 数据库均未修改。
旧文档中的命令是历史记录，不再承诺能在当前主工作树执行；旧实验使用对应冻结软件。

恢复：从删除前提交 `3f165c1` 提取以上精确路径，例如
`git show 3f165c1:memory/tehm/evaluation/research_r5_asset_authority_preflight.py`。
主线保留 `research_pilot.py`、`research_r5_train_m0_v5`、v8 TRAIN 审计、当前 Agent
入口和回归检查。较早编号的 binder、rollback、oracle 和 authority 模块仍有传递依赖，不能仅按编号删除。

删除后验证：v8 19 项、v7 Action 18 项、v7 Asset 29 项、controller/provider/native
25 项，共 91 项离线检查通过；368 个剩余 memory Python 文件语法通过。
统一入口及最新 TRAIN builder 的 `--help` 均可启动，自动化文件无退役模块残余引用，
`git diff --check` 通过。这是限定回归，不是全部历史实验重跑；本次未调用模型、未 push。
