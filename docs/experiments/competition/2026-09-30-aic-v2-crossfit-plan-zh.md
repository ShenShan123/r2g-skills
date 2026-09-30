# AIC v2：冻结后改进的两折交叉验证（2026-09-30）

状态：**运行前提交**。这是冻结 cohort（`1e5438b`）之后的改进评测，结果单独报告，不改写
预注册 cohort 的结论。

## 改进内容（相对冻结版本）

1. `engineer_loop fmax-retry`：Fmax 搜索被后端中断阻断、之后被修复签核的设计，重新搜索并重跑。
2. Fmax 模式项目在 timing minor 档也提供 `period_relax`（固定周期任务不变），放宽记录为放宽链。
3. 平台级汇总劣化模型（仅周期 ≤ 10 ns 的 run），家族样本不足时使用。

## 设计与交叉拟合

- 设计：cohort 全部 161 个（R2G 实验一获取的有时钟设计），从开跑前的原始项目重建（SDC 10 ns，
  模板配置），不继承 cohort 的任何修复。
- `random.seed(20260930)` 分成 A（81）/ B（80）两折（`2026-09-30-aic-v2-folds.json`）。
  评测 A 折的代码副本，其知识库 = 冻结知识库删除 A 折设计的全部 cohort 记录（R2G 臂 + 对照臂），
  再重新 learn；B 折对称。每个设计使用的劣化模型和 recipe 统计都不含它自己的数据。
- 流程：`fmax-drain --no-place-fast` → `run --no-learn` → `fmax-retry` → `run --no-learn`。
- 成功定义与指标同 cohort 预注册（manifest `strict_clean`；period_source 可为 relaxed_chain）；
  另报：与冻结 v1 R2G 的逐设计对比（通过数、签核周期）、与原生 100 MHz 及原生扫频样本的对比、
  放宽链触发次数、fmax-retry 次数。

## 冻结状态（203）

```
A knowledge=33768739a11ea9bc heuristics=9abed3c591fb9064
B knowledge=253b586566dd8184 heuristics=f3ff368c1aacc542
folds=3a98d04e14a083f0
code_diff=e0382d537c278f46 head=eefd10e
```
