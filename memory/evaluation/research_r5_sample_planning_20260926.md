# R5-8 样本量规划补充：独立单位、精度情景与准入边界

本补充承接既有 paper protocol §4、F1 和 C7。它不修改 frozen gen5、Memory、F1、任何历史最终数据或方法结果，不启动新模型/EDA/数据获取，也不将已观察样本重新冻结为未见数据。数据验证采用标准范围核验：定义、单位、假设、独立重算和结论边界；不是重新执行整套 RTL 证据审计。

## 1. 需要修正的规划缺口

原协议正确区分了工作量上限与统计样本量，但没有可复算的数值规划。本补充提供**条件性的保守精度情景**，不从一个 pilot 或 F1 的零改善估计总体方差，不把 8 个候选写成 8 个独立来源，也不补造功效。

当前状态由封存输入重新核对：F1 仍只有 1 个预登记 FINAL task、4 个策略视图，不是 4 个独立任务；其声明来源组不授予统计独立性。C7 是 1 个已观察 DEV task、3 个真实调用，不进入 FINAL 分母。B1 的两个 wrapper 复用了 TRAIN/DEV 机制叶，不能以 top 名或 repository commit 的变化增加独立来源数。本次不声称整个 corpus 已穷尽。

## 2. 主估计量及抽样单位

沿用原协议：每个来源组先计算 M+ 与各对照的组内 task 修复率差，再对来源组等权平均。每个组的配对差值位于 `[-1,1]`；三个预先固定对照为 M−、Mremove、transform-only。组内全部预纳入 task 保留，UNKNOWN 不成功、也不改写为功能 FAIL。micro repair rate 仍另报，不能用等权 group average 冒充 micro rate。

同一个机制的 fork、复制/共享叶节点或 generator 衍生必须在谱系审核中合并或记录依赖；不同 owner、不同字节或来源组标签不是独立性证明。子测试、参数、seed、view、恢复次数不增加独立来源数。

定向可行性样本只支持已观察范围的描述。如果未来主张总体效果，须在新 FINAL 观察前固定目标来源总体、覆盖范围、抽样方式、谱系聚类、每组 task 纳入规则、比较族和停止规则。不能用下面的公式补救便利抽样偏差，也不能对同一冻结代次继续挑选成功样本。

## 3. 不依赖 pilot 方差的保守精度情景

对于独立、相同已知界长 `R` 的来源组差值，Hoeffding 单尾界应用于正负两侧，并对预定 `K` 个比较作 union bound，可得到充分规划条件：

```text
P(any contrast absolute estimation error >= h) <= 2*K*exp(-2*G*h*h/R^2)
G_sufficient = ceil(R^2 * ln(2*K/alpha) / (2*h*h))
```

这里 `G` 是独立来源组数，`R=2`，`alpha=0.05` 是情景参数；各比较彼此不必独立，但来源组之间必须满足独立抽样条件。依据 [Hoeffding 1963 原文](https://www.cs.rpi.edu/academics/courses/spring06/random/hoefding.pdf) 的有界独立和尾界推导。该应用公式及下表是本项目的计算，不是论文直接给出的 TEHM 样本建议。

| 假设精度半宽 h | 一个预定比较：充分 G | 三比较同时覆盖：充分 G |
|---:|---:|---:|
| 20 个百分点 | 185 | 240 |
| 15 个百分点 | 328 | 426 |
| 10 个百分点 | 738 | 958 |
| 5 个百分点 | 2952 | 3830 |

**这些数值是最坏情形下的充分条件，不是最小必要样本量、功效分析、经用户选定的精度要求或执行预算。** 更强且可辩护的模型假设可能显著降低所需规模。不能从该表断言“少于 958 组就不能发论文”，也不能据此自动获取或运行数百个仓库。

即使假设拥有 8 个独立且适当抽样的组，三比较的上述半径仍约为 1.094，接近/超过整个差值坐标的单侧尺度；当估计差值为零时，截断到 `[-1,1]` 后没有排除任何可能值。**这只是该保守界很宽，不证明所有统计方法在 8 组时都无效。** 当前候选框架甚至未建立独立随机抽样条件，因此本补充不对现有结果输出该置信区间。

## 4. 可执行计算与防误用

`tehm.evaluation.research_r5_sample_planning` 输出四种精度、单比较/三比较的情景表；保留 `selected_target_precision=null`、`power_claim=null`、`current_empirical_interval=null`、`final_test_ready=false` 和零执行授权。`empirical_interval` 在任一独立抽样/目标总体/前瞻冻结前提未验证时拒绝计算；这些布尔前提仍须有真实证据，不是调用函数就获得认证。

6 组检查覆盖独立 Decimal 60 位精度重算、整数充分界的前后一项、范围与多重比较、异常输入、三项推断前提拒绝、区间界限以及规划不授予最终准入。并不把这些软件检查记作统计样本。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_sample_planning_checks
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=memory python3 -m tehm.evaluation.research_r5_sample_planning
```

补充生成器 `memory/evaluation/research_r5_sample_planning_review.py` 封存控制文档、协议、F1 原 readout/receipt、B1 与 C7 审计和本计算代码。它重新核对原 receipt 的字节身份，并从 manifest/normalized rows 核对 task/view 粒度；不把这次核对宣称为重新运行 RTL 或全部历史 raw auditor。第二副本在禁网隔离中重算计划与粒度检查，必须得到相同 JSON。

## 5. 后续决策门槛，不能由本补充代替的工作

R5-8 现在有可复算的精度情景，但论文规模来源和最终数据仍未完成。下一步应固定可审计候选框架并完成来源/暴露/资格准入，而不是继续增加 DEV 调用。合法但不匹配 binder 的新任务不能因预期负结果而排除；不合格 oracle 也不能为了扩充分母而准入。

若选择确认性总体主张，必须另行明确最小有意义效果、检验/精度目标、可用来源总体和资源上限，在新 FINAL 之前冻结具体样本方案；本表不代替该选择。若最后只能得到有限定向来源，保留完整结果并如实报告范围内可行性，不升级成总体显著收益。原方案、范围和未完成要求均保留，不以本补充已通过来宣告 R5 完成。
