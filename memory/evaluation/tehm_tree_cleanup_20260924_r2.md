# TEHM 文件树清理续记（2026-09-24）

本次继续按用户要求清理迭代入口，不重写冻结证据或历史 verdict。

- 用户已自行删除仓库外的整个 `/data1/zhangdy/tehm-campaigns/`。此前清理记录中“保留 r4-real-pilot 原始产物”和“verify-s2-pilot 重放通过”仅是删除前的历史状态；当前已不能据此复核 S0/S1/S2 原始 flow 与事件链。该目录不是本仓库 Git 跟踪内容，不能从本仓库恢复。
- 删除 `memory/scripts/` 中除 `research_pilot.py` 外的 143 个 Git 跟踪的历史实验入口。它们与当前 Revision4 统一入口没有 Python 导入依赖；不再承诺 README、旧 manifest 和设计文档中提到的历史命令可执行。
- 一并删除 `memory/tehm/evaluation/orfs_interference_attribution_replay.py`：该旧回放模块直接导入其中两个已删除的脚本，且当前统一入口和其他运行时代码不引用它。避免留下一处已知的悬空导入。
- 保留 `memory/tehm/` 其他通用运行时/安全检查、`memory/evaluation/` 冻结文本、`memory/fixtures/` 和 `memory/docs/`；这些不是本次“仅保留当前脚本”的删除目标。旧文档及结果引用继续作为历史记录，而非当前可重放性声明。

删去的 Git 跟踪文件可从本次删除前的提交恢复；外部 `tehm-campaigns/` 的删除由用户执行，不在此提交中。
