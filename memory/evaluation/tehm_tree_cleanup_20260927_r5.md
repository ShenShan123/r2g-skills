# TEHM 主树历史入口清理（2026-09-27，r5）

接续 [r4](tehm_tree_cleanup_20260927_r4.md)，仅从当前主工作树退役以下 3 个独立历史入口，共 452 行：

- `memory/tehm/evaluation/research_r5_axis_adapter_q_probe.py`：旧 AXIS native 资格探针；不是当前 I²C DEV oracle 或冻结 gen6 M0 入口。
- `memory/tehm/evaluation/research_r5_zipcpu_formal_live_control.py`：旧 ZipCPU skid formal live-control 驱动。
- `memory/tehm/evaluation/research_r5_zipcpu_formal_audit_v2.py`：仅由上述驱动导入的旧 formal 审计器。

删除前，仓库内非 Markdown 文件对这三个模块的唯一引用，是 live-control 驱动对 audit-v2 的导入；当前入口、检查及 Asset authority 不导入它们。Git 历史仍可按路径恢复；`RTL_testbench/_r5_pilot/software/frozen-gen6-f51f8fc` 中三份文件与删除前 `HEAD` 字节完全一致，SHA-256 分别为 `bfdcd0a2a40977610801f1da47fcd903112f481d1c9ee7f74f1fec76ab41f2f1`、`f8f4c751e485a293fdef840d898b9a54fafc1a0b3f6641738507bb7d0c347bf2`、`177911c8d27c6ceeece01e27b26d92c1752d31c5f0d4c9c3826ae9435a2dc761`。

历史 Markdown 中记录的旧命令是当时代次的复现说明，不能用当前主树重新解释；复现时须选择其冻结软件。保留仍在当前依赖链上的旧编号 binder/action/rollback 模块，以及当前 `research_r5_train_m0_v8.py`、`research_r5_train_mux.py`、`research_r5_s2_provider_run.py`。本次不删外部 corpus、原始日志、测试、Memory、冻结软件或其他研究分支文件，也不改变 DEV/TRAIN/target 权威归类。

限定回归：在显式传入已冻结 DEV 源的正确 CLI 下，v9 binder 9/9、v9 action 10/10、v8 binder 19/19、v8 TRAIN authority 30/30 全部通过。直接以 `unittest` 裸调用前三个模块未设置必须的 RTL 全局输入，会产生 `source_size_or_type` 假失败；不计为清理回归。未调用模型，未运行新 RTL 仿真，未推送。
