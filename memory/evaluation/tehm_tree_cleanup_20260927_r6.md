# TEHM 主树旧脚本清理（2026-09-27，r6）

接续 [r5](tehm_tree_cleanup_20260927_r5.md)，从当前主工作树退役
`memory/tehm/evaluation/research_r5_zipcpu_augmented_checks.py`。它只检查历史
ZipCPU DEV augmented scoreboard 的 11 个适配器分支；仓库非 Markdown 代码没有
对该入口的引用。其被测的 `research_r5_zipcpu_augmented_probe.py` 仍由当前
TRAIN rollback 兼容链导入，故保留。

现行 v8/v9 检查、I²C v1 检查和 v1 binder 仍保留：前者承担当前回归，后者是
已冻结 I²C v2 设计中的委托依赖。旧编号的核心 binder/action/rollback 模块
同样不能按编号直接删除。该检查入口可从 Git 历史恢复；历史 Markdown 中的
旧命令需要相应历史软件工作树。

本次不删除 corpus、原始日志、冻结证据或 Memory，不改变样本角色和结果。
