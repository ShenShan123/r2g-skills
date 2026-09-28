# AIC 竞赛 Fmax Cohort 预注册（2026-09-28）

状态：**运行前冻结**。本文件在任何 cohort flow 启动前提交；结果不得反向修改本文件，
偏离之处只能在结果报告中追加说明。

## 目的

为 2026AIC·AI+集成电路主题赛的“应用成效”提供证据：R2G 在实验一中自主获取的全部
合格 RTL 上，能否在 **R2G 自主寻找的 Fmax** 下完成严格签核，以及相对不修复的默认
ORFS 的增益。本 cohort 是竞赛展示用的应用评测，**不是**论文实验二（实验二固定
10 ns，另行进行；本 cohort 的 run 只写入 203 服务器上的独立知识库副本，实验二完成前
不合并回 208 的知识库）。

## 设计集合（全量，无抽样）

- 来源：实验一最终存档 `r2g_experiment1_final_archive_20260903` 中 `r2g-expander-cold`
  （R2G Pipeline）获取的 200 个合格设计。
- 其中 161 个有时钟端口，对应实验二基线计划中的 161 个任务（列表：
  `2026-09-28-aic-fmax-cohort-task-ids.json`，sha256 `4b82327a…b2710`），**全部**进入
  cohort，不做筛选、不区分知识库是否见过。
- 39 个无时钟端口（组合逻辑）无法做 Fmax 搜索，不进入本 cohort，结果中单独列出。
- 输入：沿用实验二基线项目的 RTL、config.mk、metadata.json；SDC 仅做等价规范化
  （字面 `-period 10` → `set clk_period 10` + `-period $clk_period`，值不变）。SDC 未设
  IO delay，Fmax 为寄存器间路径口径。

## 两个臂

| 臂 | 做法 |
|---|---|
| **R2G（Fmax）** | `engineer_loop fmax-drain --no-place-fast` 搜索并写入 Fmax 周期 → `engineer_loop run --no-learn`：完整 flow + 签核 + 自动修复（冻结知识库，不学习、不跑 A/B） |
| **默认 ORFS（对照）** | 与 R2G 臂**相同的已写入周期**、相同 RTL/config；完整 ORFS flow → `fix_signoff.sh --check both --max-iters 0`（只测量、不修复，不放宽周期） |

两臂都在每次 flow 后 ingest，均生成 `reports/signoff_manifest.json`。

## 成功定义（逐设计，二值）

`reports/signoff_manifest.json` 中 `strict_clean == true`，即：DRC full deck clean、Netgen
LVS clean、route 残留 0、RCX complete、最终 timing tier clean，且签核周期经
`constraint.period_source ∈ {search_winner, relaxed_chain}` 绑定到 Fmax 搜索结果
（放宽必须有完整记录链，见 failure-patterns P0-2b）。flow 崩溃、超时、缺报告均计为失败。

## 报告的指标

1. 两臂的 strict-clean 数与比例（分母 161），配对差异及 McNemar 检验、bootstrap 95% CI。
2. R2G 臂的 Fmax：`confirmed_period`（签核周期）分布；`relax_ratio` 分布及发生放宽的
   设计数；与 10 ns（100 MHz）的比较。
3. 按实验二 10 ns 基线结果分层（签核通过 / setup 失败 / DRC·route 失败 / 后端中断等）的成功率。
4. 修复归因：R2G 成功但对照失败的设计，逐一列出起作用的 recipe / 周期放宽。
5. 代价：Fmax 探针数、flow 次数、修复迭代数、wall time。
6. 全部失败设计及失败原因，不删除任何设计。

## 冻结状态（203 服务器，`/home/yangao/r2g_fmax_pilot_20260927/r2g-skills`）

| 项 | 值 |
|---|---|
| git HEAD | `849a6997c02adfd5ed808e92901bcd995f549c23` |
| 工作区 diff sha256（含未提交改动） | `44e0127c94cc36fca1032fd0cc1ff9b2f53adc62ac427c6e9a47e526d7e61b95` |
| knowledge.sqlite sha256 | `c214eb622cef2875ade6d16e87b28e8a5c261d463fbe07c45047694208d6a822`（4055 runs，32 promoted） |
| heuristics.json sha256 | `003935e708a605c066b1432a322f43ff929ef9b9691b30e526292b7ea2b84bbd` |
| `_env.sh` md5 | `27e7e5514fde5b7b73548c243aef4edd` |
| ORFS / OpenROAD / Yosys | `a5ff7ef7d` / `26Q3-318-g6b9d7fb806` / `0.64` |
| 平台 | Sky130HD，每个 flow `NUM_CORES=8` |

运行期间不修改代码、知识库或工具链；如必须修改（例如环境故障），受影响设计两臂一起重跑，
并在结果报告中说明。

## 预先声明的局限

- 设计全部来自 R2G 自己的获取结果，部分设计可能已出现在知识库历史 run 中；本 cohort
  展示的是应用效果，不作泛化能力结论。
- Fmax 由 placement 代理搜索得到，劣化模型为 sky130 默认参数（未校准），可能偏保守。
- 无 IO delay 约束。
