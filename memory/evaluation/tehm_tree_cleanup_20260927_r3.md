# R5 旧代次检查入口清理（2026-09-27）

按“只保留当前需要的脚本”继续整理主工作树。本次删除 15 个已跟踪、无未提交修改的旧代次独立检查入口，共 2431 行：

- Asset binding：`research_r5_asset_binding_v4_checks.py`、`_v5_checks.py`、`_v6_checks.py`。
- Scoped TRAIN：`research_r5_rtl_scoped_checks.py`、`research_r5_rtl_scoped_v2_checks.py`。
- SKID core/action/binding：`research_r5_skid_core_checks.py`、`_v2_checks.py`、`_v3_checks.py`、`research_r5_skid_action_v6_checks.py`、`research_r5_skid_binding_v2_checks.py`、`_v3_checks.py`。
- TRAIN Asset authority：`research_r5_train_asset_authority_checks.py`、`_v4_checks.py`、`_v5_checks.py`、`_v6_checks.py`。

上述文件均位于 `memory/tehm/evaluation/`。删除前逐项扫描剩余 Python、shell、JSON、YAML、TOML，未发现完整模块名引用。它们不是 v8 当前入口，也不在保留的 gen5 构建入口的导入链上。历史文档仍可能引用旧命令；复现旧代次应使用 Git 历史或当时冻结的软件副本，不把当前树解释为完整历史重放环境。

保留仍有传递依赖的旧编号 binder、action、adapter、rollback、authority 实现，以及 gen5 构建入口、v7/v8 和核心运行时回归。没有删除冻结原始证据、失败记录、外部 corpus、Memory 数据库或 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot`。删除内容可从清理前提交 `a43974d` 按精确路径恢复；Git 未执行强制重置。

清理同时发现未提交的 v8 canonical TRAIN 适配器把 AXIS preservation 测试 ID 错写为两个不存在的 `run_pause_*`。已按现行九项 cocotb 清单改为 `run_test_tuser_assert_001` 和 `run_stress_test_004`，并在生成见证时对照 AXIS 的真实 `TARGET_IDS`、`PRESERVATION_IDS`、`IDS` 检查。该适配器仍属在建代码，不构成新的 Knowledge 或 Memory 准入结论。

本次不调用模型，不推送 GitHub，不改写冻结实验结果。最新回归结果以本次工作树实际运行输出为准。
