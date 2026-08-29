# Parametric Shadow RFC（v0.1，read-only）

本 RFC 只定义 Parametric 的 shadow-only 观察边界，不实现第五个物化 view。
`tehm.views.parametric_stub.PARAMETRIC_VIEW_STATUS` 继续为
`NOT_IMPLEMENTED`。

## 进入条件

shadow proposal 必须同时绑定以下三类不可变输入：

1. `parametric_readiness.json` 的 aggregate status 为
   `READY_FOR_IMPLEMENTATION`，且 distance、coverage、uncertainty、lineage
   diversity 和 `all_retrieval_policies_ready` 全部为 true；
2. 对当前 `platform|family|dataset_tier` scope 的 calibration policy 为
   `status=ready`，其 held-out firewall 与训练 lineage disjoint；
3. freeze verifier 返回 `ok=true` 且 `roundtrip_byte_stable=true`。

任一条件不满足，proposal 必须 `ABSTAINED`，并保留可枚举的 abstain reason。
硬 OOD ceiling 仍为 `3.0`；校准只能收紧，不能放宽。

## 运行边界

```text
frozen readiness + policy + replay receipt + graph context
  → tehm.parametric.shadow.build_shadow_proposal (read-only)
  → shadow proposal / abstain receipt
```

该入口只调用 `PhysicalEffectMemory.predict` 的读路径，不调用
`record`、canonical capture、crystallization、`tehm_rule_status`、activation
或 promotion authority。返回记录必须包含：

- `parametric_view_status=NOT_IMPLEMENTED` 与 `parametric_shadow_status=SHADOW_ONLY`；
- policy/readiness/replay digest、policy scope、bundle/manifest digest、query
  graph digest；
- nearest distance、uncertainty intervals、support、held-out lineage IDs；
- 若候选 action 已提供，action digest 与 action-conditioned audit（domain、
  transformation family、config-edit keys、normalized config-edit values）也必须绑定；缺少 transition action
  provenance 的 physical row 不得伪装成兼容 support；
- `canonical_memory_mutation=none`、`promotion_eligible=false`；
- deterministic `abstain_reasons`。

因此它可以被外部实验 harness 写入独立 shadow log，但不会改变 canonical
TEHM，也不会进入 runtime production retrieval。现有 production 路径仍保持
`build_query → retrieve → propose_activation`，且只允许 promoted rule。

### Selector-before-execution preflight result

The execution-ordered preflight lane first ran six frozen 50%-utilization
baselines, then applied the read-only context-conditioned selector.  It produced
`PROPOSED=0`, `ABSTAINED=6`, so no 40%-utilization after arm was materialized.
Proposal coverage `0.0` was below the frozen `0.5` gate; the
`TIMING_RELIEF_BUDGETED_V1` 50→40 mechanism is therefore closed, not widened.
The evidence is in
`evidence/tehm-authority-v1/v4/prospective-selector-preflight-v1/`; any next
experiment must register a new action signature and a new source-disjoint
prospective manifest.

### Independent V2 action signature (50→45)

The next independent signature,
`TIMING_RELIEF_BUDGETED_V2_50_TO_45`, was calibrated from four source-isolated
support effects and three external calibration observations. The support
contract pass rate was `1/4` with raw Pareto harmful `4/4`; the calibration
contract pass rate was `0/3` with raw Pareto harmful `3/3`. After treating
serialized interval endpoints as closed with a scale-aware numerical tolerance,
the read-only split-conformal policy reached empirical and per-metric coverage
`1.0`, but retained broad uncertainty intervals.

The prospective V2 cohort then ran all six 50% baselines before selection. The
action-bound selector produced `PROPOSED=0`, `ABSTAINED=6`, and materialized no
45% after project, so the frozen proposal-coverage gate stopped with
`STOP_50_TO_45_LOW_PROPOSAL_COVERAGE`. Three baselines failed strict
signoff/graph completeness; the three complete baselines abstained because WNS
or power intervals did not fit the typed contract. No selected after ORFS,
cross-lineage TE, or promotion evidence is claimed, and the canonical snapshot
remained unchanged. Compact evidence is under
`evidence/tehm-authority-v1/v4/next-action-v2-support-50to45/`,
`next-action-v2-calibration-50to45/`, and
`prospective-selector-preflight-v2/`.

The operational implication is a real stop, not a request to widen the
intervals: either register a narrower, semantically justified action signature
or add disjoint support/calibration until the selector has meaningful proposal
coverage. Both V1 and V2 remain shadow-only.

## Typed utility contract 与 context-conditioned abstention

V4 的 broad `DENSITY_RELIEF / CORE_UTILIZATION 50→40` cohort 已固定为负面
基线：raw Pareto harmful rate=`7/8`，不能通过修改旧 gate 进入 production。下一
阶段使用 `TIMING_RELIEF_BUDGETED_V1`（定义在
`tehm.physical.utility_contracts`）作为独立 typed utility contract。它保留 raw
Pareto verdict，另外预注册 WNS/TNS objective、area/power resource budget、hard
oracle 和 OOD 行为；contract verdict 不能覆盖 raw verdict。

`select_contract_proposal` 只允许以下闭环：

```text
ready action-bound calibration + baseline PPA + hard oracles + graph context
  → read-only physical prediction
  → interval fits every contract boundary ? PROPOSED : ABSTAINED
```

缺少 action binding、baseline、obligation、hard oracle、ready calibration 或
区间证据时必须 abstain；该入口不调用 `record`、canonical capture、lifecycle
status 或 activation，并始终返回 `promotion_eligible=false` 和
`canonical_memory_mutation=none`。当前 8 条 V4 样本上的 4/8 contract pass 只
是 retrospective contract-design evidence，不能替代后续 2 条 calibration + 4
条 held-out A/B 的 prospective 验证。

## 由 shadow 到 candidate 的下一道门

shadow 记录本身不构成候选规则。进入 lifecycle 前还必须有真实可执行 A/B：
冻结 target scope、两 arm 实际不同、obligation coverage 达标、无 hard
regression、rollback receipt 可验证，且 `status_version` 未发生漂移。当前 v3
freeze 的真实 ORFS trial verdict 是 `inconclusive`，所以不能 promotion。

## 独立 Shadow Campaign v1

`tehm.parametric.shadow_campaign` 将 proposal 包装为外部实验记录，不把
shadow log 放入 `tehm.sqlite`。推荐的两阶段运行方式为：

```text
prepare (frozen read-only DB + cases)
  → shadow_events.jsonl (hash-chained receipts)
  → fixed-policy execution outside TEHM
  → outcomes JSONL
  → append outcomes
  → join + metrics/report
```

receipt 的 join identity 固定绑定：

```text
target_graph_context_digest
+ action_digest
+ policy_digest
+ bundle_digest
+ manifest_digest
+ proposal_digest
```

Decision cases declare `candidate_actions`; the campaign harness expands each
case into one shadow receipt per candidate and assigns deterministic
`candidate_rank` values while retaining the common target/case identity.  If
calibration is action-signature-bound, a case must provide
`calibration_policies[action_digest(action)]` for every candidate.  Missing
bindings are refused rather than reusing a policy calibrated for another knob
value.  Outcome rows for expanded cases should therefore use `receipt_id`
(case-id-only lookup is intentionally ambiguous when a target has multiple
candidates).

Receipt 的机器可读契约固定在
`memory/evaluation/parametric_shadow_receipt_v1.schema.json`；运行时仍由
`build_receipt` 做 fail-closed 语义校验，schema 不授予 promotion authority。

### Action-conditioned physical retrieval（shadow-only extension）

`PhysicalEffectMemory.predict(..., action=...)` 是向后兼容的可选读路径。未提供
action 时保持既有 `family + graph_context` 行为；提供 action 时只保留 transition
action 与候选 action 的 domain、family、config-edit key 集合及 normalized value
一致的样本。transition action 缺失或不兼容会显式返回
`no_action_compatible_contexts`，不会回退到 family-wide profile。校准 policy 还会
绑定这一完整 signature：mixed/missing/invalid action provenance 的 held-out cohort
直接 `firewall_failed`；已绑定 policy 遇到 query signature 缺失或不一致时分别以
`calibration_action_signature_required`/`calibration_action_signature_mismatch`
abstain，而未绑定 policy 不得服务 action-conditioned query。该扩展不改变 schema、
不写 canonical、不声称对数值 knob 做可微插值；数值 action 泛化仍须由独立
calibration/observation 证明。

当前 shadow-only 实现还提供一个更严格的 typed-action 描述入口。若 action
声明了 typed 字段，则必须同时给出单一 `knob`、`direction`、有限的
`relative_change`（或百分比形式）和 `operation_point`；这些字段与 exact
config-edit key/value 一起进入 signature。字段不完整或 operation point 非法时
直接 `invalid_action_signature` abstain。这样可以为后续数值 action 研究记录类型
边界，但不会把 relative change 当作已经验证的插值模型。

校准器默认保持历史的 weighted-mean interval。实验可显式选择
`interval_method=split_conformal_residual_v1`，此时 policy 保存独立 held-out
residual 的有限样本保守 order-statistic radius，预测端只在 policy 明确携带该
radius 时应用 split-conformal interval；缺失 radius、lineage firewall 或 coverage
仍然按原规则 fail-closed。

### Lineage-grouped calibration / Pareto safety（v1）

后续 calibration 不再按 observation row 直接汇总 coverage。外部样本必须含
`lineage_id`、point `predicted` 和真实 `observed_deltas`；
`tehm.parametric.calibration.calibrate_lineage_grouped` 先按 lineage 计算
split-conformal residual radius，再报告每个 lineage 的 coverage，最后对
lineage 等权汇总。训练与 calibration/held-out lineage 重叠、lineage 数不足或
任一物理指标 support 不足都会 fail closed。

安全定义也固定在 receipt 中，而不是由模型输出解释：`harmful` 是任一指标
超过 `max_regression`（WNS/TNS 的负回退、area/power/congestion/DRC 的正回退）；
`pareto_safe` 是无 harmful 指标且至少一项指标沿有利方向改善。校准报告即使为
`ready_for_shadow`，也保持 `shadow_only=true`、`promotion_eligible=false`、
`canonical_memory_mutation=none`；只有独立真实 A/B、rollback、registry 与
obligation 证据才能进入后续 candidate gate。

此外，lineage-grouped calibration 还必须包含至少一条 `pareto_safe=true` 的
观测；全为 `NEUTRAL` 的 cohort 只能说明 flow/oracle 完成，不能建立可消费的
shadow utility policy，因此返回 `shadow_calibration_failed`。该 gate 只阻止无
utility 的 shadow policy，不会把任何 Parametric 结果写入 canonical 或 production。

#### 为什么仍然不能写 canonical / 进入 production

这不是实现遗漏，而是证据边界：Parametric 的输出是对连续 knob 的 point
prediction/区间建议，不是已验证的 executable rule。若它写入 canonical，下一条
query 就会把自己的预测当作 learner support，造成 train/held-out 污染、同一
lineage 的重复计权和 feedback loop；而 canonical 的 `record` 还要求真实
transition、verifier snapshot、obligation transfer、lineage 与 provenance，不能
用 shadow proposal 补齐。若直接进入 production retrieval，则模型输出会绕过
symbolic applicability、typed action signature、rollback 与 registry authority，
把“预测可能改善”误当成“可执行且安全”。

因此运行时只读 `PhysicalEffectMemory.predict`，外部 JSONL shadow log 才是唯一
可追加载体；log 的 hash-chain、join identity 和 canonical counter snapshot 用来
审计，但不授予写权限。只有后续独立 cohort 在精确的
`platform|family|dataset_tier|action_signature` 分区中通过 lineage-disjoint
split-conformal coverage、harmful-rate/Pareto safety，并且真实 A/B 同时通过
rollback、registry、obligation 和 cross-lineage TE，才可生成 candidate。最终
promotion 仍由 lifecycle authority 的六项门控合取决定，Parametric proposal
本身永远不是 promotion authority。

当前代码对应入口为 `tehm.parametric.shadow`、
`tehm.parametric.shadow_campaign` 与 `tehm.parametric.calibration`；新的真实
ORFS/PPA 提取器 `tehm.physical.orfs_ppa` 和
`memory/scripts/run_real_orfs_lineage_calibration_v1.py` 只产生外部报告，不打开
SQLite。结构负例与 Yosys 等价检查位于 `tehm.rtl.compatibility` /
`tehm.rtl.equivalence`；未知或失败的等价结果不能满足晋升门。

可用以下入口积累外部 calibration（不会打开 TEHM SQLite）：

```bash
python3 memory/scripts/run_parametric_lineage_calibration_v1.py \
  --input observations.json --output lineage_calibration.json
```

JSONL 具备 sequence、previous digest、event digest、幂等去重和不完整尾记录
恢复；完整记录被篡改时 fail-closed。outcome 重新计算六维 physical delta，缺失
指标保留为 `null`。若 canonical counters 在执行前后变化，记录为
`INVALID_MEMORY_MUTATION`，不会进入 joined metrics。

入口脚本：

```bash
python3 memory/scripts/run_parametric_shadow_campaign.py \
  --phase prepare --db /path/to/frozen/tehm.sqlite \
  --cases prospective_cases.jsonl --readiness parametric_readiness.json \
  --replay-evidence replay_receipt.json \
  --prospective-manifest prospective_manifest.json \
  --out-dir /tmp/tehm-parametric-shadow

python3 memory/scripts/run_parametric_shadow_campaign.py \
  --phase outcomes --outcomes fixed_policy_outcomes.jsonl \
  --out-dir /tmp/tehm-parametric-shadow

python3 memory/scripts/run_parametric_shadow_campaign.py \
  --phase join --out-dir /tmp/tehm-parametric-shadow
```

包含 decision case 的 prepare 必须额外提供 observation join 生成的
`shadow_metrics.json`：

```bash
python3 memory/scripts/run_parametric_shadow_campaign.py \
  --phase prepare --prospective-manifest prospective_manifest.json \
  --observation-metrics /tmp/tehm-parametric-shadow/shadow_metrics.json \
  ...
```

该门控会在产生任何 decision receipt 前 fail-closed 检查预注册的 proposal 和
outcome coverage、obligation coverage、hard OOD ceiling、harmful rate，以及
manifest 指定物理指标的 interval coverage；观察轮未达标时 decision round 不会
启动。

默认输出放在 `/tmp`，最终 evidence freeze 只应复制 receipts、reports、DEF 和
摘要；不能把 ORFS 的可再生 `RUN/logs/results/objects` 写回 `/data1`。
ORFS scratch campaign 的 durable evidence 可用
`memory/scripts/promote_orfs_evidence.py --scratch-root ... --evidence-root ...`
显式提升；该命令不会删除 scratch，也不会复制 RUN 的 results/objects。

## Prospective observation pilot result

2026-08-17 的独立 pilot 使用两个新 lineage，在 shadow receipt 写入后才执行
固定 action。receipt/outcome join 为 2/2，canonical counters 零变化；两条 proposal
均被现有 calibration 的 OOD gate 拒绝（reason distribution 为
`out_of_distribution:2`，OOD distance 为 0.945599–0.952020，proposal coverage=0），真实 outcome 的
obligation coverage=0.333 也未达到预注册门槛 0.95。因此这轮只证明了
future-lineage firewall、时间顺序、只读与 fail-closed 行为，不能证明 Parametric
预测质量或 action ranking；decision round 与 Parametric View 均保持关闭。证据留存
在 `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v3/`，不属于现有
canonical v3 freeze。

该历史 pilot 的 frozen manifest 生成于 decision gate 加入之前；不得回写其
manifest digest 以“修复”历史证据。下一轮必须用带 `decision_gate` 的新 manifest
重新冻结，历史 pilot 仅作为 observation/firewall 记录保留。

## v8/v9 prospective observation result

v8/v9 使用新 calibration policy 完成真实 sky130hs ORFS route/PPA join：2/2
proposal、2/2 outcome、obligation coverage=1.0，OOD distance=0.430214，canonical
counters 零变化；但 harmful rate=1.0、WNS interval coverage=0/2，故预注册
decision gate 失败。随后重放 action-conditioned shadow receipt；该 cohort 的
action signature 与现有 evidence pool 完全兼容，预测数值和失败原因均未改变。
这证明 action provenance 已接线，但不把它误写成模型质量改进。证据位于
`/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v89-action-conditioned/`。

## v10/v11 fresh calibration and v12/v13 prospective observation

为验证 calibration/support 是否能在不改变 hard OOD ceiling 的情况下恢复，新增
`future-parametric-v10/v11` lineages 并执行真实 ORFS base/action flow。4 条 firewall
lineage 中只有 2 条 sky130hs 样本可评估；2 条 sky130hd 的 TritonRoute
SIGABRT/route-congestion 仅作为 infrastructure failure 留存，不能转成 physical
positive。合并既有只读样本后的 `sky130hs|DENSITY_RELIEF|research` policy 为
`coverage_failed`：18/24 metric comparisons=`0.75`，低于预注册 `0.80`，distance
max=`0.430214`，physical memory count 保持 `114 → 114`。因此 policy 没有晋级为
ready，也没有改变 canonical v3。

随后用该 policy 冻结两条新的 prospective lineages（v12/v13），每条均真实完成
base/action ORFS 并 join outcome（2/2），但 proposal 均因
`calibration_policy_not_ready` abstain；proposal coverage=`0`，obligation coverage
min=`0.333333`。decision gate 失败，decision prepare 返回 `rc=2` 并拒绝产生
decision receipts；candidate ranking、activation 和 Parametric View 仍关闭。
完整证据位于 `/data1/zhangdy/tehm-campaigns/tehm-p2-fresh-calibration-v10v11/`
与 `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v12v13/`，均在
canonical v3 freeze 之外。

## v21–v23 fresh observation result

v18–v20 were consumed only as calibration held-out support; the independent
v21–v23 cohort was then prepared from three new lineages. All three receipts
and all three ORFS PPA outcomes joined, while two proposals abstained for OOD
and one proposed. The cohort therefore had proposal coverage `0.333333`,
harmful outcome rate `1.0`, and minimum obligation coverage `0.333333`.
The pre-registered decision gate rejected preparation with rc=`2`; no decision
action was executed and no canonical row changed. Evidence is compacted at
`/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v21v23/`; its source
ORFS work remains under `/tmp/tehm-p2-calibration-expansion-v14v16`.

## v30–v32 observation and action-value calibration follow-up

The next disjoint sky130hs cohort (v30–v32) joined all three ORFS outcomes,
but only one receipt was proposed.  Proposal coverage was `0.333333`, harmful
rate `1.0`, obligation minimum `0.333333`, and WNS interval coverage `0/1`;
the pre-registered decision gate rejected preparation with rc=`2`.

An action-value probe (v33–v38) also showed that matching only
`config_edit_keys` is too broad: mixing `CORE_UTILIZATION=22` and `=40`
reduced held-out coverage to `0.583333`.  The physical action signature now
includes normalized config-edit values, so a knob value cannot borrow an
unrelated empirical pool.  With same-value action-40 support, held-out
coverage was `0.416667` and the policy remained `coverage_failed`; no shadow
or decision receipt was emitted.  These campaigns remain external evidence,
with ORFS work under `/tmp` and compact reports under
`/data1/zhangdy/tehm-campaigns/`.

The follow-up v39–v44 action-40 cohort added six independent sky130hs lineages;
all six ORFS pairs were evaluatable. With v33–v38 as staging support, exact
action-signature held-out coverage was `0.583333` (area `0.667`, power `0.167`,
TNS `1.0`, WNS `0.5`), so calibration remained `coverage_failed`. This is
additional support evidence only; no shadow/decision receipt or promotion was
authorized.

The v45–v50 action-40 cohort then produced a ready exact-signature policy from
six new held-out lineages (aggregate coverage `0.833333`, maximum distance
`0.1677005`; area/power per-metric coverage `0.667/0.667`). A disjoint v51–v56
observation cohort joined 6/6 outcomes and proposed 4/6 cases; two abstained
for uncertainty. Its proposal coverage was `0.666667`, harmful rate `0.5`,
obligation minimum `0.333333`, and area interval coverage `0.75`, so decision
preparation remained fail-closed.

The calibration staging SQLite digest is now carried through case binding and
shadow receipt provenance. Running those cases against canonical v3 (which
lacks the action-40 support rows) is rejected at prepare time rather than being
reported as a valid zero-coverage observation.

The v57–v62 action-40 follow-up added six new future lineages and ran a complete
strict-signoff plus timing-oracle pass over all 12 before/after projects. The
reports are compacted at
`/data1/zhangdy/tehm-campaigns/tehm-p2-action40-calibration-v57v62/`; dirty
strict results remain explicit evidence. Exact-signature calibration measured
aggregate coverage `0.708333` (area/power/TNS/WNS `0.667/0.667/1.0/0.5`), so
the policy stayed `coverage_failed` and no prospective receipt was generated.

The v63–v68 action-40 calibration cohort added six disjoint sky130hs lineages;
all 12 before/after projects completed strict-signoff and timing checks. The
exact-signature policy reached aggregate coverage `0.916667` (area/power/TNS/WNS
`1.0/1.0/1.0/0.667`, maximum distance `0.168773`) and is bound to staging
snapshot digest `76de1868543f19259a10be71e6d4d85508bf921a980d22737c4ca74e4f7f15d2`.
Because WNS coverage remains below the pre-registered per-metric threshold,
this policy is observation-only.

The v69–v74 observation cohort used six further disjoint future lineages and a
verifier-produced v3 replay receipt. The append-only shadow log joined 6/6
receipts with 6/6 ORFS outcomes and obligation coverage `1.0`; maximum OOD
distance was `0.051424`. The decision gate remained closed because proposal
coverage was `0.666667`, harmful rate `0.75`, and area/power/TNS/WNS interval
coverage was `0.75/1.0/1.0/0.25`. The compact evidence is at
`/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v69v74/`; no ORFS RUN tree
or canonical memory mutation was promoted.

Calibration reports now also retain a deterministic nearest-distance
`selective_risk_coverage` curve.  It reports retained sample coverage, metric
interval coverage, and interval-miss risk at each observed distance threshold;
it is diagnostic only and cannot relax the hard OOD or coverage gates.  For
v39–v44 the full retained cohort has risk `0.416667`, confirming that the
remaining block is empirical coverage/distribution mismatch rather than a
missing diagnostic.

## 实现与复核

- 实现：`memory/tehm/parametric/shadow.py`；导出：
  `memory/tehm/parametric/__init__.py`。
- prospective manifest 的 decision gate：
  `memory/scripts/prepare_parametric_prospective_manifest.py` 与
  `tehm.parametric.shadow_campaign.validate_observation_gate`。
- 单元契约：`memory/tests/test_parametric_shadow.py`；campaign integrity、join 和
  metric tests 也在同一测试文件中。
- 下一次 evidence freeze 必须重新收录这些源码和测试，并把 freeze 的固定测试
  计数更新为最新回归实际值；现有 canonical v3 snapshot 为 225；当前 canonical v3 bundle 位于
  `/data1/zhangdy/tehm-evidence-freeze-v3-refresh/`，在新 bundle 完成前不得覆盖它。
  唯一 canonical 指针及 digest 见
  [`../evaluation/canonical_freeze_pointer_v1.json`](../evaluation/canonical_freeze_pointer_v1.json)。
  或宣称新代码已被现有 v3 digest 覆盖。
