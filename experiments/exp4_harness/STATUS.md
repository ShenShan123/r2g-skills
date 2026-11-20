# Graph harness 实验运行状态

截至2026-11-16 00:39（UTC+8），原协议四组三轮均已结束，720/720次测试评分已落盘。最后一组于00:33:15完成，控制器正常退出；所有归档请求均已有回执。

| 组别 | 第一轮严格通过 | 第二轮严格通过 | 第三轮严格通过 | 合计 |
| --- | ---: | ---: | ---: | ---: |
| DeepSeek × 原生 | 0/60 | 0/60 | 0/60 | 0/180 |
| DeepSeek × R2G | 59/60 | 19/60 | 0/60 | 78/180 |
| GPT × 原生 | 60/60 | 60/60 | 60/60 | 180/180 |
| GPT × R2G | 60/60 | 60/60 | 60/60 | 180/180 |

严格通过要求全部规定产物通过校验，不等同于逐项标签准确率。完成评分包含对开发失败和缺产物的计分，并不表示每轮都成功交付。

DeepSeek R2G第三轮开发异常结束（`harness_error`、exit 2），没有交付`converter/run.sh`，因此60例均计未通过；该轮模型用量未完整返回，Token保留为未知。第二轮正常交付程序，通过19例，其余为实际产物未通过校验。DeepSeek原生第三轮达到200次模型请求上限，已有程序仍被统一测试。

DeepSeek R2G首轮唯一未通过设计为`C03498__reg_row__riscv-inregistercpu__nangate45__p2`：D/E图同构与异构输出各漏一条RC耦合边（5771/5772条），该设计六类标签准确率100%。

19:12:35启用的加速评分器保持原评分语义；优化和切换记录见[评分提速](SCORING_ACCELERATION.md)。模型开发、转换器和原协议结果保留。11月16日提出的生成与检验工作流修订仍为[设计稿](PROTOCOL_REVISION_20261116.md)，未启动新版本实验。

同次资源采样：203 CPU使用率18.4%，可用内存409.0 GiB，实验磁盘剩余401.4 GiB。Image四组三轮720次测试均严格通过，结果见[Image汇总](../exp4_image_harness/RESULTS.md)。

计算目录：`memlab203:/home/yangao/exp4_harness_20261115_203`。归档目录：`memlab216:/proj/workarea/yangao/exp4_harness_20261115_203_archive`。

[原协议](PROTOCOL.md) · [最新机器可读进度](evidence/progress_latest.json) · [完整结果数据](results/summary.json) · [原协议结果表](results/table.md)
