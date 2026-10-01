# AIC v2.1：两项进一步改进的定向验证（2026-10-01）

代码：GitHub main `e24f251`（Fmax 模式 period_relax 可连续放宽，累计 ≤1.2× 搜索结果）、
`8816f09`（未经 A/B 的 learner 自动候选不阻挡已晋升的 pin_side_rebalance 几何迁移）。

## 为何只重跑 5 个设计
- 改进 1 只在“已用过一次 period_relax 且仍需继续修复”时生效：v2 中 6 个设计用过 period_relax，
  5 个首次即清零，仅 chacha20 继续修复。
- 改进 2 只在“精确记录为无 A/B 的 learner 候选、且几何证明为单侧边缘 m3.2”时生效：v2 中
  pcie_7x、matmul（失败）与 tt_um_example、qmap（用 density_relief 通过）。
- 其余 156 个设计不经过新代码路径，结果与 v2 相同。
- 条件：A 折 v2 开跑时的知识库（按 v2_prep 重建，平台模型参数与 v2 一致）、相同 Fmax 周期。

## 结果
| 设计 | v2 | v2.1 | 归因 |
|---|---|---|---|
| chacha20 | 失败（minor） | 通过，4.195 ns（放宽链 2 步，最后一步 utilization_reduce） | 改进 1 |
| pcie_7x_v1_9_gtp_pipe_reset | 失败（m3.2×4 无策略） | 通过，1.297 ns，pin_side_rebalance | 改进 2 |
| tt_um_example (e29f3bd66378) | 通过（density_relief） | 通过（pin_side_rebalance，不改面积） | 改进 2 |
| qmap_row1024_axi_smoke_bd | 通过（density_relief） | 通过（pin_side_rebalance，162→0） | 改进 2 |
| matmul_top | 失败 | 通过，但未经任何修复 | 环境：v2 布线在 20 并发下 7200 s 超时；**按 v2 失败计** |

汇总：严格签核 139 → 141 / 161（n=149 口径 93.3% → 94.6%）；对原生 100 MHz 多 22 / 少 0
（McNemar p≈5e-7），共同通过 119 个设计频率中位 435 MHz（4.35×）。
数据：`/home/yangao/aic2026_materials/data/v21_rows.json`；203 运行目录 `r2g_aic_v2_20260930/{proj21,proj22}`。
