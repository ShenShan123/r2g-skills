# 新实验数据入口（尚无结果）

这些 CSV 只有列名，不包含虚构样本。先冻结协议再填数据；每行保留 protocol、method 和 design/family 身份。

- `e5_pipeline.csv`：每个 method × design 一行，填各阶段终态与成本。状态使用 pass/fail/inconclusive/not_applicable，不能用空白掩盖失败。
- `e6_conversion.csv`：每个 converter seed × design 一行，区分公共语义与完整包装契约。
- `e7_prediction.csv`：每个 split × seed × method × stage × task × design 一行，保存逐设计指标，便于正确计算 macro 指标与设计级不确定性。
- `e8_repair.csv`：每个 method × design 一行，coverage、success 和尝试数分开；A 阶段成本另建学习总账。
- `e9_mutations.csv`：每个 design × mutation 一行，包含 benign/control 状态与不可评估结果。

原始日志和逐节点标签不得仅用汇总 CSV 替代。这些表格尚未与 LaTeX 自动连接；填写后需要按 README 的步骤审核并搬入表格。现有结果重算脚本只处理 E1/E3/E4，不会把未完成的新实验当作已有结果。

新增 `e11_admission.csv` 记录证据故障与真实入库判定；`e12_continual.csv` 记录按时间窗口冻结的策略和逐设计产出。它们仍是空白协议模板，未执行实验。
