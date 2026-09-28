# AIC 竞赛：原生 ORFS 手动扫频对照 预注册（2026-09-28）

状态：**运行前冻结**（在任何扫频 flow 启动前提交）。补充
`2026-09-28-aic-fmax-cohort-preregistration-zh.md`（commit `1e5438b`）。

## 目的

原生 OpenROAD-flow-scripts 没有 Fmax 搜索。用户若想得到最快可签核频率，只能按一组
周期逐个跑完整 flow 并人工挑选。本对照测量：在相同设计上，原生手动扫频能达到的最快
可签核周期及其计算代价，与 R2G 自动 Fmax 搜索（+修复）相比如何。

## 设计样本

- 总体：Fmax cohort 的 161 个设计中，去掉 Fmax 搜索判定为 `unconstrained`（无受约束
  寄存器间路径，设计本身没有 Fmax）的 12 个，余 149 个。该排除依据设计属性，不依据
  R2G 签核结果。
- 用 `random.seed(20260928)` 从 149 个中无放回抽 30 个（清单：
  `2026-09-28-aic-native-sweep-sample.json`，sha256 `6bef8e58…3f141`），全部报告。

## 原生扫频臂

- 周期梯度（ns）：`1.0, 1.25, 1.67, 2.0, 2.5, 3.33, 5, 10, 15, 20, 25`
  （1000–40 MHz，覆盖从 100 MHz 往上提频与时序失败时往下放宽两种习惯）。
- 每个（设计, 周期）：与 cohort 相同的 RTL/config.mk 模板，SDC 只改 `clk_period`；
  原生 ORFS 完整 flow（`run_orfs.sh`，无 PLACE_FAST/ROUTE_FAST，无钩子）→
  `fix_signoff.sh --check both --max-iters 0`（只测量，不修复，不放宽周期）。
- 单点通过 = DRC full deck clean、Netgen LVS clean、route 残留 0、RCX complete、
  timing tier clean（即 manifest 的 `strict_missing` 中除 Fmax 绑定相关的 `constraint:`
  项外为空）。
- 使用独立的知识库/journal 副本，不影响仍在运行的 cohort。

## 报告的指标

1. 原生扫频的最快通过周期 vs R2G 的 `confirmed_period`（同一设计配对）；R2G 更快/
   相同/更慢/仅一方有结果的计数。
2. 代价：原生完整扫频 = 11 次完整 flow；另报“贪心上扫”（从 10 ns 开始每次提频一档、
   首次失败即停；10 ns 失败则逐档放宽直到通过）所需完整 flow 次数；R2G = place 探针次数
   + 完整 flow 次数（含修复重跑），以及两者的 CPU 时间（wall × NUM_CORES）。
3. 原生在任一周期都无法通过、而 R2G 通过的设计（体现修复）。

## 预先声明的局限

- 离散梯度的分辨率有限：原生最快周期只精确到梯度档位，R2G 为连续值。比较“更快”时
  以 R2G 周期 ≤ 原生通过档位为准。
- 配置模板相同（来自 R2G 项目模板），不是完全裸的 ORFS。
