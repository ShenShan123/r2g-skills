# 实验四：物理设计转图能力对比协议 v1

## 1. 研究问题

在完全相同的、已经 strict signoff 的物理设计输入上，比较：

1. 冻结的 R2G `def-graph` v3 流水线；
2. GPT-5.6 Sol 生成并冻结的通用转换脚本；
3. Claude Opus 5 生成并冻结的通用转换脚本；
4. Qwen3.7 Max 生成并冻结的通用转换脚本。

实验考察的是从 post-synthesis netlist、四阶段 DEF、Liberty/LEF、SDC、
SPEF 和 STA 报告生成四阶段 PyG 图的正确性，不重新运行 PnR，也不评价修复能力。

## 2. 与实验三的关系

主实验四不读取实验三的 Recipe、proposal bank、修复结果或模型反馈。输入只来自
实验一之后完成的默认 baseline 中，已经独立证明 strict clean 的物理设计。因此实验三
和实验四可以并行，且实验三的成功筛选不会污染实验四。

实验三结束后可以增加一个不计主分的 paired extension：比较同一 RTL 修复前后图的
拓扑/特征变化是否符合物理改动。该扩展必须单独报告，不与主实验四合并。

## 3. 数据冻结

候选池只使用转图前可知属性：strict-clean 状态、源码仓库组、mapped cell 数以及必需
物理文件是否存在。禁止根据任何图输出、转换成功率或模型表现选择样本。

限定 `100..10000` mapped cells，并分为：

- small：`100..499`；
- medium：`500..1999`；
- large：`2000..10000`。

固定划分为：

- canary：1 个 medium，不计分；
- development：每个规模 2 个，共 6 个；
- hidden test：每个规模 8 个，共 24 个。

同一源码仓库组在三个集合中最多出现一次。划分由固定 seed 对 task ID 哈希排序产生，
不人工挑选“好看”结果。

## 4. LLM 脚本开发

三个模型各自独立开发一个通用 Python 转换器。每个模型最多五轮、五次 API 调用，
总计仍为 200,000 Token。第 0 轮读取公开数据契约和开发集输入摘要；每轮脚本均在全部
6 个开发样本上执行。后续轮次只返回开发集的有界结构化诊断，包括失败检查、归一化
异常签名、相对 `output_dir` 的缺失文件、结构错误、运行时间和输出大小；不返回原始
日志、真实 task ID、输入路径或任何 hidden-test 信息。

若某轮达到开发集 `6/6` strict pass，则提前停止。否则五轮结束后，按预先固定的
开发集排序规则选择最佳有效轮次（strict pass、契约检查、验证/统计状态、结构错误，
最后以较早轮次打破平局）并冻结，记录 SHA256。隐藏集执行阶段不再调用 LLM，也不
允许逐题修改脚本。

## 5. 公平执行边界

- Referee 预先把 ODB 导出为四阶段 DEF，并生成固定 STA 报告；这部分时间不计入任何方法。
- Referee 显式使用冻结 ORFS 工具链中的 OpenROAD `26Q3-318-g6b9d7fb806`，不得从登录 shell 的 PATH 自动选择版本。
- 每种方法读取同一份经 SHA256 证明的输入 config 和文件。
- 转换器不得联网、启动子进程、调用 R2G 脚本或读取 R2G 源码。
- 所有方法使用相同 Python/PyTorch/PyG 环境、单任务超时和并行度。
- R2G 的脚本和三个 LLM 脚本均在 hidden test 前冻结。
- 断点恢复只跳过已有完整 attestation 的 task；失败 task 从空输出目录重新执行。

## 6. 输出契约

主产品为 `r2g2_four_stage_hetero_pipeline_v3`：floorplan、placement、cts、route 四张
异构图，共享 post-synthesis 拓扑和 post-route 标签，阶段间只允许使用对应 cutoff 前
可知的物理特征。每个方法还必须生成阶段快照、alignment metadata、validity masks 和
provenance。

## 7. 评分

主指标：24 个 hidden-test case 中严格通过全部验证的数量。

严格通过同时要求：

1. `validate_four_stage.py` 返回 PASS；
2. 验证报告中的全部适用数据契约检查均满足；未配置的可选输出单独列出，不计作失败；
3. `summarize_four_stage_graph_data.py` 返回 PASS；
4. `structural_issues=0`；
5. 未超时、未读取禁止输入、未调用网络或 R2G 实现。

辅助指标：平均适用契约通过率、按 small/medium/large 的成功率、转换 wall time、峰值 RSS、
输出体积、开发 Token 和 API 调用数。任何环境故障单独重跑，不当作方法失败；确定性
程序错误保持原结果。

## 8. 报告方式

分别报告三种 LLM，不只报告平均值。开发集结果用于说明学习曲线，不进入主分。canary
只验证链路。实验三 paired extension 若执行，放在独立附录。
