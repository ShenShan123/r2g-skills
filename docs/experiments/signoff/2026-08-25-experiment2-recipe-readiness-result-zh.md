# Experiment 2 Recipe Readiness Result

## 结论

**NOT READY。** 当前 Sky130HD、100 MHz、fixed-footprint、strict-signoff 的
Recipe 库还不满足启动正式实验二的硬门槛。本轮没有把部分改善、旧策略覆盖、
或历史证据误写成新 Recipe promotion。

## 本轮完成的有效工作

- 冻结了工具链、知识库、动作白名单和固定任务边界；A/B 隔离、gate、route 与
  policy 回归共 `22 passed`。
- 审计了 family 去重后的自然失败 ledger。当前有 5 个合格 repair-needed 任务：
  USB、Hazard3、Skaarler SHA-256、Secworks Blake2s 和 ACE2 RoPE。
- 对重复出现的 ACE2 setup-timing 机制，筛选了三个互不等价且不放宽 10 ns 时钟、
  不改变 footprint 的候选：`ABC_CLOCK_PERIOD_IN_PS=8000`、`ABC_AREA=0`、
  `SYNTH_HIERARCHICAL=1`。三者在完成 CTS 后均不优于重复 baseline，按预注册规则
  提前停止并记录为负证据；没有写入 learner 的正向事件。
- 新的 Git 绑定 DES 自然 DRC 任务复现了 `16` 个右边界 `m3.2`，随后仅施加
  `PLACE_PINS_ARGS=-exclude right:*`。候选达到了 strict clean：DRC/route/LVS/
  antenna 均为 `0`，setup/hold WNS 分别为 `+6.263200` / `+0.404928 ns`，RCX complete。
  这是现有 `pin_side_rebalance` 的一次成功**范围筛选**，不是新 Recipe，也还不是
  lifecycle scope promotion。

## 启动门槛核对

| 硬门槛 | 当前证据 | 结果 |
|---|---|---|
| 至少 1 条真正新的 Recipe 严格 promotion | 0 条；唯一完整成功是既有 `pin_side_rebalance` 的单对覆盖筛选 | 不通过 |
| 冻结独立 cohort `N >= 10` 且 strict-clean `>= 7/10` | 合格自然任务仅 5 个，尚未形成独立 cohort | 不通过 |
| 固定五任务 fixed-footprint 回归 `>= 4/5` | 既有 `3/5` 含 area-changing recovery，按当前规则不可复用 | 不通过 |
| 无 P0 证据问题 | 本轮候选未见任务放宽、混杂修改或正证据污染 | 通过（仅本轮范围） |
| lifecycle/ranking 只自动应用正确 scope | 单元回归通过；本轮没有触发 lifecycle 变更 | 未完成端到端验证 |
| manifest、统计和关键测试一致 | 22 个目标回归通过；正式 cohort 尚不存在 | 部分通过 |

## 为什么此时停止

本轮唯一可重复的 catalog-uncovered 机制是 broad setup timing；它的三个安全、
非等价候选均已失败。`m3.2` 已被已有 pin-side 机制覆盖，DES 的成功不能通过改名
虚构成新能力。route-capacity 和面积调整属于 bounded-area track，LVS/source-closure
则不属于当前物理 Recipe 的安全动作空间。继续在同一机制上换参数或做无关 A/B，
只会制造重复试验，不会提高 readiness 的可信度。

## 下一步

1. 用 Expander 定向补充至少两个独立、Git 绑定的中等规模自然 failure family，优先
   residual DRC/detailed-route interaction、真实 antenna，以及不依赖 footprint 的
   route 机制。
2. 每个新机制先做两次固定 100 MHz baseline，再按每机制最多三个非等价候选筛选。
3. 只有候选在两个 family 上均进入完整 strict-clean，才投入两轮 `2A + 2B` 的
   ownership/provenance 完整 A/B；通过后才允许写入 lifecycle 和 learner。
4. 得到新 Recipe 后冻结知识库，另建 `N >= 10` 的 family-deduplicated validation
   cohort；在此之前不启动正式实验二。

## 可追溯证据

- Goal: `2026-08-24-experiment2-recipe-readiness-goal-zh.md`
- 固定边界审计: `2026-08-24-fixed-footprint-integrity-audit.md`
- 缺口审计: `2026-08-23-sky130hd-recipe-gap-audit.md`
- 失败台账: `/home/yangao/r2g_recipe_readiness_2026_08_24/failure_ledger.jsonl`
- DES baseline: `/home/yangao/r2g_recipe_readiness_2026_08_24/projects/des_top_fixed_baseline_current/repair_family_probe_result.json`
- DES candidate: `/home/yangao/r2g_recipe_readiness_2026_08_24/projects/des_top_pin_side_rebalance_current/repair_family_probe_result.json`
