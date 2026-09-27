# TEHM 主树旧入口清理（2026-09-27，r4）

按“主工作树只保留当前需要的脚本”的要求，精确退役五个已跟踪的独立历史入口，合计 1,207 行：

- `memory/tehm/evaluation/research_r5_train_m0_v5.py`：gen5 Memory 构建器；当前构建入口为 gen6/v8。
- `memory/evaluation/research_r5_paper_gen5_readout.py`：gen5 一次性 pilot readout。
- `memory/evaluation/research_r5_sample_planning_review.py`：一次性 sample-planning 封包/恢复演练脚本；冻结 bundle 自带该脚本副本。
- `memory/tehm/evaluation/research_r5_axis_adapter_recovery.py`：旧 axis_adapter 恢复演练审计入口。
- `memory/tehm/evaluation/research_r5_zipcpu_sby_audit_v3.py`：旧 ZipCPU DEV formal 双代次冷审入口。

删除前主工作树干净、五个文件均由 Git 跟踪；在仓库非 Markdown 文件中未检出其完整模块名引用。未删除任何原始 RTL、测试、外部 corpus、冻结结果、Memory 数据或 frozen software worktree。删除前提交为 `dd918f45b5365b2e4cb89748754eca29aea31c75`；五个入口可由 Git 历史按路径恢复，gen5 原软件另存在 `RTL_testbench/_r5_pilot/software/frozen-gen5-2ce921a`。这不将历史脚本从过去的冻结代次中追删。

保留仍有依赖的旧编号 binder、action、adapter、rollback 和 Asset authority 模块；v8/v9 当前入口、统一 `research_pilot.py` 与协议检查也保留。版本号小不等于无调用。主树不再承诺重跑上述退役的历史命令；历史证据仍须使用对应冻结软件，不得以当前软件哈希冒充旧代次。

限定回归：当前主树 v9 DEV action 10/10、v9 binder 9/9、v8 binder 19/19、v8 TRAIN authority 30/30，均通过；统一 `research_pilot.py --help` 与当前 v8 构建器 `--help` 均退出 0。gen6 M0 由当前主树冷验时按预期拒绝 `software epoch drift`；改从 frozen-gen6 软件副本独立冷验 `valid=true`，report digest 仍为 `sha256:af461d667815d501723fbf9b95781bc2d21d325aa8cadaa99085b63bc0d6840d`。这不是功能失败。未调用模型、未重跑 RTL simulator、未推送远端。
