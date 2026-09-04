# TEHM — Typed Executable Hardware Memory

**Versioned Verified Hardware Experience Graph**：R2G 记忆平面的完整替换方案。

设计文档：[`docs/Typed_Executable_Hardware_Memory_R2G.md`](docs/Typed_Executable_Hardware_Memory_R2G.md)

> 本目录是设计文档指定的工作路径。目标是用三层 + 五视图的 Typed Executable
> Hardware Memory **完整替换** R2G 原始 symptom-indexed statistical memory，
> 同时把原版 R2G memory 保留为独立 baseline（二者严格隔离，honesty H5）。

---

## 当前进度：Phase 0 - 11（实现骨架；能力验证分开记账）

设计文档中的 Phase 0–11 已有对应模块和脚手架，但“模块存在”不等于“研究能力
已验证”。截至 2026-08-27，工作树已补齐 P0 数据完整性/重现性与 P1
结构化绑定证明，并生成当前源码绑定的 v4 development freeze；下方 v3 数字仍是
历史外部 freeze 的声明，不能当作本工作树的现时测试结果。v3 的完整 reproduce
仍需要它自己的 freeze 指针；当前 v4 可直接由本地 `evidence/` bundle 重放。

<!-- TEHM_EVIDENCE_V3_START -->
### Evidence Contract v3（由 freeze manifest 自动生成）

- Freeze contract：`tehm-evidence-freeze-v3`；唯一 canonical bundle id：
  `tehm-evidence-freeze-v3-refresh`；路径必须通过机器可读指针
  后续脚本和报告必须以机器可读指针
  [`evaluation/canonical_freeze_pointer_v1.json`](evaluation/canonical_freeze_pointer_v1.json)
  为唯一解析入口；不得引用其它 v3 目录；
  当前开发基线为 tag `tehm-p0-baseline-20260817-postaudit`（commit `86178eb`）。
- TEHM snapshot：116 transitions / 606 views / 114 physical effects / 2 rules。
- 回归：`225 passed`；H1–H12 + A1 审计：`ALL GREEN`；H7=`1 activations preserve obligation honesty`；H10=`1 real ORFS trial(s) have verified rollback receipts`。
- H11：export → import → export byte-stable；reproduce 入口为 `reproduce.sh`。
- M0/M1/M8 pilot：M0=0/6，M1=0/6，M8=6/6；该结果仍不是普适 benchmark。
- Physical calibration：memory count 114 → 114；策略状态：`ihp-sg13g2|DENSITY_RELIEF|research=ready, ihp-sg13g2|PLACEMENT_DENSITY_RECOVERY|research=ready, ihp-sg13g2|ROUTING_CAPACITY_RECOVERY|research=ready, sky130hd|DENSITY_RELIEF|strict_clean=ready, sky130hd|PLACEMENT_DENSITY_RECOVERY|strict_clean=ready, sky130hd|ROUTING_CAPACITY_RECOVERY|strict_clean=ready, sky130hs|DENSITY_RELIEF|research=ready, sky130hs|PLACEMENT_DENSITY_RECOVERY|research=ready, sky130hs|ROUTING_CAPACITY_RECOVERY|research=ready`。
- Parametric readiness：`READY_FOR_IMPLEMENTATION`（只表示可运行 shadow RFC）；Parametric
  View：`NOT_IMPLEMENTED`；lineage diversity 2/2。该 readiness 不授予 canonical 或
  production authority。
- Source binding（HEAD、dirty-diff、workspace state digest）记录在 `bundle_manifest.json`，由 reproduce 验证。

Parametric View 只有在 distance、coverage、uncertainty、lineage diversity 四项同时通过，并且该 bundle 可重放后，才允许进入 shadow RFC。
<!-- TEHM_EVIDENCE_V3_END -->

### Current v4 development freeze（2026-08-27）

当前源码已生成便携 development freeze：
[`evidence/tehm-evidence-freeze-v4-dev`](../evidence/tehm-evidence-freeze-v4-dev)。
其 bundle digest 与 manifest digest 以该目录内 `bundle_manifest.json` 为准，源码绑定
也记录在那里；不在 README 中复制易失的 digest 文本。内含 schema-v4 canonical snapshot、真实 Icarus
training receipts、P0 dependency-free regression smoke、H1–H12/A1 审计；H9 与
H11 均为实际检查而非 N/A。pytest 仍因当前环境缺少依赖未计入该 development
freeze。

### 工具链复现入口（2026-08-30）

TEHM 后续 ORFS/RTL/graph 执行统一使用 R2G 的 bootstrap 和同一份用户目录 pin，
不再让每个 flow 从 `/usr/bin`、`/opt` 或 PATH 自行猜工具：

```bash
export R2G_PREFIX=/data1/zhangdy/Tools/tehm-toolchain
export ORFS_ROOT=/data1/zhangdy/Tools/OpenROAD-flow-scripts
bash r2g-skills/eda-install/bootstrap.sh --dry-run --prefix "$R2G_PREFIX"
```

确认计划后才执行安装；安装生成的 `references/env.local.sh` 通过
`R2G_ENV_FILE` 作为唯一运行时 pin。四个 R2G skill 的 `_env.sh` 已保持字节一致，
解析顺序为显式 pin ＞用户/conda/ORFS 候选 ＞ PATH/宿主机回退，且用户本地 PDK
优先于 `/opt/pdks`。TEHM 的 `preflight_orfs_toolchain()` 仍会对正式 evidence
重放 ORFS root、工具版本、SHA256 和 capability；`/usr/bin`/`/opt` 只能作为显式
`bound_external` 诊断，不能进入 `bound_internal` evidence。

当前 R2G bootstrap 的下载 URL、ORFS 默认分支及部分 conda/pip spec 尚未全部锁定，
所以开源复现还需要发布 TEHM toolchain manifest（ORFS commit、工具/PDK 版本与
SHA256、capability probes）。个人目录用于缓存和按 manifest 分目录安装，仓库只发布
manifest、脚本和验证结果，不提交大型二进制。

### C4/C5 RTL Asset Memory shadow（2026-08-26）

已用 `req_ack_bug` / `req_ack_bug2` 两个独立 training lineage 生成真实
`CapabilityGapReceipt`，并通过 manifest/结构化槽位绑定同一
`RTL_REWRITE_TEMPLATE`。模板在两个 training lineage 与 held-out
`req_ack_bug3` 上均由 Icarus/vvp 通过 target + frozen regression；异构
`valid_ready_bug` 被兼容性边界拒绝为 `INAPPLICABLE`。asset 只在派生 shadow
数据库注册为 `candidate`，不写 canonical、不进入 production。由这些独立 receipt
推导的七项 asset authority gate（schema/static/verifier/compatibility/
cross-lineage/regression/rollback）当前均为 `true`，但这只是 audit-only 的
`asset_promotion_eligible`，没有执行 lifecycle promotion 或 production runtime
导入。完整 receipt 位于
[`evidence/tehm-asset-gap-shadow-rtl-r1-dev/asset_gap_shadow_report.json`](../evidence/tehm-asset-gap-shadow-rtl-r1-dev/asset_gap_shadow_report.json)，
重放入口为 `scripts/build_rtl_asset_gap_shadow.py`。
当前新增的 `assets.authority` 还提供 content-bound
`record_asset_authority()`/`verify_asset_authority()` 与 strict `promote_asset()`；
它们会重新读取 registry asset、校验 content digest，并将 validation/binding/
rollback evidence 写入 append-only ledger；replay 时从 ledger 重新推导七项 gate，
再允许显式 authority 调用 lifecycle。shadow builder 已写入并验证这两类 ledger
rows，但仍只保持 candidate-only 结论；没有自动 promotion 或 production import。
ledger schema 初始化不再通过会隐式提交的 `executescript` 完成；整组 evidence
rows 与 authority receipt 现在共用 savepoint，晚到的 immutable-row 冲突会整体回滚，
且不会提交调用方已有事务。
Gap detector 现在只通过 `assets.registry.get_asset()` 消费 promoted asset：兼容性
profile、全部 contract 和 content digest 必须先完整校验；JSON 合法但 digest 被
篡改的 status row 会被忽略并重新报告 capability gap，不会伪造覆盖证据。

### C1-C8 RTL capability attribution（2026-08-26）

在上述 asset shadow 之上新增了 evaluation-only capability harness：基线策略、
候选策略和移除 asset 的 ablation 都重新调用 Icarus/vvp；候选策略的 runtime
load receipt 绑定到实际执行 receipt，而不是只读取数据库中的 policy row。两个
training lineage 的 target failure 均被修复，独立 held-out `req_ack_bug3` 通过，
异构 `valid_ready_bug` 保持 `INAPPLICABLE`，因此 C1–C8 attribution gates 全部为
`true`。这只证明该小型 fixture 上的可归因行为变化，capability 仍为
`candidate`，`capability_promotion_eligible` 是审计结果，不执行 capability 或
production promotion。报告位于
[`evidence/tehm-capability-attribution-rtl-r1-dev/rtl_capability_attribution_report.json`](../evidence/tehm-capability-attribution-rtl-r1-dev/rtl_capability_attribution_report.json)，
重放入口为 `scripts/build_rtl_capability_attribution.py`。

### C1-C8 ORFS capability attribution（2026-08-26）

真实 ORFS fail→pass pair 的 attribution lane 现在也会把 C1–C8 写入派生 v4
数据库：每个 gate 都绑定不可变的 capability evidence row，并重新校验 candidate
policy snapshot 与 evaluation-runtime load receipt；最新的 candidate load receipt
还绑定实际 runtime execution receipt，避免仅凭 snapshot lookup 满足 C3。当前
failpass-r2 cohort 的
C1–C8 与 capability authority receipt 均为 `eligible=true`，但这仍是
evaluation-only capability evidence；六项 rule promotion gates 仍全部缺失，
`promotion_attempted=false`、`production_promotion_eligible=false`，canonical
memory 未发生变化。C8 的 `gain_without_memory` 现在由实际执行的 baseline
`M_t+1 - ΔMemory` ablation receipt 推导，并绑定独立 policy-load receipt，不再接受
调用方布尔值。重放入口为 `scripts/build_orfs_capability_attribution.py`，
报告为
[`evidence/tehm-capability-attribution-orfs-r2-dev/capability_attribution_report.json`](../evidence/tehm-capability-attribution-orfs-r2-dev/capability_attribution_report.json)。

authority evaluator 现在区分 `NOT_ESTABLISHED`、`FAIL` 和 `PASS`：空 gate map
会报告六项 `NOT_ESTABLISHED`，而已提交但未达阈值的证据才报告 `FAIL`；新增的
`not_established`、`failed`、`all_gates_established` 只改善 receipt 可审计性，不
改变六项合取或 promotion 行为。
Capability attribution 的 C1 现在还提供 `memory-delta-v1`：strict campaign 必须
列出实际新增/删除/修订的 transition、rule、asset、causal path 或 capability ID，
并校验 delta 内的 baseline/candidate memory digest；仅传入两个不同字符串不能再
满足 strict C1。历史通用 fixture 仍保留兼容模式，新 ORFS/RTL/preflight 入口均已
切换到 strict memory-delta receipt。该校验仍是 evaluation-only，不会写 canonical
memory、触发 lifecycle promotion 或使 policy 进入 production runtime。
当 attribution receipt 进入 capability authority 时，C1 receipt 会随 authority
payload 保存并在 replay 中重新归一化/校验；ORFS/RTL 的 C1 evidence ID 也绑定完整
delta receipt，而不是只绑定两端 digest。
rule lifecycle status 也已采用同一 replay 边界：`set_status()` 对同状态只接受
provenance 完全一致的幂等重放，对冲突直接拒绝；状态转换改为 UPDATE 后完整重读校验，
`get_status()` 对 status/version/provenance/更新时间损坏 fail-closed，不再使用
`INSERT OR REPLACE` 静默覆盖。
普通 `crystallize_all()` / `crystallize_affected_groups()` 也不会再通过同一
`rule_id` 改写已 promoted rule 的定义或 source witness；完全相同的重建才是幂等 no-op，
任何 projection 漂移都必须进入显式 shadow revision 与 authority 路径。这样新增训练
证据不会在没有晋级收据时悄悄改变 production retrieval 的规则语义。
现在 rule promotion 也有独立的数据库绑定 authority seam：
`lifecycle/rule_authority.py` 将每个 gate 的 payload 写入 immutable evidence ledger，
并把 rule 内容 digest、candidate `status_version` 与真实 winning `tehm_trials` 行绑定。
`record_rule_authority()` 从这些行重新推导 rollback、registry、obligation、cross-lineage
TE、harmful-rate 和 conformal coverage；`verify_rule_authority()` 在消费前再次重放。
因此 production wrapper 没有 authority receipt 时即使收到全 `True` map 也保持拒绝，只有
验证通过的 receipt 才能调用 `promote_rule()` 或 strict trial authority；失败尝试只留下
审计行，不改变 lifecycle。
能力 authority 的 gate evidence 与最终 authority row 现在通过同一 savepoint
原子写入；不可变 evidence 冲突或后续写入失败会回滚整组 C1–C8 rows，不留下部分
authority 证据。若调用方已有事务，则只释放 savepoint，由调用方继续负责最终提交。
Capability registry 的 lifecycle replay 也已收紧：registry reader 会校验 status、版本、
provenance 与时间字段；重复 evidence 会逐字段比较 split/verdict/lineage/digest。已
promoted capability 的再次 promotion 只有在 authority provenance 完全一致时才幂等，
冲突会拒绝；注册重放返回数据库中的实际 lifecycle status，不会把 promoted 状态伪装成
candidate，也不会覆盖 production provenance。

rule authority 的 `cross_lineage_te` 现在还支持显式的
`causal_transfer_receipt_ids` 输入：每条 receipt 必须在同一 shadow DB 中由
`verify_causal_transfer()` replay 为 `L4_TRANSFER_SUPPORTED_MECHANISM`，并携带至少
两条 training lineage 与独立 held-out lineage；系统据此生成 authority evidence，拒绝
把手写 `te_pass=true` 与 ledger witness 混用。该桥只建立可重放的 gate evidence，
不会自动补齐 rollback、registry、obligation、harmful-rate 或 conformal gate，也不会
改变 canonical/lifecycle/runtime。传入 `rule_id` 时还会绑定 path mechanism family、
held-out action domain、rule source transition 和 training campaign；因此一张合法的
L4 receipt 不能被复用给无关 candidate rule。

`build_trial_authority_evidence()` 还可从指定 `tehm_trials` row 及其
`tehm_activations` rollback/obligation witness 生成 database-bound 的
`rollback_verified`、`obligation_coverage`、`registry_verified` 以及由
produced transition observation delta 明确记录的 utility evidence；pair JSON 与
activation row 缺失或不一致时直接拒绝。它不会从 target PASS
推断 harmless，也不会伪造 conformal 或 cross-lineage 证据，生成的 evidence 仍需与
独立 L4/calibration rows 一起交给 `record_rule_authority()`。

真实 ORFS `run_pending_orfs_trials(..., production_authority=True)` 现在已经接通这条
projector：trial 落库后先从同一 DB 重放 activation witness，再生成并保存
`RuleAuthorityReceipt`，最后才调用 strict lifecycle wrapper。`promotion_gate_inputs`
在该模式下只作为诊断快照，不能授予 authority；独立的 harmful/conformal rows 通过
`promotion_authority_evidence` 传入，cross-lineage 必须通过
`causal_transfer_receipt_ids` 由 L4 ledger replay 产生。崩溃恢复路径会重新生成同一
receipt；缺失/篡改 witness 只会生成不完整、不可晋级的 receipt，绝不回退到调用方
布尔值。兼容模式仍保留给旧的确定性 fixture，不能代表 production authority。
authority receipt 的 trial binding 只覆盖 arm/pair 和测量结果；runner 后续追加的
`registry_authority`、gate summary 等派生审计字段不进入 witness digest，因此崩溃恢复
后可以重放同一 receipt，同时仍会拒绝 pair 或结果字段篡改。

### Capability retention replay ledger（2026-08-27）

新增 `capability.retention.record_capability_retention()` /
`verify_capability_retention()`，把原先纯函数的 retention replay 绑定到不可变
capability、candidate policy snapshot digest、成功的 runtime load receipt，以及
`heldout`/`ab` 的独立 lineage。receipt 以内容 digest 生成 ID，并与
`tehm_capability_evidence` 的 `capability_retention` 行在同一 savepoint 中写入；
回放失败会留下 `retained=0` 的审计证据，但不会修改 capability lifecycle，也不会
进入 production policy。验证阶段会重新校验 snapshot/load JSON、ledger payload、
registry evidence 和纯 evaluator 结果，直接 SQL 篡改或 training split 均
fail-closed。外部 ORFS retention builder 默认仍保持只读，只在报告中补充 split、lineage
和 policy digest，不能把外部回放冒充 learner support；显式传入独立的
`--retention-ledger-db` 后，builder 才会把同一 replay 写入隔离副本并立即执行
`verify_capability_retention()`，输出 authority-grade receipt。该 ledger 仍不改变
source/canonical DB 或 production policy。C7 authority evidence 现在可显式携带
`retention_receipt_id`；一旦提供，authority 记录与消费都会重新验证 retention
ledger，failed/stale receipt 会阻断 capability authority。

真实 causal transfer 的 ORFS pair 现在也有显式 split 入口：
`run_orfs_add_designs_campaign.py --dataset-split heldout` 会把 split 写入 source
freeze 和每个 manifest item；`capture_pairs()` 只有在 `training` 且 full oracle
完整时才允许 `learner_eligible=1`。heldout/calibration/ab pair 即使通过全部
oracle，也只作为隔离 staging 的 audit evidence，并可与 training path 放在同一派生
DB 中供 `evaluate_causal_transfer_batch.py` 重放。请求 training 但 oracle 不完整会
fail-closed 降级为 calibration，修改 manifest split 也会被 source-freeze request
校验拒绝；该入口不写 canonical、不触发 consolidation 或 production runtime。

为支持后续真实 held-out 批量积累，新增 `scripts/build_orfs_capability_retention_batch.py`。
它先整体校验 manifest 的 case/lineage 唯一性、项目路径和 attribution firewall，再逐条
调用上述单 pair builder；不完整 pair 会保留在失败分母中，少于默认两个独立 lineage 时
状态为 `NOT_ESTABLISHED`，而不是误报 PASS。显式指定 `--retention-ledger-db` 时所有
receipt 写入同一个 source DB 的隔离副本并逐条重放验证；聚合报告仍固定
`canonical_memory_mutation=none`、`promotion_attempted=false`，不能替代 capability
authority 或 rule promotion。若 ledger 可写，批处理还会在隔离库生成一个内容寻址的
`capability_gate:C7` 聚合 evidence，并列出全部 retention receipt IDs；后续 authority
消费该引用时会逐条重放这些 receipt，而不是信任聚合布尔值。

### Online consolidation trigger lane（2026-08-26）

`evolution.observe_transition()` 现在在记录 transition/causal/novelty/conflict
事件后，额外生成确定性的 `ConsolidationTriggerReceipt`：novel mechanism、达到
learner support、冲突或 harmful outcome 会标出受影响的 effect group，并写入
hash-chained `CONSOLIDATION_TRIGGERED` 事件。触发后会继续运行两个
`dry_run=True` 投影（受影响 group 与完整 campaign rebuild），把等价性、规则
来源 witness 和 `mode=preview` 写入 `RULE_REVISION_PROPOSED`；同时由纯函数决策层
给出 `RETAIN`/`ADD`/`MERGE`/`REVISE`/`SPLIT`/`QUARANTINE` 等 shadow operation。
该 proposal 仍是
shadow-only，不写 `tehm_rules`、`tehm_rule_revisions` 或 lifecycle。这里的
*triggered* 只表示满足 consolidation 条件，不表示规则已经生效；learner 支持的
语义谓词现在固定为 `split='training' AND learner_eligible=1`，因此 held-out/
calibration/A-B transition 即使被错误写成 `learner_eligible=1` 也会在 API 和所有
learner 查询入口 fail-closed，并返回 `NOT_LEARNER_ELIGIBLE`，不会触发 consolidation。
novelty lookup 也只承认目标 campaign 的 training learner path；held-out/calibration
shadow path 不能抑制 learner-side 的 `NOVEL_MECHANISM` 触发。
事件写入器本身还会反向解析 transition/causal-fragment/activation 的 canonical
witness；没有同 campaign 的 training membership 时，即使调用方传入
`learner_eligible=true` 也会拒绝，事件链审计同样会报告该违规。
真正的增量 crystallization 仍须由显式、隔离的调用执行，不能自动改变 production
lifecycle。

为让 fast-memory receipt 能够被后续审计和 consolidation 重放，online observation
现在还绑定 typed `mechanism_signature`、`affected_rule_ids` 与
`affected_path_ids`。rule witness 只解析该 transition 直接拥有的 episode-step
来源；path witness 则逐行重放 source transition、training campaign 和持久化 path
校验。缺失、重复、损坏或跨 campaign 的 path/source 关系直接 fail-closed，不会用
effect key 或 mechanism family 猜测受影响规则。上述字段同时写入
`CAUSAL_FRAGMENT_CREATED`、`NOVEL_MECHANISM`、`RULE_CONFLICT`、
`RULE_HARMFUL`、`CONSOLIDATION_TRIGGERED` 和 `RULE_REVISION_PROPOSED` 事件，receipt
仍保持 `path_id=null`（受影响 path 可能是多个），且整个绑定过程与事件链共享同一
savepoint。`ConsolidationTriggerReceipt` 与
`ConsolidationDecisionReceipt` 也复用同一 signature/rule/path witness，调用方无需
依赖事件 JSON 反查。它只增强 shadow/evaluation evidence，不写 canonical rule、lifecycle
或 production runtime。

rule witness 的解析还会重放 `tehm_rule_sources.source_substitution_json` 与 episode
step 覆盖关系，确认引用的 rule 仍存在，并要求该 rule 的全部 source transitions 都是
目标 campaign 的 training learner evidence。缺失、损坏、遗漏当前 transition 或混合
campaign 会拒绝整个 observation，防止只返回一个局部 rule ID。

online observation 的首个 `TRANSITION_CAPTURED` 事件现在额外保存
`online-receipt-v1` 摘要和无 event-id 的预期链序列。重复调用会先校验该摘要、当前
event hash-chain 及 causal fragment 的 content-addressed replay，再返回原始 receipt；
不会因为之后出现新的 shadow path、rule 或 support 而重新解释同一 transition，也不会
追加第二条事件链。摘要损坏、链不连续、fragment ID 漂移或 learner membership 改变时
直接 fail-closed；没有该摘要的旧 capture 链也不会被静默追加第二种解释。该机制只保证 fast-memory 观察的时间一致性，仍不授予 canonical
import、lifecycle promotion 或 production runtime 权限。

canonical import 的绑定现已从 staging 文件哈希扩展为内容级 witness：authority
receipt 会对选中的 external row 在只读 staging DB 中逐条重放，核对 execution record、
transition、training membership、physical effect 及其 typed payload，并保存排序后的
`staging_witness_sha256`。导入时会重新计算该 witness，且在同一 savepoint 释放前再次
检查 observations/staging DB 哈希；任何 witness 漂移、campaign/split 不一致或
TOCTOU 修改都会 fail-closed 并回滚，不能把“文件存在且哈希相同”误当作 canonical
authority。缺少该 witness 的旧 allow receipt 也不再可消费。

另外，calibration expansion 的 staging-only 外部 transition 已改为冲突检查的
immutable insert：重复导入必须 content-equivalent，直接 SQL 篡改或 payload 漂移会被
拒绝，不再使用 `INSERT OR REPLACE` 静默覆盖 evidence。该路径仍只服务隔离校准，
不创建 canonical/learner support；整批 external sample 写入也共用 caller-safe
savepoint，晚到的 malformed sample 会回滚此前已写入的 staging rows。

生命周期 trial writer 也对 UUID-less 兼容调用执行相同的确定性重放检查；相同
`trial_id` 只能 content-equivalent 重放，冲突会失败，不再通过 `INSERT OR REPLACE`
覆盖旧的 trial evidence。

当 preview 需要进入试验阶段时，`evolution.run_shadow_candidate_trial()` 会把 TEHM
连接复制到内存 staging DB，在 staging 内重建并登记 `shadow → candidate`，然后复用
现有 A/B trial adapter。Icarus/ORFS 执行器通过 evaluator callback 注入；即使六项
promotion gate 全部满足，该 API 也只返回可供 authority 审查的 receipt，
`promotion_attempted=false`、production lifecycle 不变，源连接的逻辑 digest 必须保持
一致。receipt 还包含 `isolated-rollback-receipt-v1`，明确记录 source/staging
digest 和 `isolated_staging_discard` authority；它只覆盖 staging DB，不替代真实
RTL/ORFS 文件树的 rollback authority。

Causal evaluation lane 现在有独立的 `tehm.causal.matcher`：在不接入 production
retrieval 的前提下，按 mechanism family/profile、结构细节、required/forbidden
effect、prior action 和 evidence level 做可解释匹配。causal recall 强制所有 path
source transition 属于目标 campaign 的 training learner 集合；兼容 profile 相同但
guard/module/state 等机制细节不符时 fail-closed，不会被误报为可迁移 causal path。
L2/L3 causal authority 与 replication 还会把 controlled edge 的
`campaign_id`/`learner_eligible` 绑定到当前评估 campaign；损坏、重复或空的
`source_transitions_json` 只产生 fail-closed receipt，不会把其它 campaign 的 edge
当作当前 campaign 的 intervention support。`build_intervention_pair()` 只有在
control/treatment 共享同一 training campaign 时才会生成 L2 edge；显式 campaign
不匹配或跨 held-out split 的 pair 只能保留为无效审计 receipt。
此外，L2/L3 authority 不再把单个 edge 与 path source 的一次交集当作充分证明：
共享的 `causal.witness` resolver 会解析直接 transition 或合法 intervention-pair
引用，并要求 path 中每个 source transition 都被同一 campaign 的 training learner
edge 完整覆盖；缺失、重复、未知、跨 campaign 或 malformed witness 均 fail-closed。
Asset promotion/validation 入口也对非 mapping、损坏 JSON、非对象 contract 和损坏
registry row fail-closed，返回不可晋级 receipt 或 unknown-asset 结果，不把解析异常
当作 verifier authority。

### A3 RTL causal retrieval evaluation lane（2026-08-26）

新增 `scripts/build_rtl_causal_retrieval_report.py`，在 v4 freeze 的只读备份上把
training transition 重建为六组 causal shadow path，并用四个未参与 learner 的
RTL lineage 做固定 R0/R1/R2 查询：R0 仅按 transformation family，R1 再加
compatibility profile，R2 再加 mechanism family 与 held-out effect key。报告位于
[`evidence/tehm-causal-retrieval-rtl-r1-dev`](../evidence/tehm-causal-retrieval-rtl-r1-dev)。
本轮真实 Icarus held-out execution 的三组 positive recall@3 都是 `1.0`；在“同一
metadata、但 module 结构不符”的 negative slice 中，R0/R1 的 false transfer rate
均为 `1.0`，R2 降为 `0.0`。这只是可解释 matcher 的 evaluation-only 证据，不是
production retrieval、rule promotion 或 capability gain；报告明确记录
`heldout_learner_eligible=false`、`canonical_memory_mutation=none`、
`promotion_attempted=false`。报告 v4 还记录 causal 机制分、质量重排字段及质量来源；当前训练
path 缺少质量证据，因此按保守先验标记 `NOT_ESTABLISHED`，不把该结果误写成
utility/risk 实测收益。重放入口为报告目录下的 `reproduce.sh`。

2026-08-27 再将 source-transition 数量与 canonical state 的独立 lineage 覆盖纳入
shadow evidence support：单来源 path 的支持分为 `0.5`，至少两个来源且来自至少
两个独立 lineage 才标记 `ESTABLISHED` 并得到 `1.0`；来源行缺失、重复、数量声明
不一致或 lineage witness 不可解析时 evaluator fail-closed。该支持分独立于
`evidence_level` 与 utility/risk，最终只作为 evaluation-only 的
`S_causal × support × U × (1-R)` 因子，仍不写 canonical memory、不进入 production
retrieval，也不满足 promotion gate。

### B3 Online evolution evidence（2026-08-26）

新增 `scripts/build_rtl_online_evolution_report.py`，在 v4 freeze 的 derived DB
中执行一个真实 Icarus learner transition 与一个 held-out transition。learner
transition 会触发 `NOVEL_MECHANISM`/`CONSOLIDATION_TRIGGERED` 并生成
`RULE_REVISION_PROPOSED` shadow receipt；held-out 即使产生诊断事件，也被硬性标记
为 `NOT_LEARNER_ELIGIBLE`，不会触发 consolidation。随后只有显式调用
`crystallize_affected_groups()` 才写入 derived rule/revision，并比较 affected 与
full rebuild。报告显示 `training_triggered=true`、`heldout_not_triggered=true`、
`incremental_full_rebuild_equivalent=true`、event chain 有效，且 observation 与
显式 persist 前后 raw evidence digest 均保持不变。事件链校验改为沿 predecessor
指针重放，允许 deterministic staging 将多个事件固定在同一时间戳而不丢失拓扑。
增量 persist 现在在单一 SQLite savepoint 中完成规则、事件和 revision 写入；它会
把 `raw_evidence_before_digest`/`raw_evidence_after_digest` 绑定到 receipt，并在
full-rebuild 等价性或 raw-evidence 检查失败时整体回滚，避免留下半个 derived
revision。该原子性只保护 derived update，不授予 production lifecycle authority。
online observation 现在也把 causal fragment 与整条事件链放在同一 savepoint 中；
novelty/conflict/preview 的晚到异常会整体回滚，不能留下孤立的事件前缀或 causal
nodes/edges。已有外层事务时只释放 savepoint，由调用方负责最终提交。
该报告仍是 shadow/evaluation-only，`canonical_memory_mutation=none`、
`promotion_attempted=false`；重放入口为
[`evidence/tehm-online-evolution-rtl-b3-dev/reproduce.sh`](../evidence/tehm-online-evolution-rtl-b3-dev/reproduce.sh)。

### ORFS Batch-0 preflight（2026-08-26）

Batch-0 已完成新 scratch root 的真实 `gcd` pair smoke，并在 2 CPU 与默认 6 CPU
下复现同一诊断结果：`CORE_UTILIZATION 50→40` 使 route 从 GRT-0116 congestion
fail 变为 route/finish pass。但这两次旧 smoke 的 executor 实际落到了
`/opt/EDA4AI/OpenROAD-flow-scripts`，与 manifest 的 `/data1/zhangdy/Tools/`
source root 不一致，故只能作为 diagnostic；修复 ORFS_ROOT 绑定后，用 `/data1`
执行会暴露 OpenROAD binary/flow assertion mismatch，已 fail-closed 记录。
即使暂不计该 provenance 阻塞，u40 arm 的完整 signoff 仍为
`pass_with_caveats`（WNS `-0.573391 ns`、48 setup violations），因此 DEF graph
与 full-oracle eligibility 被严格 gate 拒绝；7 条 manifest lineage 当前
`eligible_positive=0`、`learner_eligible=0`。该结论和 canonical digest 不变性已
固化在
[`evidence/tehm-orfs-batch0-smoke-r2-dev/batch0_preflight_report.json`](../evidence/tehm-orfs-batch0-smoke-r2-dev/batch0_preflight_report.json)。
当前 run/signoff/graph 已显式绑定 manifest `ORFS_ROOT`；在提供匹配的
OpenROAD/Yosys toolchain、修正 timing/signoff contract 并由同一 pair 重新跑通
graph、observe、staging 之前，不扩大到完整 14-arm 批跑。

本轮新增 `preflight_orfs_toolchain()` 运行前门：只有 ORFS tree 内置的
OpenROAD/Yosys，或调用方显式设置的 `OPENROAD_EXE`/`YOSYS_EXE` 才能执行；缺失
时在 EDA stage 前 fail-closed，禁止 `_env.sh` 静默回退到宿主 PATH。receipt 会
绑定工具来源、路径、版本、SHA256 与 fingerprint；外部 override 标记为
`operator_bound_unverified`。成功 flow 的 `campaign-run-receipt.json` 必须携带
该绑定，旧的无绑定结果只能作为 diagnostic，不能成为 learner evidence。
另外，preflight 现在会对真实 Yosys 版本探测当前 ORFS
`read_liberty -unit_delay` 能力；已知不兼容版本在 synth 前直接 `blocked`，避免把
工具版本错误误记为设计失败或消耗完整 campaign 预算。
prepare 还会把每个 before/after project 的 `config.mk`、`constraint.sdc` 和有序
RTL 字节流写入 `input_bindings`。observe 时重新计算并逐项比对；任何 post-prepare
改动（包括为了 timing smoke 临时改 SDC）都会落为
`INCOMPLETE_EXTERNAL_ONLY`，即使后续 ORFS 报告全部为 clean，也不能进入 learner
或 staging。与此同时，manifest 会记录固定 `timing_contract`（当前 SDC 的
`clk_period` 与摘要）；缺失、重复或不一致的 timing target 也会 fail-closed。
为避免先运行后补 provenance，`prepare` 现在还要求 campaign 已经由 `--phase freeze`
生成 source-freeze manifest；缺失或路径失效会直接拒绝 batch preparation，不能以
`source_freeze_sha256=null` 绕过可复现性边界。
在同树打包工具链（OpenROAD `26Q3-1510-g6cb3f2b704`、Yosys `0.68`）下，SPI
`u50→u40` held-out pair 已完成全部 oracle（equivalence、strict signoff、PPA、DEF
graph），得到 1 条 `ELIGIBLE_POSITIVE` external receipt；由于 split=heldout，
`learner_eligible=0`，staging 实际导入 0 行，canonical digest 保持不变。JPEG
pair 同轮保留为负证据（before `CTS-0080 Sink not found`，after detailed-route
timeout）。详见
[`evidence/tehm-orfs-batch0-exact-r1/batch0_exact_pair_report.json`](../evidence/tehm-orfs-batch0-exact-r1/batch0_exact_pair_report.json)。

随后用同一打包 toolchain 完成了第一条 support pair：参数化 UART 的
`CORE_UTILIZATION 50→40` 在固定 `2.8 ns` contract 下两个 arm 均通过完整 ORFS、
equivalence、strict signoff、PPA 与 DEF graph，并安全导入 campaign-local staging。
该 pair 的物理 utility 为 `HARMFUL`（面积 `+3119.8 µm²`、功耗 `+0.000052 W`、WNS
`−0.127763 ns`），所以没有 canonical import 或 promotion。独立 authority receipt
从外部 observation chain 推导出 `obligation_coverage=PASS`、`cross_lineage_te=FAIL`、
`harmful_rate=FAIL`；`rollback_verified`、`registry_verified`、
`conformal_coverage` 仍为 `NOT_ESTABLISHED`，决定为 `DENY_CANONICAL_IMPORT`，
`promotion_attempted=false`；完整报告见
[`evidence/tehm-orfs-batch0-support-uart-r1/batch0_support_uart_report.json`](../evidence/tehm-orfs-batch0-support-uart-r1/batch0_support_uart_report.json)。
`build_orfs_authority_receipt.py` 的生产 CLI 不再接受 caller-supplied gate
booleans，而是从 observation receipts 推导可用测量；缺失测量保留为
`NOT_ESTABLISHED`，不会伪造实测 `False`。`build_receipt(..., gate_inputs=...)`
仅保留给旧 deterministic fixture replay。

在同一 source-freeze 与 exact toolchain 下又完成了第二条 source-disjoint support
lineage：参数化 UART 与 `uart_no_param` 各自独立跑 `CORE_UTILIZATION 50→40`，四个
arm 均通过完整 ORFS、equivalence、strict signoff、PPA 与 DEF graph，并各自导入
campaign-local staging。两条 lineage 的物理 utility 均为 `HARMFUL`（面积
`+3119.8 µm²`、功耗约 `+0.000052 W`、WNS `−0.127763 ns`）；因此这次只把
`cross_lineage_te` 提升为 `PASS`，`harmful_rate` 仍为 `FAIL`，
`rollback_verified`、`registry_verified`、`conformal_coverage` 仍为
`NOT_ESTABLISHED`，authority 继续 `DENY_CANONICAL_IMPORT`，canonical digest
保持不变。完整机器可读证据见
[`evidence/tehm-orfs-batch0-support-uart-dual-r1/dual_support_report.json`](../evidence/tehm-orfs-batch0-support-uart-dual-r1/dual_support_report.json)。

随后用这两条真实 pair 重放了 L2/L3 causal shadow；path 达到
`L3_REPLICATED_EFFECT`，并明确记录 2 个独立 design/lineage 与 2 个独立 run
witness。L3 gate 现在对缺失 run/design witness fail-closed，防止旧 transition
provenance 中 `unique_runs=[]` 仍被误判为 replicated effect；该 causal receipt
仍为 evaluation-only，不改变 rule 或 canonical authority，详见
[`causal_l3_replication_report.json`](../evidence/tehm-orfs-batch0-support-uart-dual-r1/causal_l3_replication_report.json)。

### L4 held-out causal transfer shadow（2026-08-26）

新增 `tehm.causal.evaluate_transfer_supported_mechanism()`：只有训练 path 已由
L3 controlled replication 重放通过，且显式 held-out transition 同时满足
`split=heldout`、`learner_eligible=false`、原始失败被移除后的 oracle PASS、无 regression、机制族/
typed action/profile/effect 匹配，并拥有与训练完全不重叠的 lineage/design witness，
才返回 `L4_TRANSFER_SUPPORTED_MECHANISM`。该评估器是只读 receipt，不修改 path、
canonical evidence、rule lifecycle 或 production retrieval；复用训练 transition、
跨 campaign 或损坏 fragment witness 均 fail-closed。当前测试用真实 ORFS adapter
构造的两条训练 lineage 与独立 held-out lineage 验证了该边界。

新增 `scripts/evaluate_causal_transfer.py` 作为冻结 DB 的 CLI 审计入口：数据库以
immutable/read-only 方式打开，报告记录输入 digest、transfer receipt 和
`database_unchanged=true`，且始终 `promotion_eligible=false`。ORFS 评估必须传入
`--require-full-oracle`；此时 before/after 两侧必须各自具备完整且精确的
Batch-0 14 项 checks（含 strict signoff、DEF graph、toolchain、input binding 与 timing
contract），仅有 `oracle_complete=true` 或手工缩减的 checks 集合不能满足 L4。
缺失 held-out receipt、非 fail→pass、full-oracle 不完整或 utility harmful 都只保留
为 evaluation/negative evidence，不会进入 learner、canonical memory 或 production。
本轮从 v4 快照重建了带 run witness 的 L3 path，并对独立 `shift32` held-out pair
执行 `require_full_oracle=true` 的重放；因两侧未具备精确 14 项 ORFS checks，结果明确为
`heldout_transfer_witness_failed`，机器可读负证据见
[`evidence/tehm-orfs-l4-transfer-r2/transfer_replay_report.json`](../evidence/tehm-orfs-l4-transfer-r2/transfer_replay_report.json)。

L4 receipt 现在还可以通过 `tehm.causal.record_causal_transfer()` 写入 additive
`tehm_causal_transfer_receipts` shadow ledger。ledger 绑定 path digest、训练/held-out
campaign、transition witness 和 `require_full_oracle`；`verify_causal_transfer()` 会
重新验证 path provenance 并重跑纯 evaluator。有效的负结果返回
`verified=true, eligible=false`，篡改、stale path 或 replay mismatch 则
fail-closed。该写入只产生可审计的派生 receipt，不改变 causal path、canonical
evidence、rule lifecycle 或 production policy。验证器还会校验 receipt 顶层投影
（`eligible`/`evidence_level`/`reason`/`transfer_receipt`）与签名 payload 一致，避免
只修改便捷字段却被误判为已验证。Capability authority 的 C6 held-out evidence
可选携带一个或多个 `causal_transfer_receipt_id(s)`；一旦提供，authority 会逐条
重放并要求 `verified=true`、L4 和 lineage 绑定，否则 C6 只留下不可晋级的审计尝试。
未携带该字段的旧 generic held-out fixture 仍保持兼容，但不应解释为已完成 causal
transfer ledger 绑定。

为累积真实 source-disjoint transfer，新增
`scripts/evaluate_causal_transfer_batch.py`。它在第一次评估前整体校验 case、唯一
transition 和唯一 lineage manifest，随后用 immutable source DB 逐案评估；失败案例
保留在分母，默认不写任何 DB。显式指定 `--ledger-db` 时，脚本先备份 source DB 到
新建的隔离库，再在一个外层事务中写入并逐条 replay 验证 shadow receipt；source DB
哈希必须保持不变。批量 `PASS` 只表示 transfer cohort 的 evaluation 状态，仍固定
`promotion_eligible=false`/`promotion_attempted=false`，但输出的 receipt IDs 可由
C6 authority 显式绑定。ORFS 调用必须同时使用 `--require-full-oracle`，因此 generic
fixture 的 PASS 不会伪装成完整 ORFS transfer。

Activation update 现在也会把反馈写入同一条 hash-chained event log：PASS 产生
`SUPPORT_INCREASED`，中性结果产生 `UTILITY_DRIFT`，FAIL/REGRESSION 产生
`RULE_HARMFUL`，payload 绑定 activation ID 与 utility 前后快照。evaluation/backend
反馈默认使用非 learner campaign；同一 activation ID 的重试不会重复累计 utility 或
事件。
utility 前后快照与对应 feedback event 现在在同一 savepoint 中原子更新；事件写入
失败会回滚 utility counter，避免出现“计数已增长但 provenance 缺失”的半提交状态。

Full rebuild 的 stale-rule retirement 现在在修改 lifecycle 前后计算
`raw-evidence-preservation-v1` fingerprint，覆盖 canonical states/transitions/
episodes/episode steps、dataset membership、experience edges 和 physical effects。
若 derived-memory maintenance 试图改写这些原始证据，crystallization 会
fail-closed；`retired` 仍只撤销 runtime authority，不删除 evidence。
`PhysicalEffectMemory.record()` 对同一 transition 采用 immutable replay：相同 payload
幂等返回，PPA/delta/provenance 冲突直接 fail-closed，不再使用 `INSERT OR REPLACE`
静默改写 raw physical evidence；图上下文只能通过明确的空值→已绑定 enrichment
路径补齐。

Capability registry 也保持同一 authority 边界：注册只能从 `observed_gap` 或
`candidate` 开始，不能用 `register_capability()` 伪造 `verified/promoted`；同一
capability evidence ID 只能幂等重放，若 split/verdict/lineage 改变则拒绝覆盖。
promotion 现在还必须消费由 `record_capability_authority()` 生成的数据库绑定
authority receipt：C1–C8（以及所需 asset gate）的 evidence rows、candidate policy
snapshot 和实际 runtime load receipt 都会在 promotion 前重新校验；C4 evidence
还必须携带与最新 load row 一致的 `execution_receipt_id`，因此单独写入
`loaded=true` 不能满足 C3/C4。调用方自报的布尔 gate 不能单独授予 capability
authority。DB attribution 在计算 C3 时也会校验 runtime load JSON 的内容 digest、
snapshot digest、runtime identity 和 `loaded` 字段；篡改 receipt 后即使 SQLite
`loaded=1` 仍会 fail-closed。

2026-08-27 又补齐了 registry 内容完整性重放：capability 定义、Asset 定义、policy
snapshot 和 runtime-load receipt 在每次复用时都会从数据库字段重算 content digest
及 content-addressed ID；`INSERT OR IGNORE` 遇到同一 ID 的冲突内容不再静默接受，
直接 SQL 篡改会被 registry/authority/loader 拒绝或转换为不可晋级结果。该 guard
不把 `status`、canonical evidence 或 production runtime 变成可写入口；新增 retention
ledger 的当前全套回归为 `493 passed`，仍保持 shadow/evaluation-only 的 promotion
边界。

Backend activation seam 现在也执行同一条 fail-closed 回执规则：`UNKNOWN` 只能被
一次性收敛为最终 outcome；已 finalized 的 outcome、created regressions、rollback
receipt 与 canonical transition linkage 只接受精确重放，冲突或缺失 transition
witness 直接拒绝，utility/event feedback 不会因重试重复累计。

Online causal seam 也不再接受调用方伪造的 lineage 或 learner eligibility：intervention
pair 的 lineage 必须由 canonical transition 推导，consolidation trigger 必须与
数据库 membership、transition 和 campaign 一致；同一 campaign 的 audit-only
membership 不能原地升级为 learner support，lineage 批量分配通过 savepoint 原子化。

Causal path 的 `ordered_nodes` / `ordered_edges` 现在使用 `causal-path-order-v1`
拓扑顺序（transition → state condition → action → effect → oracle outcome），并在
有数据库上下文的 replay/retrieval/replication 入口校验节点、边端点及顺序；重排或
缺失 witness 即从 shadow evaluator 中剔除，不影响 canonical evidence。路径 replay
还会重算 node/edge 的 canonical JSON、payload digest 与 content-addressed ID，确认
所有 transition owner、edge witness 和共同 training campaign 均完整覆盖；即使攻击
者同步重算 path digest，篡改后的派生 node/edge 也不能进入 causal evaluator。
L2 `evaluate_causal_rule_evidence()` 也复用同一条完整 replay firewall，因而不会仅凭
内存中的 evidence level 或全局 edge 交集接受损坏的路径。

Rule retrieval loader 现在也执行同一条内容完整性边界：加载时严格解析
`before/after/hard_preconditions/context/validity/confidence/utility/risk` 字段，
并用不可变定义重算 `rule_id`。坏 JSON、错误类型、profile 冲突或定义 digest
不匹配的 rule 会被记录在 `RuleIndex.rejected` 但不会进入任何 recall/activation
索引；hard preconditions 与 context predicates 不再因字段遗漏被默认为空。这样
runtime 只能消费完整、内容寻址且生命周期已授权的规则。

RTL oracle 的 obligation coverage 也改为逐项 fail-closed 重放：
`RTL_TARGET_TEST_PASS`、`RTL_FROZEN_REGRESSION_PASS` 和 `RTL_COMPILE_PASS` 只在
对应 test/compile arm 实际产生结果时计入 checked；缺失 target 或 frozen regression
不会被另一侧的结果掩盖。partial run 会返回真实的 `oracle_type`/confidence tier、
`oracle_complete=false`，且 target 通过但 regression 未运行不再伪造
`created_regressions`。因此 target-only 仍可作为 T-tier 执行反馈，但不能满足完整
RTL verifier 或 promotion gate；只有两侧都实际运行才可得到完整 R-tier coverage。

Online evolution 的事件与 revision receipt 也已补上 content-bound replay guard：
事件写入前会重放并校验当前 campaign hash-chain、`event_digest` 与
content-addressed `event_id`；链或事件内容被直接篡改时，后续 append/replay 会
fail-closed。`tehm_rule_revisions` 对相同 revision ID 只接受完全相同的 validation
payload，冲突内容不会被 `INSERT OR IGNORE` 静默吞掉。该约束仍只保护 shadow/derived
evolution provenance，不授予 rule lifecycle 或 production authority。

Causal shadow objects 现在也采用 immutable replay：node、edge 与 intervention pair
在相同 ID 重放时必须逐字段一致，path 会重算 `path_digest` 并拒绝损坏的
source/support/evidence JSON；causal recall 遇到 digest 不匹配的 derived path 会
直接跳过，L3 replication 只有在完整校验后才更新版本化 digest。该 guard 只强化
causal shadow 的可重放性，不把 causal score 变成 production authority。

2026-08-27 又把 A3 causal recall 的质量重排接到同一条 evaluation-only 管线：每条
匹配回执同时暴露 `mechanism_score`、`utility_score`、`risk_penalty` 与
`quality_status`，并按 `S_causal × U × (1-R)` 计算可解释 shadow score。若 path 没有
自带质量字段，evaluator 会优先从其 source transitions 的 canonical
`observation_delta.utility_verdict` 与 regression witness 派生质量，并记录
`quality_source` 及具体 transition IDs；这避免把 path 外部注入的分数当成 canonical
事实。canonical 质量证据不完整时仍使用保守的
`U=0.5`、`R=0.5` 并标记
`NOT_ESTABLISHED`；显式质量字段损坏、越界或非有限时直接从 evaluator 剔除，不能
被默认当成安全证据。该层只影响 causal shadow 排序和审计字段，不写 canonical
memory、不进入 production retrieval，也不改变任何 promotion gate。

Typed view 与 activation receipt 也已改为 immutable replay：相同 owner/schema/extractor
的 view 只接受完全一致的 payload、digest 与 source refs，冲突 materialization 会
fail-closed；相同 activation ID 只接受完全一致的检索、绑定、义务转移和验证结果，
不会再由 `INSERT OR REPLACE` 静默覆盖历史 receipt。ORFS trial 的 verifier/rollback
reconciliation 仍通过单独的显式更新路径执行，不改变 canonical evidence 或
production-only authority 边界。

Dataset membership 也有同一条硬约束：只有 training split 可以标记
`learner_eligible`；capture/assignment 会拒绝非 training 的显式 opt-in，直接写入的
矛盾行会被 crystallization、causal、gap、conflict 和 online trigger 排除，同时由
honesty 审计报告为防火墙违规。

ORFS causal shadow/controlled-replication builder 进一步只接受 `training` split；
held-out、calibration 和 A/B 证据必须留在 audit staging，并通过 L4 transfer evaluator
重放，不能借参数把非 training row 合并进 learner path。

新增五类独立 RTL cluster（handshake completion、credit/obligation recovery、reset
semantic loss、width correction、overlap-priority protocol/IP）的冻结 M1/M8 A/B 报告位于
[`evidence/tehm-procedural-ab-v4-dev`](../evidence/tehm-procedural-ab-v4-dev)：
M1 `0/5`、M8 `5/5`，每个 task 运行 3 次（15 次 M8 Icarus execution），五个 cluster
的真实 Icarus obligation coverage 均为 `1.0`，M8 harmful activation rate 为 `0`，
canonical SQLite digest 前后不变。RTL rule lookup 已绑定显式
`(transformation_family, compatibility_profile)`，因此 overlap-priority 不再依靠
额外同簇样本弥补结构不兼容。该轮仍是小样本 development evidence，不是普适
benchmark 或 production promotion 依据。

### Parametric Shadow RFC（当前工程边界）

v3 的证据门槛已经满足，但 Parametric View 仍保持 `NOT_IMPLEMENTED`。当前只
实现只读的 `tehm.parametric.shadow.build_shadow_proposal`：它绑定 readiness、
platform/family/tier calibration policy、freeze replay receipt 和 graph context，输出
带 digest、距离、uncertainty、lineage 与 abstain reason 的 shadow receipt；不
写 canonical memory、不进入 runtime retrieval、不改变 lifecycle 或 promotion。
完整契约见 [`docs/Parametric_Shadow_RFC.md`](docs/Parametric_Shadow_RFC.md)。
独立 campaign 的 receipt/outcome/join/report 流程也在该 RFC 中定义；其默认工作根
为 `/tmp/tehm-parametric-shadow`，不改变 `memory/tehm.sqlite`。

这条边界是有意的：Parametric 输出是连续 knob 的预测建议，不是带真实
transition/verifier/obligation/provenance 的 executable rule。写入 canonical 会把
预测反馈成 learner support，污染 held-out 与 lineage 统计；直接进入 production
则会绕过 applicability、typed action、rollback 和 registry authority。后续只有在
精确 `platform|family|dataset_tier|action_signature` 分区中通过独立 conformal/
harmful-rate/Pareto 证据，再叠加真实 A/B 的 rollback、registry、obligation 与
cross-lineage TE，才可进入 candidate；promotion 由六项门控合取，shadow proposal
本身不具备 authority。

### ORFS Batch-0 experience lane（2026-08-24）

Batch lane 已实现为独立执行面：
[`scripts/run_orfs_batch0.py`](scripts/run_orfs_batch0.py) 只允许
`<campaign-root>/staging` 下的 SQLite/artifacts，输出 hash-chained external
observations；canonical import 只能通过独立的
[`scripts/promote_orfs_batch_observations.py`](scripts/promote_orfs_batch_observations.py)
并提供完整 authority receipt。batch runner 本身没有 promotion phase。
canonical import 还会拒绝 authority 选择未知 case、空选择，或
`split/classification/learner_eligible` 不一致的 observation，避免 held-out/空批次
借由 authority payload 伪装成 learner 写入。

support receipt 的 staging import 与 authority-gated canonical import 也采用单一
caller-safe savepoint：一批记录中的 canonical capture、typed views 与 physical
effect row 只有在整批成功后才提交；晚到的 malformed receipt 或 physical write
异常会回滚整批，不能留下部分 learner projection。capture 前已创建但最终无引用的
content-addressed artifact 仍只会成为可审计 orphan，不会被 canonical row 采用。

首轮 `/tmp/tehm-orfs/orfs-batch0-v1` 固定 sky130hs、7 个单时钟 RTL lineage、
`DENSITY_RELIEF/CORE_UTILIZATION 50→40`，按 support/calibration/heldout=`4/2/1`
隔离。当前冻结累计 34 个 bounded attempt；每个项目的 latest outcome 为 12 个
`SUCCESS`、1 个真实 routing-congestion `FLOW_FAILURE`（AES u50）和 1 个冻结
3600 秒 `TIMEOUT`（AES u40）。ORFS 的 Slang 配置通过只读 synth bridge 接入支持
`read_slang` 的 Yosys，未修改冻结 RTL 或系统 ORFS 安装；配置不改 RTL的 7 个 pair
均由独立 cryptographic source-identity proof 证明，若 RTL 字节变化仍必须走实际
Yosys/formal proof。

本轮绑定 Magic `8.3.677`、Netgen `1.5.323` 和完整 sky130hs transistor CDL，完成
GCD strict-signoff smoke 后再依次解封 support、calibration 和 heldout。最终 8 个
ORFS project 同时通过 full GDS DRC、Netgen LVS、OpenRCX、timing、artifact/run
binding 和 graph gate，对应 GCD、UART、JPEG、SPI 四个 before/after pair；其中
SPI 是冻结 support 后才运行的独立 heldout。AES 因 flow failure/timeout 不完整，
Ibex 因 strict DRC 不完整，RISC-V 虽 DRC/LVS/RCX clean，但两臂 setup WNS 为负，
因此 strict gate 均 fail closed。

7 条 hash-chained external observation 的最终分类为 4 个 `ELIGIBLE_POSITIVE`、
3 个 `INCOMPLETE_EXTERNAL_ONLY`；只有 support split 的 GCD/JPEG 两条
`learner_eligible=true` 并进入 staging。calibration UART 和 heldout SPI 即使为正也
不回灌 learner。staging 为 2 transitions / 2 physical effects，canonical snapshot
仍为 9 transitions / 0 physical effects，SQLite SHA-256
`bd64290d6bdf4db59376325ca38b781b7c98b51dc12b3fb10377eb3c1d8ac89f`，
`canonical_memory_mutation=none`。

这里的 `ELIGIBLE_POSITIVE` 只表示 before/after 的全 oracle、obligation 和证据绑定
完整，**不表示 Pareto utility 为正**。两条 support effect 的平均 ΔWNS 为
`+0.109501 ns`，但平均 Δarea=`+4473.5 um²`、Δpower=`+0.017345 W`，profile 会把
area/power 标为 harmful；heldout SPI 更是 ΔWNS=`-0.016755 ns`、
Δarea=`+57 um²`、Δpower=`+0.000070891 W`。因此该批证明了可审计经验采集与
action effect 的非单调性，没有证明 `CORE_UTILIZATION 50→40` 是可直接执行的安全
策略。staging 的两条 physical effect 已绑定 2 个不同的真实 strict-clean graph
context；空 `{}` 不再被重复导入误记为 graph support。

因此当前结论是：已经具备继续进行**受控 Batch0/Batch1-prep 全 ORFS 经验积累**的
工程能力，但还没有 production promotion authority。下一轮应冻结新的 lineage 和
action signature，优先扩充独立 support/calibration、把 AES timeout 与 Ibex DRC/
RISC-V timing 作为分层失败标签，而不是调低门槛。只有独立真实 A/B 同时满足
rollback、registry、obligation coverage、cross-lineage TE、harmful rate 和
conformal coverage 六门，才可由独立 authority 执行 canonical import；batch runner
与 Parametric shadow 仍无该权限。

### V4 负面基线与 typed utility contract

V4 已持久化到 `evidence/tehm-authority-v1/v4/`，其中 broad
`DENSITY_RELIEF / CORE_UTILIZATION 50→40` 保留为不可晋升的负面基线：8 条
full-oracle lineage、raw Pareto harmful rate=`7/8`、authority decision 为
`DENY_CANONICAL_IMPORT`，canonical SHA 前后不变。历史的非支持 scope
`orfs:sky130hs:route` 不在下一次 authority staging registry 中；支持的
`route` 仍为 candidate v2。

下一阶段的 contract 定义位于
`tehm/physical/utility_contracts.py`，contract id 为
`TIMING_RELIEF_BUDGETED_V1`。它保留 raw Pareto verdict，另加 WNS、TNS、area
和 power 的预注册边界；`select_contract_proposal` 只返回 `PROPOSED` 或
`ABSTAINED`，要求 action binding、ready calibration、完整 hard oracle、OOD
ceiling 和所有预测区间同时满足，且永远返回
`canonical_memory_mutation=none`、`promotion_eligible=false`。

当前 8 条样本上的 contract 评分仅为 retrospective design evidence（4/8
通过、4/8 拒绝），不构成 promotion 验证。prospective manifest 已冻结为 2
条 calibration 加 4 条 held-out A/B；必须先由 selector 决定 proposal/abstain，
再执行新的 ORFS cohort。

该执行顺序已经由
`memory/scripts/run_timing_relief_selector_preflight.py` 实际验证：6 条新
lineage 先完成 50% baseline，随后 selector 得到 `PROPOSED=0`、`ABSTAINED=6`，
没有任何 40% after arm 被 materialize。由于 proposal coverage=`0.0` 低于冻结的
`0.5` 门槛，`TIMING_RELIEF_BUDGETED_V1` 按停止条件关闭；不能通过扩大 interval
或复用 broad rule 继续运行。完整 preflight 证据在
`evidence/tehm-authority-v1/v4/prospective-selector-preflight-v1/`，下一动作必须
重新注册独立 action signature。

### V2 独立 action signature（50→45）

下一轮已注册独立的 `TIMING_RELIEF_BUDGETED_V2_50_TO_45`（digest
`f9b5824716868e3bd247c97ff47e75230bdff88984b6845e5db01e0239048863`），并用 4 条
support + 3 条源隔离 calibration 建立只读物理策略。support 的 4 条 A/B 均完成
ORFS、equivalence、strict signoff 和 graph，contract 仅 `1/4` 通过且 raw Pareto
harmful=`4/4`；calibration 的 contract 为 `0/3`，raw Pareto harmful=`3/3`，但
经过数值稳定的闭区间判定后 split-conformal policy 达到 empirical/per-metric
coverage=`1.0`，仍保持宽 uncertainty interval。

随后对全新 6 条 RTL（2 calibration + 4 held-out）先跑 50% baseline，再执行
action-bound selector；结果 `PROPOSED=0`、`ABSTAINED=6`、after materialized=`0`，
proposal coverage=`0.0 < 0.5`，停止状态为
`STOP_50_TO_45_LOW_PROPOSAL_COVERAGE`。3 条 baseline 因 strict signoff/graph 不完整
直接 fail-closed，另外 3 条的 WNS/power 区间无法同时满足 contract。V2 因此也关闭
为 shadow-only；没有 selected after ORFS、cross-lineage TE 或 promotion gate，
canonical snapshot 仍为 9 transitions / 0 physical effects。证据分别位于
`evidence/tehm-authority-v1/v4/next-action-v2-support-50to45/`、
`next-action-v2-calibration-50to45/` 和 `prospective-selector-preflight-v2/`。

这说明当前卡点不是“缺少更多 ORFS 命令”，而是两个独立 action signature 都在
selector-before-execution 阶段没有足够的安全提案覆盖。下一步应注册新的、语义更
窄且可解释的 action（或扩充源隔离 support/calibration 以缩窄 interval），不能放宽
OOD/coverage/utility contract，也不能把 calibration 或 shadow outcome 写回
canonical memory。

### 下一阶段工程状态（P0–P3）

- 当前工作树已提交完成 P0 的 schema v3/migration、dataset membership
  firewall、episode-owned witness、utility-preserving re-crystallization、
  stale-rule retirement、obligation evidence finalization，以及 production
  仅允许 `promoted` rule；P1 已把 RTL parser graph 接入 canonical state，并在
  binding receipt 中记录 target-context digest 和每个 hole 的来源证明。已用
  fresh DB、migration、capture/re-crystallize、RTL/Icarus 和 activation smoke
  checks 验证；当前环境未安装 pytest，因此本轮以 dependency-free compileall、v4
  freeze reproduce 和定向 preflight smoke 作为可复核证据；最终源码绑定的
  development freeze 已随本轮源码变化重新生成，不沿用历史 v3 数字。

- 历史 P0 基线已封存于 tag `tehm-p0-baseline-20260817-postaudit`（提交
  `86178eb`）；这不是当前工作树的提交，也不覆盖本轮未提交的完整性修复。
  v3 refresh 仍是历史 canonical bundle，后续 campaign 必须从明确的 freeze 指针
  派生，并把可重建 ORFS RUN/logs/results/objects 留在 `/tmp`。
- P1 已实现：独立 receipt/log/outcome/join/report，源码提交 `9b841a1`；shadow
  记录不能写 canonical memory 或授予 promotion authority。
- 当前 v4 development A/B 已覆盖五个独立 held-out cluster：M1=`0/5`、M8=`5/5`，
  每 task 3 repeats；四类 parser-backed action family（另含
  `PRIORITY_REORDER`）均有
  真实 Icarus target/regression evidence、binding proof、obligation coverage=1.0，
  harmful activation=0，canonical SQLite 不变。对应报告在
  `evidence/tehm-procedural-ab-v4-dev/`；该样本仍不是 benchmark。
- leave-one-cluster-out harness 已落地于
  `scripts/run_procedural_loco_v1.py`，报告在
- `evidence/tehm-procedural-loco-v1/`。当前 5 folds 中 M8=`5/5`；每个 fold 都保留
  held-out source exclusion、显式 compatibility profile 与 obligation evidence。
  这仍不能把 development replay 扩展成普适泛化结论。
- Phase-10 RTL campaign 的历史 receipt 生成过 Section-13 funnel：真实 Icarus
  external A/B trial 的 `RC_ret→AY→BSR→IVR→RU=1`、`OC=1`、`HAR=0`、`TE=1`，且
  3/3 activation rollback receipt 与 registry authority 均 verified；不再使用 fake
  evaluator 作为主证据。当前 smoke runner 还会显式补齐缺失的 PPA/conformal gate
  为失败值，因此只记录 candidate，不把该小样本 funnel 当作 production promotion。
- P2 已实现入口门控：`prepare_parametric_prospective_manifest.py` 强制 future
  lineage 与 training/calibration/held-out/A-B firewall 不相交，并要求 decision
  target 至少有两个候选 action；已执行一轮独立 observation pilot（见下方），
  但尚未进入 decision round。现在 decision case 还必须提供已完成的
  `shadow_metrics.json`；`validate_observation_gate` 会 fail-closed 检查
  proposal/outcome/obligation coverage、OOD ceiling、harmful rate 和预注册的
  physical interval coverage。
- P3 已物化四个独立 future-lineage RTL fixture，并由
  `scripts/run_procedural_ablation.py` 使用真实 Icarus 完成逐 arm replay。Role View、
  Predicate View、Validity Gate、Obligation Transfer 四个对照均观察到预注册的
  component contrast；M8 的 harmful activation rate 为 0，canonical counters
  前后不变。报告位于 `/data1/zhangdy/tehm-campaigns/tehm-procedural-ablation-v1/`。
  该轮 acceptance 仍为 false：当前 canonical 只有 1 条 `VALIDATED` rule，低于
  manifest 要求的 2 条；不能把组件对照结果扩展成普适 benchmark。
- 当前 canonical snapshot 的只读 procedural audit 为 9 个 effect groups、8 个
  non-singleton groups、2 条 rules（group→rule conversion `0.25`）。独立
  `rule-growth` replay 已在 canonical 副本上完成，不能把副本结果当成 canonical
  promotion。
- P3 rule-growth replay 已使用真实 Icarus 捕获 `valid_ready_bug` 与 `fifo_space_bug`：
  隔离副本从 2 条增长到 8 条 rules，其中 7 条为 cross-lineage `VALIDATED`；canonical
  仍保持 2 rules / 116 transitions，promotion authority=`NOT_AUTHORIZED`。报告位于
  `/data1/zhangdy/tehm-campaigns/tehm-procedural-rule-growth-v1/`，后续须用更多
  独立 lineage 和 leave-one-lineage-out stability 复核后才能考虑进入 lifecycle。
- P3 growth + runtime ablation v2 已加入两条 prospective `RESET_RESTORE` AST_REWRITE
  lineage，并用第三条独立 reset lineage 做 rule-growth training；隔离 staging 中
  执行真实 Icarus 逐 arm replay：validated rules=`7`、
  cross-lineage support=`8`、Rule Coverage=`0.5`、VCG=`0.5`、harmful activation rate=`0`；
  validity 与 obligation 两个对照可辨识，预注册 acceptance=`true`。RESET rule 只以
  `candidate` 状态提供给隔离 runtime evaluator；没有在 staging 或 canonical 中写入
  `promoted`，因为该实验没有完整六项 production promotion gates；报告位于
  `/data1/zhangdy/tehm-campaigns/tehm-procedural-growth-ablation-v2/`。
- P3 leave-one-lineage-out stability replay 已完成：全量隔离副本 7 条
  `VALIDATED` rules；去掉 `fifo_space_bug` 后保留 7/7，去掉 `valid_ready_bug` 后保留
  6/7，最小 validated-rule retention=`0.857`。这是稳定性证据，不是 runtime Rule
  Coverage/VCG 或 promotion 证据；报告位于
  `/data1/zhangdy/tehm-campaigns/tehm-procedural-rule-stability-v1/`。
- P3 mechanism-family v3 已把 role、predicate、validity、obligation 四类 fixture 与
  两个既有 RTL lineage 合并为六谱系隔离 replay：rule-growth 得到 8 个 profile、7
  个 cross-lineage `VALIDATED` rules、8 个 non-singleton effect groups；leave-one-
  lineage-out 最小 retention=`0.857`。在临时 enrolled staging 中，四个 component
  contrast 均可辨识，harmful activation rate=`0`，但 runtime Rule Coverage/VCG 仅为
  `0.25/0.25`（没有超过 reset v2 的 `0.5/0.5`），所以该结果只扩大机制区分证据，
  不授予 production lifecycle authority。紧凑报告位于
  `/data1/zhangdy/tehm-campaigns/tehm-procedural-rule-growth-v3/`，promotion
  receipt 明确 `canonical_memory_mutation=none`、`promotion_eligible=false`。
- P3 mechanism-family v2 cohort 在 v3 基础上加入两个独立的正向
  role/predicate/validity-compatible lineage，并把 acceptance 预注册为
  Rule Coverage/VCG `>=0.5`。六任务真实 Icarus replay 达到 validated rules=`7`、
  cross-lineage support=`8`、non-singleton groups=`8`、Rule Coverage/VCG=`0.5/0.5`、
  harmful activation rate=`0`，四个 component contrast 仍全部可辨识；cluster-level
  M8 rate=`0.4`，所以仍是有限 cohort 的 staging evidence，不是 production promotion。
  紧凑证据位于 `/data1/zhangdy/tehm-campaigns/tehm-procedural-mechanism-ablation-v2/`。
- P2 calibration supplement 已在独立 `/tmp` ORFS scratch 中完成：新
  `future-parametric-v5` RTL lineage 在 sky130hs/IHP 各有一个真实 base→density pair，
  与既有只读 held-out samples 合并后 9 个 retrieval policy 全部 `ready`；校准 DB
  的 physical memory count 保持 `114 → 114`，没有写入 canonical v3。小型 durable
  证据位于 `/data1/zhangdy/tehm-campaigns/tehm-p2-future-v5-physical/`。这只证明
  calibration gate 可在不放宽硬 OOD ceiling 的前提下恢复，尚未证明 Parametric View
  已实现，也不能直接把旧 observation pilot 晋级为 decision round；必须用新 policy
  重新生成 observation receipts 并通过预注册的 coverage/interval/obligation gate。
- P2 v8/v9 prospective observation 已按新 calibration policy 完成真实 sky130hs
  ORFS route/PPA join：2/2 proposal、2/2 outcome、obligation coverage=1.0，OOD
  distance=0.430214，canonical 六类 counters 零变化；但 harmful rate=1.0（阈值≤0.1）
  且 WNS interval coverage=0/2（要求≥0.8），所以 decision gate 保持失败，不能执行
  candidate ranking 或 Parametric View promotion。证据位于
  `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v89/`；下一步是扩充独立
  calibration/support 或改进物理 effect model，再重新预注册 cohort，不能调低门槛。
- shadow predictor 现已可选绑定候选 action 的 domain/family/config-edit keys；缺失
  transition action provenance 时 fail-closed，不回退到 family-wide profile。对 v8/v9
  的 action-conditioned 重放选中了同一 evidence pool，预测和门禁失败均未改变，故这
  是 provenance 完整性改进而非效果改进。诊断证据位于
  `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v89-action-conditioned/`。
- 当前 P3 还补上了 `typed_action_signature` 的 shadow-only 结构：除 exact
  config-edit key/value 外，候选 action 可声明单一 knob、direction、finite
  relative change 与 operation point；字段不完整即拒绝，避免把数值插值误当成已有
  支持。校准器可显式选择 `split_conformal_residual_v1`，用独立 held-out residual
  的保守 order statistic 形成 interval；默认旧 policy 仍使用
  `normal_weighted_mean_v1`，因此没有偷偷改写历史证据或放宽任何 gate。
- P2 fresh calibration v10/v11 已完成独立 physical firewall 与真实 ORFS 尝试：4 条
  fresh lineages 中 2 条 sky130hs 样本可评估，2 条 sky130hd 运行因 TritonRoute
  SIGABRT/route congestion 作为 infrastructure failure 保留，未转成 physical
  positive。合并旧样本后的 `sky130hs|DENSITY_RELIEF|research` policy 为
  `coverage_failed`（18/24=`0.75` < `0.80`），distance max=`0.430214`，canonical
  physical count 保持 `114 → 114`；证据位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-fresh-calibration-v10v11/`。
- P2 fresh prospective observation v12/v13 已按该 policy 真实完成 2/2 ORFS outcome
  join，但两条 proposal 均因 `calibration_policy_not_ready` abstain，proposal
  coverage=`0`、obligation coverage min=`0.333333`；decision gate fail-closed，
  decision prepare 以 rc=`2` 拒绝，未执行 candidate ranking 或 promotion。证据位于
  `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v12v13/`。
- P2 calibration expansion v14–v23 已在 `/tmp` 完成 10 条真实 sky130hs
  base→CORE_UTILIZATION=22 pair；v18–v20 作为 calibration held-out 时 policy
  aggregate coverage=`0.916667`、max distance=`0.117634`，staging physical
  count=`122`，canonical v3 未变。该 policy 的 area 单指标 coverage 仍为 `2/3`，
  所以不能把 `ready` 标签等同于 decision gate 通过。独立 v21–v23 observation
  已完成 3/3 receipt、3/3 outcome join：2 条因 OOD abstain、1 条 proposed，
  proposal coverage=`0.333333`、harmful rate=`1.0`、obligation coverage
  min=`0.333333`；decision prepare 按预注册门槛以 rc=`2` 拒绝。compact evidence
  位于 `/data1/zhangdy/tehm-campaigns/tehm-p2-calibration-expansion-v14v23/`
  与 `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v21v23/`，没有复制
  ORFS RUN/logs/results/objects，Parametric View 仍为 `NOT_IMPLEMENTED`。
- P2 fresh observation v30–v32 使用三条全新 sky130hs lineage，3/3 outcome join；
  仅 v30 被 proposal，v31/v32 因 OOD abstain，proposal coverage=`0.333333`、
  harmful rate=`1.0`、obligation coverage min=`0.333333`，WNS interval coverage=`0/1`，
  decision prepare 仍以 rc=`2` fail-closed。证据位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-calibration-expansion-v30v32/` 与
  `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v30v32/`，canonical v3 未变。
- P2 action-conditioned calibration v33–v38 先用 v33–v35 的 `CORE_UTILIZATION=40`
  作为 support，再在 v36–v38 held-out 上评估；将 numeric edit value 纳入 action
  signature 后，policy coverage=`0.416667`，status=`coverage_failed`，因此没有生成
  shadow/decision receipt。该结果证明不同 knob value 不能共用同一 empirical effect
  pool；紧凑 evidence 位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-action40-calibration-v33v38/`。
- P2 action-40 follow-up v39–v44 使用 6 条全新、独立 sky130hs lineage；6/6
  ORFS base→`CORE_UTILIZATION=40` pair 可评估。以 v33–v38 作为 staging support 后，
  exact-signature policy 的 aggregate interval coverage=`0.583333`，其中 area=`0.667`、
  power=`0.167`、TNS=`1.0`、WNS=`0.5`，仍为 `coverage_failed`，未生成 shadow/decision
  receipt。compact evidence 位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-action40-calibration-v39v44/`；ORFS RUN 树
  保留在 `/tmp/tehm-p2-calibration-expansion-v39v44`，canonical 未变。
- P2 action-40 calibration v45–v50 再使用 6 条完全新的 lineage；6/6 pair 可评估，
  以 v33–v44 为 staging support 时 policy aggregate coverage=`0.833333`、
  `status=ready`，distance max=`0.1677005`。area/power 单指标 coverage 仍为
  `0.667/0.667`，所以它只可用于 observation，不能直接作为 decision gate 通过。
  staging snapshot digest=`2bfaa913beecd4b0284711ed310fc1cd050b45c51c832c3bb81235b1d744b12b`。
- P2 v51–v56 observation 使用与 v45–v50 完全 disjoint 的 6 条 lineage，并绑定同一
  staging snapshot digest；6/6 outcome join，4/6 proposal，2/6 因
  `prediction_uncertainty_above_threshold` abstain。proposal coverage=`0.666667`、
  harmful rate=`0.5`、obligation min=`0.333333`、area interval coverage=`0.75`，
  decision gate 仍 fail-closed。compact evidence 位于
  `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v51v56/`，Parametric View
  仍为 `NOT_IMPLEMENTED`。
- P2 v57–v62 full-oracle follow-up 使用 6 条全新、与既有 calibration/held-out
  完全 disjoint 的 sky130hs lineage。12/12 before/after project 均完成 strict
  signoff/timing oracle，dirty strict 结果按事实保留，没有被转成 physical positive。
  action-40 policy 的 aggregate coverage=`0.708333`，area/power/TNS/WNS=
  `0.667/0.667/1.0/0.5`，因此仍为 `coverage_failed`，没有生成 observation/decision
  receipt。compact evidence 位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-action40-calibration-v57v62/`。
- P2 action-40 calibration v63–v68 使用 6 条新的 held-out sky130hs lineage；
  12/12 before/after project 均完成 strict signoff/timing oracle，policy
  aggregate coverage=`0.916667`，area/power/TNS/WNS=`1.0/1.0/1.0/0.667`，
  max distance=`0.168773`。该 policy 仅作为 observation 支持，staging
  physical snapshot digest=`76de1868543f19259a10be71e6d4d85508bf921a980d22737c4ca74e4f7f15d2`，
  canonical v3 未变；由于 WNS 单指标仍未达到预注册 coverage，不得直接进入 decision。
- P2 prospective shadow v69–v74 使用与 calibration 完全 disjoint 的 6 条 future
  lineage，并以正确的 v3 verifier replay receipt 重跑 observation：6/6 proposal
  receipts、6/6 ORFS outcomes join、obligation coverage=`1.0`、OOD max=`0.051424`；
  但 proposal coverage=`0.666667`、harmful rate=`0.75`、area/power/TNS/WNS
  interval coverage=`0.75/1.0/1.0/0.25`，故 decision gate 继续 fail-closed，未执行
  ranking、activation 或 promotion。紧凑 evidence 位于
  `/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v69v74/`；ORFS RUN 树仍在
  `/tmp/tehm-p2-prospective-v69v74`；staging counters=`116/142/2/190/144/606` 前后不变，
  canonical v3 counters=`116/114/2/190/116/606` 未变。
- P2 action-40 calibration v75–v80 是又一批与既有 calibration/held-out 完全
  disjoint 的 6 条 sky130hs lineage。12/12 before/after 项目均完成 strict-signoff
  与 timing report；strict dirty 结果按事实保留，timing reports 均为 clean，未把
  signoff failure 转成 physical positive。exact-signature policy 的 aggregate
  interval coverage=`0.708333`，area/power/TNS/WNS=`0.5/0.667/1.0/0.667`，
  observed distance range=`0.021573..0.209407`，因此仍为 `coverage_failed`，没有
  生成 shadow/decision receipt。staging physical count=`130`，snapshot digest=
  `14af2b4aa038a92cca92750833da19e883b50b71151fc1edec7c296d5c4f7f58`；canonical
  v3 未变。紧凑 evidence 位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-action40-calibration-v75v80/`，可再用的
  v81–v86 fixtures 仅作为预注册后续 cohort，尚未运行或计入 calibration。
- P2 action-policy binding 已补齐：`calibrate_retrieval` 现在把完整 action
  signature（domain/family/config-edit keys/normalized values）写入 policy；held-out
  cohort 若混合 action value、部分缺失 provenance 或 action 非法则直接
  `firewall_failed`。`PhysicalEffectMemory.predict` 在 policy/query signature 不一致、
  或 action-conditioned query 使用未绑定 policy 时 fail-closed，避免校准阈值跨 knob
  value 借用。当前工作树完整回归为 `647 passed`；canonical v3 仍固定为 `225`。
- Calibration policy v0.2 进一步把每个 required physical metric 的 interval
  coverage 绑定到预注册 target（默认与 aggregate target 相同）；aggregate 达标但
  单项 metric 不达标时也会输出 `coverage_failed`，`PhysicalEffectMemory.predict`
  对任何非-`ready` policy 统一 `heldout_calibration_not_ready` abstain。这个门只
  收紧 future campaign 的 promotion/readiness 语义，不回写历史 evidence，也不放宽
  OOD/uncertainty ceiling。
- calibration expansion runner 现在提供显式 `--strict-oracle` 阶段：对每个
  before/after project 运行 strict signoff，并单独生成 `timing_check.json`；该阶段
  只写 campaign reports，不调用 TEHM capture/crystallize/lifecycle。
- Shadow receipt 现在额外绑定 calibration staging memory snapshot digest；case 指向
  错误 DB 会在 prepare 阶段直接拒绝，而不是产生一批无意义的
  `no_action_compatible_contexts` metrics。
- Calibration report 现在额外输出按 nearest-distance 的
  `selective_risk_coverage` 曲线（样本 coverage、metric interval coverage、risk），
  仅作为诊断，不会放宽 hard OOD 或覆盖 gate；v39–v44 曲线显示全量保留时 risk
  为 `0.416667`，因此当前失败来自真实 coverage/分布失配，而不是缺少报告字段。
- `calibrate_exact_groups()` 的 `ready_for_shadow` 报告现在只能通过严格的
  `materialize_shadow_policy()` 变成 predictor 可读的 `status=ready` policy：必须是
  单一 exact action group，并重新通过 lineage firewall、per-metric conformal、
  harmful-rate 与 positive-utility gate。输出固定为
  `policy_kind=lineage_grouped_shadow`、`shadow_only=true`、`promotion_eligible=false`、
  `canonical_memory_mutation=none`；calibration runner 的可选
  `--shadow-policy-output` 只导出外部 shadow policy，不触碰 SQLite/canonical。
  v63–v68 positive cohort 已完成只读 predictor recheck；旧 v69–v74 fixtures 因
  `tehm-v2` scratch DB 与当前 `tehm-v4` reader schema 不一致而 fail-closed，需迁移或
  重建后再 replay。
- 新增 `scripts/migrate_tehm_snapshot_v4.py`：以 SQLite backup 复制旧快照、应用正式
  v1→v4 migration chain，并逐表比较已有 canonical rows 的 count/digest；源库拒绝
  原地修改，输出报告固定 `replay_required=true`。对迁移后的 v69–v74 输入，当前
  integrity replay 仍发现继承的 H1/H7 问题，shadow prepare 六条全部以
  `replay_not_verified` abstain，且 canonical counters 不变；这确认了 schema migration
  不是 evidence repair，下一步必须重建完整 v4 staging/verification fixture。
- `run_calibration_expansion.py` 的 staging importer 现为每条 external calibration
  observation 写入 deterministic before/after state，并显式写入
  `calibration-expansion-v1` 的 `split=calibration, learner_eligible=0` membership。
  states、transition、physical effect 与 membership 共用一个 savepoint；late failure
  会整体回滚，不能留下 dangling transition 或隐式 learner support。在 v4 development
  freeze 副本上用 8 条历史 support 重建后，H1–H12/A1 审计通过，但 exact calibration
  仍因 WNS per-metric coverage 不足而 `coverage_failed`；因此本修复不改变 canonical
  memory、authority 或 production runtime，也不代表 Parametric 已可晋级。
- calibration expansion 的 sample builder 现将 strict oracle 设为硬 eligibility gate：
  每个 before/after project 都必须绑定最新 backend run 的 `strict_status=pass`、
  `timing_status=clean`，且无 timeout/非零 oracle 返回码，才可生成 calibration sample。
  缺 receipt、LVS/DRC 等 strict failure 或 timing 非 clean 的 pair 只写入
  `excluded_strict_oracle` evidence，不进入 calibration 分母；因此 v63–v68、v75–v80
  当前 LVS-error cohort 不会再被误计为可校准的 physical support。该 gate 只收紧
  staging evidence admission，不写 canonical memory、authority 或 production runtime。
- 修复顶层 derived-schematic 的 supply 语义后，sky130hs geometry canary 通过，新的
  v81–v86 cohort 以 workers=1 完成 12/12 strict-signoff=`pass`、timing=`clean`，6/6
  pair 可评估。将 v81–v83 作为 calibration、v84–v86 作为 held-out 的只读 staging
  exact calibration aggregate coverage=`0.583333`（area/power/TNS/WNS=
  `0.333/0.333/1.0/0.667`），仍为 `coverage_failed`；旧 v12/v13 pair 因没有
  strict-oracle evidence 被排除。该结果证明 signoff 闭环已恢复，但不构成 ready
  policy、authority promotion 或 production runtime 写入。紧凑 durable evidence（含
  strict/sample/calibration receipts 与 staging SQLite/artifacts）位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-action40-calibration-v81v86-clean/`，原始
  ORFS RUN 树仍保留在 `/tmp/tehm-v4-clean-v81v86`。
- v3 authority staging 快照已通过输出式 migration 生成 v4 副本；migration reader
  改用 SQLite `immutable=1`，避免 WAL 源在只读审计时产生 `-wal/-shm` sidecar 并误报
  source mutation。13 张 canonical 表逐表 count/digest 保持不变，且回归测试覆盖
  WAL-backed source。迁移副本的 H1–H12/A1 审计仍因 H2 dangling provenance、H7
  obligation 不完整、H10 rollback authority 缺失而 `DENY_REPLAY_NOT_VERIFIED`；完整
  负证据位于 `evidence/tehm-authority-v1/v4/migration-audit-v1/`。migration 只修复
  schema 可读性，不修复证据完整性，也不改变 canonical memory、authority 或 runtime。
- v87–v92 新建了与 v81–v86 support source-disjoint 的 exact action-40 held-out cohort。
  12/12 ORFS arm 完成，但 strict pair firewall 淘汰 v89 action 与 v90 before 的
  DRC/LVS dirty pair，仅 v87/v88/v91/v92 进入分母。normal retrieval coverage=`0.6875`；
  新接入的 lineage-grouped split-conformal 四项 coverage 均为 `1.0`，但 v87 WNS
  `-0.076515ns` 触发 regression budget，故 `harmful_rate=0.25`、最终
  `shadow_calibration_failed`，未 materialize policy、未运行 shadow observation。
  runner 现在在 retrieval evaluation 中绑定 point prediction，并显式区分 retrieval
  interval 诊断与 Parametric grouped admission；conformal radius 的 inclusive 浮点边界
  也已按既有 epsilon 修复。durable receipts 位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-action40-calibration-v87v92-clean/`。
- action36/action38 筛选分别建立了 3 条 strict-clean exact-signature support 与 3 条
  完全独立 held-out；原 support v95/v99 strict-dirty 后由显式绑定
  `replacement_for` 的 v105/v106 补位，没有降低 support 下限。两组 held-out 均 3/3
  strict-clean，normal coverage 都为 `0.583333`，grouped conformal 四项均为 `1.0`；
  但 v97/v103 的面积分别退化 `+2/+26um²`，使两组 `harmful_rate=1/3`，所以均未
  materialize policy 或运行 shadow。sample-only campaign 的 `--phase promote` 也已
  修复为不再错误依赖 calibration report；support 与 held-out receipts 保存在
  `/data1/zhangdy/tehm-campaigns/tehm-p2-action36-*` 和 `tehm-p2-action38-*`。
- action34 使用 base 28/30/32，显式避免 no-op，并建立 v107–v109 support 与
  v110–v112 held-out。12/12 arm strict pass/timing clean；normal coverage=`0.583333`、
  grouped conformal 四项=`1.0`，但 v112 area `+3um²` 导致 `harmful_rate=1/3`，因此
  仍未 materialize 或运行 shadow。durable receipts 位于
  `/data1/zhangdy/tehm-campaigns/tehm-p2-action34-*`；下一步只能用新的 source-disjoint
  action32 cohort 继续，不能删除 harmful lineage 后重算 readiness。
- Shadow harness 现在真正支持 decision case：对 `candidate_actions` 做一候选一
  receipt 展开并写入 deterministic `candidate_rank`；action-conditioned policy 可用
  `calibration_policies[action_digest]` 逐候选绑定，缺失绑定直接拒绝，避免多个候选
  共用错误的 knob-value calibration。多候选 outcome 必须按 `receipt_id` join；这只
  完善实验闭环，不改变 canonical memory 或授予 promotion authority。
- ORFS campaign 默认 scratch root 为 `/tmp/tehm-orfs/<campaign>`；写入
  `/data1/zhangdy/tehm-campaigns` 会被拒绝，除非显式设置
  `R2G_ALLOW_DATA1_ORFS_WORK=1`。每个 campaign manifest 记录可删除的 RUN/logs/results/objects
  与应复制到 evidence root 的 receipts/reports/DEF/summary。
  将 durable 产物提升到证据目录使用 `scripts/promote_orfs_evidence.py`；它不会删除
  scratch，也不会复制 RUN 的 results/objects；RUN 下的最终 DEF/GDS/JSON 和
  `run-meta/stage_log` 会展平到 evidence 的 `final/<run-id>/`、`receipts/<run-id>/`，
  evidence root 不保留 `RUN_*` 目录。

### P3 procedural component replay（非 canonical v3 freeze）

2026-08-17 已完成四个 future lineage 的真实 Icarus replay。每个 fixture 的 baseline
target oracle 为 FAIL、frozen regression 为 PASS；M5/M6/M4/M7 分别移除 role、predicate、
validity、obligation gate。四个 component discrimination 均为
`component_contrast_observed`，但 `validated_rules=1 < 2`，所以 procedural acceptance
保持 fail-closed。该证据只写入独立目录，不回灌 canonical v3。

### P3 procedural rule-growth replay（隔离副本，非 canonical v3 freeze）

`scripts/run_procedural_rule_growth.py` 在 `/tmp` 的 canonical 副本上重新捕获两个真实
RTL lineage，并运行 `crystallize_all` 与 validity profile。副本得到 8 条 rules、7 条
cross-lineage `VALIDATED` rules；源 canonical 的 2 rules / 116 transitions 前后完全不变。
该结果证明 rule-growth harness 可工作，但没有任何写入或 promotion 权限。

### P3 procedural growth + runtime ablation v2（隔离 staging，非 canonical v3 freeze）

新增 `p3_reset_restore_a/b` 两条 prospective lineage，并以 `p3_reset_restore_c`
作为第三条独立 rule-growth training lineage，分别验证 reset 语义恢复、regression
保留与 AST payload 的 role-normalized binding。三条 fixture 的 target/regression
均由真实 Icarus 返回 PASS；隔离 staging 结晶出的 `RESET_RESTORE` rule 通过 V4
并成为 `VALIDATED`，仅在该副本临时进入 runtime scope。两任务逐 arm replay 的
M8 Rule Coverage=`0.5`、VCG=`0.5`、
harmful activation rate=`0`，validity gate 与 obligation transfer 均有可辨识对照，
接受门全通过；canonical v3 的 SHA-256 与 counters 均未变化。该结果是 runtime
mechanism evidence，不是 canonical promotion，也不证明其它 RTL mechanism 已具备
同等覆盖。

### P3 procedural rule-stability replay（隔离副本，非 canonical v3 freeze）

`scripts/run_procedural_rule_stability.py` 对 full、去掉每条 fixture 的
leave-one-lineage-out 副本按 executable-pattern signature 比较规则，而不比较易变的
rule ID。full 副本的 7 条 `VALIDATED` rules 在两个 LOO 变体中的最小 retention 为
`0.857`；runtime Rule Coverage/VCG 明确标记为 `NOT_AVAILABLE`，因为该审计不执行
activation/A-B，也不写 canonical。

### P3 mechanism-family v3 replay（隔离副本，非 canonical v3 freeze）

本轮把 `valid_ready_bug`、`fifo_space_bug` 与 `p3_role_collision`、
`p3_predicate_unknown`、`p3_validity_boundary`、`p3_obligation_recovery` 六个真实
Icarus fixture 放入同一只读 canonical 副本的 rule-growth harness。growth report
记录 8 个 rule profile，其中 7 个通过 cross-lineage V4；stability report 的
leave-one-lineage-out 最小 validated-rule retention 为 `0.857142857`. 隔离 staging
再临时 enrolled 可执行 RTL rule，逐 arm component replay 的 acceptance=`true`，
role/predicate/validity/obligation 四个 contrast 均为 `component_contrast_observed`，
harmful activation rate=`0`，non-singleton effect groups=`8`，cross-lineage rule
support=`8`。不过 M8 Rule Coverage=`0.25`、VCG=`0.25`，低于此前 reset v2 的
`0.5/0.5`；因此这里的进展是机制可辨识性和谱系覆盖的扩大，而不是覆盖率提升，
不得据此升级 runtime authority 或实现 Parametric View。所有 durable 文件仅为报告、
manifest 和 promotion receipt；staging SQLite、artifact/eval_work 与 ORFS tree
仍留在 `/tmp`。

### P3 mechanism-family v2 coverage replay（隔离 staging，非 canonical v3 freeze）

为验证 v3 的低 Coverage 是否只是所有任务都在测试 veto，新增
`p3_positive_valid_ready` 与 `p3_positive_fifo_space` 两个独立 future lineage；两者
均声明 role-compatible、predicate=`TRUE`、candidate validity=`PROVISIONAL_VALID`，
并通过真实 Icarus target/regression oracle。六任务 manifest 将四个负迁移/门控任务与
这两个正向路径任务绑定，acceptance 预注册为 Rule Coverage/VCG `>=0.5`。隔离
staging 的逐 arm replay 结果为：M8=`3/6`、M0=`0/6`，Rule Coverage/VCG=`0.5/0.5`，
validated rules=`7`，cross-lineage support=`8`，non-singleton groups=`8`，harmful
activation rate=`0`，四个 component contrast 全部为 `component_contrast_observed`。
cluster-level M8 rate 为 `0.4`，Wilson 区间仍较宽，因此该结果只证明可执行正向路径
已补齐到 reset-v2 的覆盖基线；canonical v3 未写入，runtime authority 仍为 staging-only。
报告和 v2 manifest 位于 `/data1/zhangdy/tehm-campaigns/tehm-procedural-mechanism-ablation-v2/`。

### P3 mechanism-family v3 credit-return follow-up（隔离 staging，非 canonical v3 freeze）

新增独立 `p3_positive_credit_return` guard-strengthen lineage，并与既有训练/对照
fixture 一起做 9-lineage rule-growth、leave-one-lineage-out stability 与 7-task
逐 arm replay。隔离 staging 结晶出 7 条 `VALIDATED` rules、8 条 cross-lineage
support；runtime Rule Coverage/VCG=`0.5714/0.5714`，harmful activation rate=`0`，
四个 component contrast 仍全部可辨识，预注册 acceptance=`true`，LOO 最小 retention
=`0.8571`。cluster-level M8 rate 仍为 `0.4`，所以这是稳定性与可执行性增强证据，
不是 production promotion；canonical v3 counters/SHA-256 未变，Parametric View 仍为
`NOT_IMPLEMENTED`。compact evidence 位于
`/data1/zhangdy/tehm-campaigns/tehm-procedural-mechanism-ablation-v3-credit/`。

### P2 prospective observation pilot（非 canonical v3 freeze）

2026-08-17 已在独立 staging DB 与 `/tmp` scratch 上完成最小 observation pilot：
两个新 lineage（`future-shadow-v3:sky130hs:future_shadow_logic:base0`、
`future-shadow-v3:ihp-sg13g2:future_shadow_logic:base0`）各生成一个固定
`DENSITY_RELIEF` action。2/2 shadow receipt 与 2/2 outcome 成功 join，canonical
六类 counters 前后完全不变；两条 proposal 均因 calibrated OOD gate
`ABSTAINED`（reason distribution=`out_of_distribution:2`，OOD distance
0.945599–0.952020），所以 proposal coverage=0，obligation coverage=0.333，decision
round 按预注册门槛暂不启动。小型证据目录为
`/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v3/`，其中只保留
receipts、reports、最终 DEF/GDS/JSON 与 shadow metrics；该 pilot 尚未写入或
覆盖 canonical v3 bundle。

### P2 prospective observation v89（非 canonical v3 freeze）

future-prospective-v8/v9 两条独立 sky130hs lineage 使用固定 CORE_UTILIZATION=22
observation action，并以 ORFS route/PPA 作为 post-execution oracle。proposal/outcome
均完整 join，且 canonical memory 未变；预注册 decision gate 明确失败于 harmful-rate
和 WNS interval coverage，故 decision cases 未准备、候选 action 未执行。完整 manifest、
hash-chained log、outcomes、metrics 与 fail-closed gate report 位于
`/data1/zhangdy/tehm-campaigns/tehm-prospective-shadow-v89/`。

> **历史 Evidence Freeze v1（2026-08-08；仅用于审计比较）**：已生成可重现证据包
> `/data1/zhangdy/tehm-campaigns/tehm-evidence-freeze-v1/`。冻结快照是独立的
> `closed_loop/tehm.sqlite`，避免把历史恢复库和闭环重放证据混在一起。快照当前包含
> **47 transitions / 7 rules / 8 activations / 8 trials**；其中 RTL 与真实 ORFS route
> 各有真实 A/B 闭环，另保留不可判定/timeout 的 ORFS attempt receipts。入口命令为 `./reproduce.sh`，现在会实际重跑一个 bundle 内的最小 ORFS A/B（而不仅是验证历史 receipt）；完整哈希和计数见
> `bundle_manifest.json`。
> 冻结的 M0/M1/M8 报告位于 `evaluation/m0_m1_m8_report.{json,md}`，任务清单位于
> `evaluation/heldout_task_manifest.json`：当前包含 1 个 RTL task 和 1 个真实 ORFS
> held-out trial。当前 task selection 报告 6 个可判定 task、5 个 lineage clusters：task-level M0=3/6、M1=3/6、M8=6/6；保守 cluster-level M0=2/5、M1=2/5、M8=5/5。另有 7 个明确披露的 duplicate/infrastructure-excluded attempts；该结果是第一份 cluster-aware controlled comparison，仍不宣称普适 benchmark。

| Phase | 内容 | 状态 |
|---|---|---|
| 0 | 冻结 legacy baseline 快照 | ✅ `baselines/r2g_legacy/` |
| 1 | Backend seam（`R2G_MEMORY_BACKEND=none\|legacy\|tehm`）+ 真实 reports 捕获 | ✅ |
| 2 | 独立 TEHM Canonical Store（states / transitions / episodes / artifacts / 经验图边；capture 与五视图物化由同一 caller-safe savepoint 原子提交） | ✅ |
| 3 | 五视图 + φ 物化（semantic / diagnostic / episodic / procedural；parametric = NOT_IMPLEMENTED） | ✅ |
| 4 | Effect Canonicalization（`K_primary`）+ Crystallizability Preflight | ✅ |
| 5 | Joint Rewrite Anti-Unification + Skill Synthesis（φ_P，候选 rule 生成） | ✅ |
| 6 | Rule Validity Gate（V2 → V1 → V3 → V4）+ Risk Stratification | ✅ |
| 7 | Typed Retrieval（query planning → high-recall → symbolic filter → rerank） | ✅ |
| 8 | 八步 Activation Pipeline（Applicable ⟂ Executable ⟂ Verifiable） | ✅ |
| 9 | 独立 Rule Lifecycle + A/B（shadow → candidate → promoted/demoted） | ✅ |
| 10 | 完整 RTL AST 扩展（rtl.* 域 + Verilog 解析 + 真实 Icarus oracle + RTL 捕获） | ✅ |
| 11 | **Cross-stage Physical Effect Memory** | ✅ |

**Phase 11**（设计文档 26 Phase 11）——`tehm/physical/`，连接 flow/RTL 阶段与物理
signoff 阶段：

    (RTL/flow context, action) → (ΔWNS, ΔTNS, ΔArea, ΔPower, ΔCongestion, ΔDRC)

- `effects.py` — `extract_deltas(before_ppa, after_ppa)` 计算六维物理 delta；
  任一侧缺失的 metric → `None`（绝不造假，H3）。
- `memory.py` — `PhysicalEffectMemory`：记录每次动作的物理效果 →
  `tehm_physical_effects` 表；按 transformation family / effect key 聚合出
  经验 profile（mean/min/max delta + support + harmful 信号），支持按
  `graph_context_digest` 做条件化 profile，也支持 platform/tier-compatible 的
  robust-scaled kNN、逐 metric 95% uncertainty 与 fail-closed abstain；production
  log PPA 可在不改 canonical
  transition ID 的前提下带证据回填。
- `graph_context.py` — 消费 def-graph 的 8 张 X-side feature tables，保存紧凑物理图
  特征、topology row counts、DEF/CSV sha256、extractor version 与 signoff gate；
  feature 完整度和 dataset tier 分开记录，dirty gate 只能是 `research` tier。
- DB schema v2：`tehm_physical_effects` 新增 graph context JSON/digest/extractor
  version；v1 store 通过 forward-only migration 原地升级。
- **第一阶段不声称可微梯度**：`predict` 返回经验均值 + support，并显式标注
  "no differentiable gradient claimed"。
- CLI：`physical-record`（before/after PPA → 记录）/ `physical-profile`（聚合）。
- 与 def-graph 的连接已完成：真实 AES/RISC-V DEF 特征已进入 campaign Physical
  Effect Memory；缺少 final DEF 或 strict signoff provenance 的设计不会伪装为可用。

**Phase 10**（设计文档 26 Phase 10、22.1 RTL v2）——`tehm/rtl/`：
- `verilog_parse.py` — 纯 Python 结构化 Verilog 解析器（模块/端口/信号/always
  block/FSM case 转移与守卫，begin/end 平衡）。
- `rtl_graph.py` — RTL 语义图（MODULE/ALWAYS_BLOCK/SIGNAL/STATE_REG/
  FSM_TRANSITION/CLOCK/RESET 节点 + CONTROL_PATH 边）+ 内容寻址 digest。
- `rtl_actions.py` — rtl.* action 域（AST_REWRITE / GUARD_STRENGTHEN /
  RESET_RESTORE / WIDTH_CORRECT / PRIORITY_REORDER）；`GUARD_STRENGTHEN` 注释感知
  重写（只改非注释代码区，幂等）。
- `rtl_oracle.py` — **真实 Icarus oracle**（iverilog/vvp compile+sim，测试基准
  `$fatal` → exit code 判定 PASS/FAIL；工具缺失时优雅降级）。
- `rtl_evidence.py` — RTL 工程（rtl/*.v + tb/*.v + manifest.json）→ 真实验证的
  ExecutionRecord（`rtl.GUARD_STRENGTHEN` action）。
- RTL action 参数作为 role-normalize slot（`rtl.source_state` 等）→ 两个不同
  状态名的同机制修复结晶成带 `$SRC/$DST/$COND` holes 的规则。

**真实 campaign**（`scripts/run_rtl_campaign.py`，用真实 iverilog/vvp）：
在 3 个真实 Verilog 设计（`req_ack_bug` / `req_ack_bug2` 训练，`req_ack_bug3`
held-out，均为 handshake-completion bug）上跑通全闭环：
`训练捕获(PASS) → 结晶+审计(GUARD_STRENGTHEN PROVISIONAL_VALID, $SRC/$DST/$COND)
→ 检索(held-out APPLICABLE) → 激活(真实 guard-strengthen + 真实 sim PASS → 新
transition) → shadow → candidate → A/B win → promoted`。

**engineer_loop 闭环接线**（backend-routed、env 门控、fail-closed）：
- `ingest_run.py`：单一 backend authority。`legacy` 只写 legacy store；`tehm`
  只 capture canonical experience 并 rebuild/crystallize；`none` 不写长期记忆。
  不再使用“先写 legacy、再镜像 TEHM”的污染路径。
- `diagnose_signoff_fix.py`：按 backend 隔离 authority。`legacy` 保持原 indexed
  recipe/lifecycle/lessons 路径；`none` 只用共同 cold-start catalog；`tehm` 不读取
  legacy recipe/lifecycle/lessons，经 `runtime_router.py` 的统一 MemoryBackend 接口
  执行 query → retrieve → propose_activation，并把 APPLICABLE 规则 prepend 为
  `source='tehm_rule'` 策略（28.4 归因）。错误可见且 fail-closed 到 cold-start。
- `runtime_router.py` — Phase 1 runtime router；诊断脚本不再直接打开 TEHM DB。
- `tehm_backend.propose_activation` — rule lookup → binding → rewrite instantiate →
  obligation transfer → 确定性 activation ID。
- runtime retrieval 只索引 `promoted` rule；VALIDATED 但仍处于 shadow/candidate 的
  rule 只允许审计/A/B，不能贡献生产 Rule Coverage。
- `tehm/integration/fix_consultation.py` — 低层咨询适配器（campaign/tests 使用）。
- `suggest_config.py`：`legacy` 保持 feature-KNN/heuristics，`none` 只用共同静态
  policy，`tehm` 只接收 backend 产生的 typed config proposal；所有 proposal 仍经过
  design-type clamps 与 `PLACE_DENSITY_LB_ADDON >= 0.10` 安全下限。
- `engineer_loop.py`：非 legacy run 不打开 legacy DB、不运行 legacy learner/A/B；
  TEHM 每轮调用 backend rebuild，新 admissible rules 独立进入
  `tehm_rule_status: shadow → candidate`。Ledger 记录 backend/schema/snapshot，禁止
  跨 backend resume；冻结 evaluation snapshot 漂移时拒绝 resume。
- TEHM `ab-drain` 已接真实 ORFS sandbox executor：arm A 为未修改 control，arm B
  只应用被测 typed rule；两边执行真实 `run_orfs`，再以
  `fix_signoff --max-iters 0` 建立 oracle（不追加 catalog repair）。结果只写
  `tehm_trials` / `tehm_activations`，由 promotion authority 更新
  `tehm_rule_status`，绝不打开 legacy `ab_trials` / `recipe_status`。
- A/B rollback authority：source 的 `constraints/` + `rtl/` 内容快照、sandbox、
  lifecycle status_version 全部入 receipt；source 漂移会自动精确恢复并 re-hash，
  regression/stale/non-divergent/低 obligation coverage 均不能 promotion。
- `R2G_MEMORY_READ_ONLY_EVAL=1`：TEHM 使用 SQLite `mode=ro`，所有 backend 的
  ingest/learn/lifecycle mutation 均关闭，held-out evaluation 不回灌 memory。

**Phase 8**（设计文档 10、11、21.3、26 章）——`activation/` 包，八步：
`1 Retrieve(Phase 7) → 2 Applicability → 3 Structural Binding → 4 Obligation
Transfer → 5 Instantiate Rewrite → 6 Sandbox Execute → 7 Oracle Verify → 8
Update`。三个 activation-time 轴——**Applicable ⟂ Executable ⟂ Verifiable**——在
`ActivationRecord` 中分开存储，绝不压成单一 success（设计文档 11）。成功的激活
会通过 canonical capture 产生**新的 verified transition**（喂给下一轮结晶）。
R2G 执行基座（engineer_loop / run_orfs / fix_signoff / oracle）作为可注入 callable
（21.3），生产接线留给完整 Phase 1。

**Phase 9**（设计文档 20.10、24.3、26 章）——`lifecycle/` 包：
- `rule_status.py`：`shadow → candidate → promoted/demoted/quarantined`，每次
  状态迁移 `status_version` 单调递增；**只有 PROVISIONAL_VALID/VALIDATED 规则
  能进入 shadow**（H6）。lifecycle 行在 `tehm_rule_status`，verdict 在
  `tehm_trials`——绝不写 legacy `recipe_status`/`ab_trials`。
- `trial_adapter.py`：backend-neutral TrialSubject（arm A = control，arm B =
  强制 TEHM rule 激活），variance-aware LCB 判决（镜像 legacy `judge_repeated_ex`）。
- `authority.py`：promotion authority（24.3）——真实 A/B、obligation coverage
  足够、无 hard regression、status version 未变、arms 实际有差异；production 路径
  还必须同时通过 rollback、registry、cross-lineage TE、harmful-rate 与 conformal
  coverage 六项 gate；任一缺失/失败即拒绝并保持 candidate。

**记忆闭环**（一次完整流转）：
```
capture(训练语料) → crystallize+audit(VALIDATED) → retrieve(held-out)
→ activate(APPLICABLE/EXECUTABLE/PASS → 新 transition) → lifecycle
→ shadow → candidate → A/B win → promoted
```

**Phase 7**（设计文档 9、26 章）——检索对象是 **当前修复状态 → 相似规则**，绝非
文本查询 → 文本块（9.1）：
- `retrieval/query_planner.py` — Stage 0：`RepairContext → MemoryQuery`（per-view
  优先级 + 嵌入修复证据 check/design/platform）。
- `retrieval/index.py` + `recall.py` — Stage 1 high-recall：只索引
  `PROVISIONAL_VALID`/`VALIDATED` 规则（H6），按 check/family/obligation 建
  metadata 索引；相似度 = check match + obligation overlap（刻意宽松，精度交给
  Stage 2）。
- `retrieval/symbolic_filter.py` — Stage 2：`P_h(S_q) → APPLICABLE | INAPPLICABLE
  | UNRESOLVED`。**UNKNOWN 永不过默认通过**（H3）；INAPPLICABLE 是最终 veto。
- `retrieval/rerank.py` — Stage 3：透明乘法分
  `Score = Similarity × Utility × Confidence × (1 − RiskPenalty)`；veto 不被
  ranker 覆盖（9.5）；UNRESOLVED 降权不丢弃。
- `retrieval/pipeline.py` — `retrieve(conn, context)` 编排四阶段 → `RetrievalReceipt`。
- `tehm_backend.retrieve` 已接入（不再为空）。

**Phase 6**（设计文档 7、8、24.1、26 章）：
- `validity.py` — 有序有效性审计 `V2 → V1 → V3 → V4`（状态机）：
  - **V2 Non-Triviality**：排除 instance memorization（hole_ratio=0 且 support<3）与
    wildcard collapse（hole_ratio 过高）；规则必须落在有效抽象带。
  - **V1 Derivation-Faithful Replay**：只用 anti-unification 的 crystallization-time
    witness 重放（`r[Θ_i^AU] ≈ A_i`），**禁止重新搜索有利 binding**（honesty H5：
    V2 严格先于 V1 被咨询）。
  - **V3 Effective Support**：报告 raw support / unique attempts / unique lineages /
    unique families；cross-lineage 标注（单 bug 多 seed 不支撑跨 lineage 声明）。
  - **V4 Stability**：leave-one-out 重结晶（`r_{-i} = φ_P(G \ e_i)`）检查能否解释
    held-out episode；**n < 3 时 V4 = N/A 而非 FAIL**（7.5）。
  - 结果状态：`REJECT_DEGENERATE` / `REJECT_UNFAITHFUL` / `INSTANCE_MEMORY` /
    `PROVISIONAL_VALID` / `UNSTABLE_CANDIDATE` / `VALIDATED`。仅
    `PROVISIONAL_VALID`/`VALIDATED` 可进入 runtime lifecycle（H6）。
- `risk.py` — 风险分层（设计文档 8）：`CREATED_REGRESSION`（PASS→FAIL）与
  `NEWLY_OBSERVED_FAILURE`（N/A→FAIL）单独记录 activation context；v1 不自动把
  `P_c` promotion 为 `P_h`（status = CONTEXT_DEPENDENT）。
- `crystallize_all` 现在自动跑审计：合成 rule → 审计 → 存 `validity_status` +
  `validity_profile` + `risk_profile`。

**Phase 5**（设计文档 6.6、22.4、26 章，`crystallization/` 的核心算法贡献）：
- `role_normalize.py` — 把每个 verified transition 投影到固定的 role-aligned slot
  schema（`match.target_check` / `match.knob` / `rewrite.value` / `execution.rerun_from` /
  `execution.recheck` / `verification.*`）。knob/check 等结构身份作为 slot VALUE，
  便于 anti-unification 在跨实例不同时把它们 hole 化。
- `anti_unify.py` — **joint rewrite anti-unification**：按设计文档 6.6 的合并顺序
  （pairwise AU cost → 最低 cost → episode id tie-break → 合并 → 重算），吸收式
  合并保证**每个 slot 路径只有一个 hole**（共享 hole namespace，before/after 不冲突），
  `source_substitutions` 保留每个来源的 crystallization-time witness，`merge_trace`
  完整保留。
- `synthesize_skill.py` — 把 AntiUnifyResult 变成候选 rule（设计文档 4.3/22.4）：
  `skill_type` + `match` + `rewrite` + `execution` + `verification` obligations，
  content-addressed `rule_id`，`status: CANDIDATE`（V2 审计在 Phase 6）。
- `build_rules.py` — 完整管线：`preflight → 按 effect 组 role-normalize → joint
  anti-unify → synthesize → 持久化到 `tehm_rules` + `tehm_rule_sources`。
  singleton 永不结晶（V2 原则）；整次 rule/source 重建及 stale retirement 由
  caller-safe savepoint 原子提交，失败不会留下半个 rule projection。

**Phase 4**（设计文档 6.2、6.3、26 章）：
- `crystallization/effects.py` — `K_primary = Canon(ΔV_target/preserve, ΔF, ΔC)`：
  target verdict delta + preserve pass count + **归一化的** failure delta
  （FIXED/SHIFTED/NEW/REDUCED/INCREASED…，非实例原始值）+ coarse structural
  delta（transformation family + 结构 flag）。`CREATED_REGRESSION` /
  `NEWLY_OBSERVED_FAILURE` 按设计文档 6.2 **不属于 primary key**（进 risk
  stratification）。capture 存储的 key 与 preflight 分组 key 共用同一 canon。
- `crystallization/preflight.py` — 按 effect key 分组，报告 `singleton_rate`、
  `CC_raw`、`CC_lineage`、`key_precision`、`key_recall`，输出设计文档 Phase 4 的
  5 个文件（`groups.json` / `group_report.md` / `group_size.csv` /
  `lineage_support.csv` / `manual_audit_sample.json`），并给出诚实的 verdict
  （`crystallizable` / `crystallizable_raw_only` / `marginal` /
  `instance_dominated`）——实例主导的语料不会被宣称可结晶。

**Phase 1 的运行时接入方式**（`R2G_MEMORY_BACKEND` seam）：
- `memory/interface.py`（MemoryBackend Protocol）+ `memory/contracts.py`（共享数据契约）+ `memory/factory.py`（进程启动时锁定 backend，fail-closed）。
- 三个 backend：`none`（无记忆基线）/ `legacy`（对 legacy knowledge 的**只读**适配器，retrieve 直接读提交的 `heuristics.json`，语义不变）/ `tehm`（完整替换，走 canonical capture）。
- `tehm/adapters/r2g_evidence.py`：读真实 R2G project dir（`reports/*.json` + `config.mk` + `fix_log.jsonl`），每个 fix 迭代 → 一条 Verified Transition，同 `fix_session_id` 累积成 Repair Episode Graph。
- `ingest_run.py`、`suggest_config.py`、`diagnose_signoff_fix.py` 与
  `engineer_loop.py` 已按 backend 路由并严格隔离 authority。
- TEHM learner/lifecycle 与真实 ORFS A/B executor 均已接入 engineer loop；无法
  完整 binding 或不能产生 config delta 的 rule 会诚实地不可执行，不会造出差异。
- Phase-1 golden/firewall gates：默认 legacy 与显式 legacy 的 suggest 输出字节一致、
  ingest logical DB rows 一致；Python process audit 验证 TEHM 不打开 legacy authority，
  legacy 不打开 TEHM authority。

**隔离原则**：TEHM 不读任何 legacy `knowledge/` 对象作为 memory authority；TEHM DB 路径有 fail-closed 校验（H5）；legacy backend 只读 legacy（H8）。

---

## 目录

```
memory/
├── README.md                      # 本文件
├── docs/                          # 设计文档（唯一权威）
├── baselines/
│   ├── r2g_legacy/                # Phase 0 冻结的 legacy 快照
│   └── freeze_legacy_baseline.py  # 冻结脚本（只读 legacy，绝不写入）
├── __init__.py                    # memory 包标记
├── .gitignore                     # 忽略运行时产物（tehm.sqlite/artifacts/__pycache__）
│
├── interface.py                   # Phase 1: MemoryBackend Protocol（设计文档 17.1）
├── contracts.py                   # Phase 1: 共享数据契约（ExecutionRecord/RepairContext/MemoryQuery/...）
├── factory.py                     # Phase 1: R2G_MEMORY_BACKEND 选择 + 进程锁（17.2-17.4）
├── none_backend.py                # Phase 1: 无记忆基线（M0 arm）
├── legacy_backend.py              # Phase 1: legacy 只读适配器（retrieve 读 heuristics.json，ingest_project 走真实 ingest_run.py）
├── tehm_backend.py                # Phase 1: TEHM backend（capture/retrieve/propose）
├── runtime_router.py              # Phase 1: backend-neutral signoff runtime consultation
│
├── tehm/                          # TEHM 包（stdlib-only，无第三方依赖）
│   ├── __init__.py                # 版本 + schema/predicate/role 版本常量
│   ├── config.py                  # TEHM_DB / TEHM_ARTIFACTS_ROOT / legacy 隔离校验（H5）
│   ├── schema.sql                 # 12 张 tehm_* 表（设计文档 19.2-19.7, 20.11）
│   ├── db.py / migrations.py      # 连接（WAL/busy_timeout/FK）/ schema 版本化迁移
│   ├── ids.py                     # 内容寻址 ID（state/transition/episode/rule/activation）+ stable_dumps
│   ├── artifact_store.py          # sha256 内容寻址 artifact 存储（19.8）
│   ├── canonical/                 # Verified State / Transition / Episode / Verifier / Capture
│   │   ├── verifier.py            #   V_t 证据分层（F/R/T/H）+ toolchain snapshot
│   │   ├── state.py               #   CanonicalState + source_digest（内容寻址）
│   │   ├── transition.py          #   Action/ObservationDelta/outcome 分类（含 created vs newly）
│   │   ├── episode.py             #   Repair Episode Graph + trajectory_summary
│   │   └── capture.py             #   ExecutionRecord → 规范存储（含会话累积 + 视图物化）
│   ├── graph/                     # LocalDesignGraph + RoleProjector + PredicateExtractor
│   │   ├── local_design_graph.py  #   RunContextGraph（flow/signoff v1 语义图，22.1）
│   │   ├── feature_extractor.py   #   ψ: G_D → 特征集（6.5）
│   │   ├── roles.py               #   RoleProjector（6.4，含 UNKNOWN）
│   │   └── predicates.py          #   三值 PredicateExtractor（UNKNOWN != FALSE，H3）
│   ├── views/                     # 五视图物化（parametric 明确 NOT_IMPLEMENTED）
│   │   ├── base.py                #   ViewRecord + payload_digest + upsert
│   │   ├── semantic.py            #   RunContextGraph 视图
│   │   ├── diagnostic.py          #   故障签名 F（22.2）
│   │   ├── episodic.py            #   修复轨迹视图（22.3）
│   │   ├── procedural.py          #   实例级可执行规则视图（22.4）
│   │   ├── parametric_stub.py     #   NOT_IMPLEMENTED（22.5，不造假数据）
│   │   └── materialize.py         #   φ 调度（5）
│   ├── parametric/                # read-only shadow RFC（不物化 Parametric View）
│   │   ├── shadow.py              # readiness/replay/OOD gates + provenance receipt
│   │   └── __init__.py
│   ├── adapters/
│   │   ├── r2g_evidence.py        # Phase 1: 真实 reports/config/fix_log → ExecutionRecord + episode 累积
│   │   └── orfs_pair.py           # 普通 production ORFS before/after pair（与 A/B 严格分离）
│   ├── crystallization/           # Phase 4-5: 经验结晶（核心算法 φ_P）
│   │   ├── effects.py             #   Phase 4: K_primary 规范 Canon（6.2，归一化 delta）
│   │   ├── preflight.py           #   Phase 4: 可结晶性预检（6.3，5 个输出文件 + verdict）
│   │   ├── role_normalize.py      #   Phase 5: role-aligned slot 投影（6.4）
│   │   ├── anti_unify.py          #   Phase 5: joint anti-unification + merge trace（6.6/23.2）
│   │   ├── synthesize_skill.py    #   Phase 5: candidate rule 生成（22.4/4.3）
│   │   ├── build_rules.py         #   Phase 5-6: preflight→anti-unify→audit→persist（20.5）
│   │   ├── validity.py            #   Phase 6: 有序有效性审计 V2→V1→V3→V4（7/24.1）
│   │   └── risk.py                #   Phase 6: 风险分层 created/newly（8）
│   ├── retrieval/                 # Phase 7: 类型化检索（9）
│   │   ├── query_planner.py       #   Stage 0: RepairContext → MemoryQuery（9.2）
│   │   ├── index.py               #   admissible rule 索引（by_check/family/obligation）
│   │   ├── recall.py              #   Stage 1: high-recall（9.3）
│   │   ├── symbolic_filter.py     #   Stage 2: P_h(S_q) 硬过滤，UNKNOWN 不过（9.4）
│   │   ├── rerank.py              #   Stage 3: 透明乘法分，veto 不被覆盖（9.5）
│   │   ├── pipeline.py            #   retrieve() 编排 + RetrievalReceipt
│   │   ├── causal_recall.py       #   evaluation-only causal path recall
│   │   └── result.py              #   RetrievedRule / RetrievalReceipt
│   ├── activation/                # Phase 8: 八步激活（10/11）
│   │   ├── applicability.py       #   Step 2: P_h(S_q)（复用 retrieval 符号过滤）
│   │   ├── binding.py             #   Step 3: 结构绑定 θ_L（hole → 实体）
│   │   ├── obligation_transfer.py #   Step 4: BOUND/SYNTHESIZABLE/UNAVAILABLE + OC
│   │   ├── instantiate.py         #   Step 5: 结构化 action（config_edits/rerun/recheck）
│   │   ├── execute_adapter.py     #   Step 6: 沙箱执行（注入 R2G 基座 callable）
│   │   ├── verify.py              #   Step 7: oracle 验证（F/R/T/H 证据）
│   │   ├── update.py              #   Step 8: 捕获新 transition + utility 更新
│   │   └── pipeline.py            #   八步编排 + ActivationRecord（三轴分离）
│   ├── lifecycle/                 # Phase 9: 独立 rule lifecycle + A/B（20.10/24.3）
│   │   ├── rule_status.py         #   shadow→candidate→promoted/demoted（版本递增，H6 门控）
│   │   ├── trial_adapter.py       #   TrialSubject + variance-aware LCB 判决
│   │   ├── authority.py           #   promotion authority（真实 A/B/无 regression/版本未变）
│   │   └── orfs_trial.py           #   真实 ORFS arms + oracle + activation/trial + rollback
│   ├── rtl/                       # Phase 10: 完整 RTL AST 扩展
│   │   ├── verilog_parse.py       #   结构化 Verilog 解析器（22.1 RTL v2）
│   │   ├── rtl_graph.py           #   RTL 语义图（MODULE/STATE_REG/FSM_TRANSITION/...）
│   │   ├── rtl_actions.py         #   rtl.* 域 + guard_strengthen 注释感知重写
│   │   ├── rtl_oracle.py          #   真实 Icarus oracle（iverilog/vvp）
│   │   └── rtl_evidence.py        #   RTL 工程 → 真实验证的 ExecutionRecord
│   ├── integration/               # typed rule 到执行策略的低层适配
│   │   └── fix_consultation.py    #   TEHM rule → diagnose 策略（source=tehm_rule）
│   ├── physical/                  # Phase 11: Cross-stage Physical Effect Memory
│   │   ├── effects.py             #   extract_deltas（ΔWNS/ΔTNS/ΔArea/ΔPower/ΔCong/ΔDRC）
│   │   ├── graph_context.py       #   def-graph X context + digest/provenance/tier
│   │   └── memory.py              #   profile + similar-graph uncertainty/abstain + PPA backfill
│   ├── evaluation/
│   │   └── campaign_metrics.py    #   RC/AY/BSR/IVR/RU/HAR/OC/TE 精确分母
│   └── cli.py                     # ... / physical-profile / physical-predict / health / honesty
├── scripts/
│   ├── run_rtl_campaign.py        # 真实 RTL campaign：全闭环演示（真实 iverilog）
│   ├── run_orfs_campaign.py       # 可恢复 production ORFS campaign/A-B/metrics/def-graph
│   └── run_orfs_diversity_campaign.py # multi-family/platform + held-out lineage
│   ├── honesty.py                 # H1-H12 honesty gates + A1 artifact audit
│   ├── cli.py                     # init-db / capture / capture-r2g / preflight / health / honesty
│   └── schemas/                   # transition/episode/rule/activation/predicate/role/obligation v1 参考
│
├── tests/                         # pytest（当前工作树 647 个测试，全 stdlib + tmp_path 隔离）
│   ├── conftest.py                # sys.path 注入 + tmp_tehm/sample_record 等 fixture
│   ├── fixtures/
│   │   ├── sample_antenna_fix_record.json      # 合成 ExecutionRecord
│   │   ├── project_antenna_fix/                # 真实格式 project（3 迭代 fix_log）
│   │   └── project_clean_run/                  # 无 fix_log 的 clean run
│   └── test_*.py                  # 当前测试文件集合（见「测试」一节）
│
└── tehm.sqlite / artifacts/       # 运行时产物（默认位置，可用 TEHM_DB / TEHM_ARTIFACTS_ROOT 覆盖）
```

## 核心概念（速览）

- **记忆原子** = Verified State Transition：`e_t = <S_t, A_t, S_{t+1}, O_t, V_t>`
- **记忆片段** = Repair Episode Graph（有序 transition 序列 + 分支）
- **五视图** = Semantic / Diagnostic / Episodic / Procedural / Parametric（`tehm_views` first-class 物化）
- **三值逻辑**：`UNKNOWN != FALSE`（H3，任何 coverage 缺失都不能生成负证据）
- **证据分层**：`F / R / T / H`（formal / regression / target / compile-lint）
- **内容寻址**：相同内容 → 相同 ID（幂等捕获、去重、跨进程确定性）

## 使用

```bash
# 0. Backend 选择（Phase 1 seam；进程启动时锁定）
export R2G_MEMORY_BACKEND=none|legacy|tehm   # 默认 legacy；tehm 时才走 TEHM

# 1. 建库（默认 memory/tehm.sqlite）
export TEHM_DB=/path/to/tehm.sqlite           # 可选，默认 memory/tehm.sqlite
export TEHM_ARTIFACTS_ROOT=/path/to/artifacts # 可选，默认 memory/artifacts
python3 tehm/cli.py init-db

# 2a. 捕获一条 ExecutionRecord（结构见 tests/fixtures/sample_antenna_fix_record.json）
python3 tehm/cli.py capture tests/fixtures/sample_antenna_fix_record.json

# 2b. 从真实 R2G project dir 捕获（读 reports/*.json + config.mk + fix_log.jsonl，
#     每个 fix 迭代 → 一条 Verified Transition，同 session 累积成 episode 图）
python3 tehm/cli.py capture-r2g /path/to/design_cases/<project>

# 3. 可结晶性 preflight（Phase 4；--out-dir 写 5 个输出文件）
python3 tehm/cli.py preflight --campaign-id live --out-dir preflight/

# 4. 结晶 + 有效性审计（Phase 5-6；anti-unify → 审计 V2→V1→V3→V4 → tehm_rules）
python3 tehm/cli.py crystallize --campaign-id live --dry-run    # 先 dry-run 看候选规则与有效性状态
python3 tehm/cli.py crystallize --campaign-id live              # 持久化（含 validity_status + risk_profile）

# 5. 检索（Phase 7；对当前修复状态召回 admissible 规则）
python3 tehm/cli.py retrieve --check drc
python3 tehm/cli.py retrieve --check drc --project /path/to/design_cases/<proj>

# 6. 激活检查（Phase 8；默认 production 只允许 promoted rule）
python3 tehm/cli.py activate --rule <rule_id> --check drc --binding '{"\$H0":"0.16"}' --dry-run
# 受控 ab/audit 才显式使用 evaluation，不改变 production authority
python3 tehm/cli.py activate --authority-mode evaluation --rule <rule_id> --check drc --dry-run

# 7. 真实 RTL campaign（Phase 10；全闭环 + 真实 iverilog/vvp）
python3 scripts/run_rtl_campaign.py

# 8. 物理效果记忆（Phase 11；记录、exact profile、相似图预测）
python3 tehm/cli.py physical-record --transition t1 --family DENSITY_RELIEF \
    --before before_ppa.json --after after_ppa.json
python3 tehm/cli.py physical-profile --family DENSITY_RELIEF
python3 tehm/cli.py physical-predict --family DENSITY_RELIEF \
    --graph-context query_graph_context.json --k 5 \
    --min-unique-contexts 3 --max-distance 3.0

相似图预测只在 platform 与 dataset tier 一致的 context 内做 robust-scaled kNN；
同一 graph digest 的重复观测先聚合，不能虚增几何 support。输出逐 metric 的加权
95% mean interval；context 数不足、metric support 不足、tier/platform 不兼容或
query 超出经验分布时 `abstained=true`（CLI 退出码 2），且不声称梯度或因果泛化。

# 9a. Parametric shadow（只读、外部 log，不物化 Parametric View）
python3 scripts/run_parametric_shadow_campaign.py --phase prepare \
    --db /path/to/frozen/tehm.sqlite --cases prospective_cases.jsonl \
    --readiness parametric_readiness.json --replay-evidence replay_receipt.json \
    --prospective-manifest prospective_manifest.json \
    --out-dir /tmp/tehm-parametric-shadow

# 9. 健康检查 + honesty gates
python3 tehm/cli.py health
python3 tehm/cli.py honesty          # 全绿退出码 0；否则 1 = HONESTY BREACH

# 10. 真实 TEHM ORFS A/B（candidate rules → tehm_trials → promotion authority）
R2G_MEMORY_BACKEND=tehm TEHM_DB=/path/to/tehm.sqlite \
  python3 ../r2g-skills/signoff-loop/scripts/loop/engineer_loop.py \
  ab-drain --ledger /path/to/ledger.jsonl --n-designs 1
# 含 holes 的规则显式提供 target binding（rule_id -> hole -> concrete value）
export R2G_TEHM_AB_BINDINGS='{"rule_x":{"$H0":"CORE_UTILIZATION","$H1":"20"}}'

# 11. 冻结 legacy baseline（Phase 0，只读 legacy）
python3 baselines/freeze_legacy_baseline.py
```

**真实 loop 接入**：`engineer_loop` 每次 flow 后调用 `ingest_run.py`，且只写所选
backend。TEHM 捕获 canonical store 后自动 crystallize/enroll lifecycle；错误
fail-closed，绝不写 legacy authority。
直接验证：

```bash
R2G_MEMORY_BACKEND=tehm python3 ../r2g-skills/signoff-loop/knowledge/ingest_run.py <project> --db /tmp/knowledge.sqlite
```

### ExecutionRecord 输入契约

```json
{
  "record_id": "...", "domain": "flow.signoff",
  "project_id": "...", "design_id": "...", "lineage_id": "...", "repository_ref": "...",
  "before":  {"repository_commit": "...", "config": {...}, "reports": {...},
              "failure_signature": {...}},
  "action":  {"domain": "signoff.REPAIR_ACTION", "transformation_family": "...", "payload": {...}},
  "after":   {"repository_commit": "...", "config": {...}, "reports": {...}},
  "observation_delta": {"original_failure": "REMOVED|PRESENT|UNKNOWN",
                        "first_divergence": {...}, "failing_tests": {...},
                        "created_regressions": [...], "newly_observed_failures": [...]},
  "verification": {"verdict": "PASS|FAIL|UNKNOWN", "oracle_type": "REGRESSION|FORMAL|...",
                   "confidence_tier": "F|R|T|H", "obligation_coverage": 1.0,
                   "evidence_refs": [...]},
  "episode": {"mechanism_family": "...", "lineage_id": "...", "step_index": 0,
              "terminal_status": "VERIFIED_REPAIR"}
}
```

Phase 2 的首批来源是 R2G 已有的 signoff/config 修复轨迹（action 已结构化、
before/after 已存在、oracle 可执行）——后续用真实 `reports/*.json` + `fix_log.jsonl`
喂给 capture adapter 即可。

## 测试

```bash
python3 -m pytest tests/ -q     # 当前工作树 647 passed；canonical v3 freeze 为 225
```

测试全部 hermetic：temp sqlite + temp artifact root，不碰真实 TEHM DB，不碰
legacy（legacy 测试用临时 DB + 只读 heuristics.json），不需要任何 EDA 工具。
覆盖设计文档 27.1 的关键项：content-addressed IDs、transition completeness、
created vs newly-observed、UNKNOWN != FALSE、五视图物化、artifact digest
integrity、effect key 确定性、backend 隔离、捕获幂等；Phase 1 的 factory
选择/进程锁/fail-closed、tehm/legacy backend、真实 reports 捕获、ingest_run
钩子 golden no-op；Phase 4 的 effect canon 归一化（FIXED/SHIFTED/REDUCED…、
config 值不入 key、created regression 不入 primary key）、capture 与 preflight
key 一致性、preflight metrics/5 输出文件/verdict 诚实性；Phase 5 的 anti-unify
确定性、共享 hole namespace、吸收式单 hole/slot、witness 完整性、merge trace
保留、singleton 永不结晶、rule 幂等持久化；Phase 6 的有序审计（V2 退化拒绝、
V1 witness-only 重放、V3 支持度/cross-lineage、V4 leave-one-out、n<3 N/A、
risk CONTEXT_DEPENDENT）；Phase 7 的检索（query planning 嵌入修复状态、
high-recall 只索引 admissible 规则、符号 veto 不被 ranker 覆盖、UNRESOLVED
降权不丢弃、透明乘法分、backend 检索集成）；Phase 8 的八步激活（注入
executor/oracle 完整闭环、三轴分离、成功激活产生新 transition、FAIL/REGRESSION
负证据捕获、dry-run 不持久化）；Phase 9 的 lifecycle（validity 门控进入 shadow、
status_version 单调、variance-aware LCB 判决、authority 拒绝 stale/未差异/
regression/低 coverage 的 trial）；Phase 10 的 RTL（Verilog 结构化解析/FSM
守卫提取、RTL 语义图、注释感知 guard_strengthen 重写与幂等、真实 Icarus oracle
检测 bug→修复 PASS、RTL 捕获、真实 campaign 全闭环 + lifecycle promoted）；
engineer_loop 咨询接线（tehm_rule 策略归因、符号 veto）；Phase 11 的物理效果记忆
（六维 delta 提取、缺失 metric → None 不造假、按 family 聚合经验 profile、
harmful 信号、predict 诚实标注无梯度声称）。

## Honesty gates（tehm/honesty.py）

| Gate | 含义 |
|---|---|
| H1 | 每条 transition 必须有 source state / action / target state / verifier snapshot |
| H2 | 每个物化 view 可回溯 canonical owner + extractor version + digest 一致 |
| H3 | coverage 缺失 → UNKNOWN，绝不生成负证据（UNKNOWN != FALSE） |
| H4 | 每个 source substitution 必须有 episode-owned witness 且可重放 |
| H5 | validity gate 必须按 V2 → V1 顺序执行 |
| H6 | 低于最低 validity 的 rule 不得进入 runtime lifecycle |
| H7 | activation 缺失 obligation 或 verifier 结果不得记为通过 |
| H8 | TEHM 与 legacy backend 必须完全隔离 |
| H9 | held-out / A-B episode 不得进入 learner support |
| H10 | 真实 ORFS trial 必须有 source/config/registry 三层可验证 rollback receipt |
| H11 | evidence bundle export → import → export 必须 byte-stable |
| H12 | TEHM 错误 fail-closed，绝不静默回退 legacy |
| A1 | artifact 内容寻址完整性（blob re-hash，补充审计） |

## 设计文档 26 的 12 个分阶段（Phase 0-11）已全部实施 ✅

### 2026-08-01 production ORFS campaigns（历史基线；已由下节补强）

- canonical store 共 **41 条普通 transitions**，达到设计目标的 30–50 区间；其中
  diversity campaign 新增 8 条，覆盖 `DENSITY_RELIEF` /
  `ROUTING_CAPACITY_RECOVERY`、sky130hs / gf180、4 个训练 lineage。所有 A/B arm
  均由 H9 firewall 排除在 training/capture 外。
- 新增 transition 分层结果：density 1/4 positive（Wilson 95%
  `[0.046, 0.699]`），routing 0/4（`[0, 0.490]`）；sky130hs 1/4，gf180 0/4。
  因此 routing family 尚未形成可晋升规则，不能用总体 coverage 掩盖这一弱项。
- 真正未见的 held-out `ihp-sg13g2/spi`（训练 lineage 和训练 platform 均未出现）：
  70% utilization control A `[0,0]`，promoted typed density-relief B（20%）
  `[1,1]`；两次真实 ORFS 均为 A floorplan fail、B route/finish clean，LCB
  `1.0 > 0.0`，无 created regression。rollback source digest 2/2 一致；规则保持
  `promoted v3`，revalidation 不修改 lifecycle authority。
- 8-case frozen funnel：RCret=RCexec=AY=BSR=IVR=OC=1.0，RU=4/7=0.571，
  HAR=0/7，TE=4/6=0.667；activation rollback 7/7、registry authority 4/4。
  1 个 infrastructure trial / 2 个 activation 从 RU/TE 分母显式排除，历史 UART
  失败证据保留但不冒充设计负例。
- strict-signoff 工具链已可真实执行：用户本地 Magic 8.3.677、Netgen 1.5.323、
  官方 sky130_fd_sc_hd transistor SPICE；AES/RISC-V KLayout DRC 均为 0，OpenRCX
  均产出非空 SPEF。Netgen LVS 当前诚实报告 `top_pin_mismatch`，所以 strict gate
  仍为 dirty，物理图 context 不会冒充 `strict_clean`。
- def-graph：5 个唯一 context digest 覆盖 15/41 physical effects；本轮 3 个真实
  final DEF 均产出完整特征并保持 `research` tier。相似图策略用 platform/tier
  firewall、唯一图去重、robust-scaled kNN 和逐 metric 95% interval；真实 IHP
  OOD 查询因 `no_platform_compatible_contexts` abstain，同平台稀疏查询因只有 2 个
  唯一 context（阈值 3）而 abstain，二者均 `gradient_claimed=false`。
- 结果：`/data1/zhangdy/tehm-campaigns/orfs-v2-diversity/` 下的
  `campaign_metrics.{json,md}`、`diversity_report.json`、`ab_result.json`、
  `physical_graph_contexts.json`、`physical_prediction_report.json`。

### 2026-08-02 strict/context/calibration 补强（已完成）

- powered schematic 不再依赖会崩溃的 OpenROAD 输出：按标准单元 transistor SPICE 的
  精确 power-pin signature 生成 named-pin Verilog；layout/library 只做有 receipt 的
  Sky130 Netgen representation normalization，拓扑、model、polarity、area 仍严格比较。
  AES、RISC-V 与 GCD 均达到 `Circuits match uniquely`；GCD 的 3 个训练 source 和独立
  SPI held-out 的 3 个 source 均通过 DRC/LVS/RCX/timing strict gate，生成真实
  `strict_clean` context。OpenROAD `write_verilog` crash probe 现在短超时后立即进入
  deterministic fallback，不再固定等待 900 秒。
- production context campaign 新增 **36 条 transitions**，canonical/physical store 从
  41 增至 **77**。`sky130hd/strict_clean`、`sky130hs/research`、
  `ihp-sg13g2/research` × `DENSITY_RELIEF`、`ROUTING_CAPACITY_RECOVERY`、新
  `PLACEMENT_DENSITY_RECOVERY` 共 9 个 strata，全部各有 **3 个唯一成功 DEF digest**；
  重复观测不计 geometric support。GF180 的 detail route 在 0 violation 后发生
  `_dbITerm` infrastructure assertion，失败证据保留但未冒充设计负例，第三平台改用
  完整成功的 IHP-SG13G2。
- routing family 从 0/4 补到 **6 条真实 fail→pass positive**（Sky130HD 3、IHP 3）。
  Sky130HS 小 GCD 即使 99% routing-capacity derating 仍能完成，3 条仅记为 neutral
  stress probe，不伪装为 recovery positive。本轮 36 条 outcome 为 6 PASS、21 NEUTRAL、
  9 REGRESSION，原始证据全部保留。
- 独立 `orfs-heldout-v3:spi` lineage 在三个平台各运行 3 个 source × 3 个 family：
  **36 个 production flow 全部成功**，形成 27 个只读 A/B 观测；不调用 capture、record、
  crystallize 或 lifecycle mutation，physical-memory count 前后均为 77。
- held-out 最近距离为 Sky130HS `3.659–4.654`、Sky130HD `7.408–7.852`、IHP
  `431.714–431.718`，均超过既有 OOD safety ceiling `3.0`。校准器现在只允许在 ceiling
  内用样本拟合 distance quantile、empirical coverage 和 uncertainty-width threshold，
  绝不因一个远端 held-out 反向放宽边界。因此 9/9 policies 均为
  `insufficient_support`，实际 gated prediction 9/9 以
  `heldout_calibration_not_ready` abstain，`gradient_claimed=false`，held-out 不回灌。
- parametric view 的证据审计结果为 `DEFERRED_INSUFFICIENT_EVIDENCE`：当前只有一个
  独立 held-out RTL lineage，且 9 个相似图 policy 都尚未越过 OOD/support gate；继续
  保持 `NOT_IMPLEMENTED` 比构造 steering vector 或伪 learned ranker 更诚实。
- 主要结果：`/data1/zhangdy/tehm-campaigns/orfs-v3-contexts/` 的
  `context_coverage_audit.json`、`physical_graph_contexts.json`，以及
  `/data1/zhangdy/tehm-campaigns/orfs-v3-heldout-calibration/` 的
  `calibration_report.json`、`parametric_readiness.json`。

下一步证据优先级（仍属于 Phase 11 成熟度，不虚构 Phase 12）：sky130hs 的 distance
目标已达成（held-out 最近距离 1.55–1.58，全部落进 ceiling 3.0），下一步是
(a) 补 sky130hs `DENSITY_RELIEF` 的 coverage——SPI 的 density delta 比 uart/fifo 训练
context 小，需引入密度响应更小的设计/context，或诚实记录该分布差异；(b) 为 sky130hd
strict_clean 三 family 生成 contexts（fifo/uart 在 sky130hs 已证明可行）；(c) 条件满足后
用第二条冻结 held-out lineage 做外部复核。只有 distance、coverage、uncertainty 和
lineage diversity 同时通过，才重新评估 parametric view 实现。

**2026-08-02 磁盘清理（Tier 1+2，已执行）**：删除全部 campaign 的 ORFS 再生中间产物
（`backend/RUN_*/{logs,results,objects}`，~20 GB）+ 整删废弃的 `orfs-v1`，tehm-campaigns
50G→30G，磁盘可用 29G→46G。**保留** `final/`（6_final.def/.gds——物理图 context 的
digest 源）、`features/`（def-graph 已提取特征）、`lvs/`（powered.v + result）、
`drc/`、`rcx/`、`reports/`、`stage_log.jsonl`/`run-meta.json`、以及全部审计 JSON
（context_coverage_audit / calibration_report / parametric_readiness），digest 仍可对字节
重验。`orfs-v2-diversity/tehm_ab`（A/B arm 克隆 933M）属 Tier 3 暂保留待决定。幂等清理
脚本：`scripts/cleanup_campaign_disk.sh`（`DRY=1` 预览，无参执行）。

### 2026-08-02 v1-era 证据恢复 + orfs-v4 sky130hs 聚焦批次（已完成）

- **v1-era ~33 条项目证据确认不可恢复**：整删 `orfs-v1` 时连同项目证据一并删除（reports/
  fix_log 均无残留）。可恢复上限为保留证据的 **44 条**（v2: 9 DEFs + v3: 42 DEFs），已按内容
  寻址全部重建进 `memory/tehm.sqlite`（44 transitions / 44 effects / 13 unique contexts，
  距离与原报告一致，honesty 全绿）。
- **orfs-v4-add-designs 驱动扩展**：`run_orfs_add_designs_campaign.py` 新增多 family + 多 index
  （`--families` / `--indexes`），`FAMILY_SPECS` 沿袭 v3-contexts 的 index 调度
  （`CORE_UTILIZATION 30/35/40`、`ROUTING_LAYER_ADJUSTMENT 0.05/0.10/0.15`、
  `PLACE_DENSITY 0.50/0.55/0.60`）；`_apply_edits` 支持 `None` 删除行（PLACEMENT after 移除
  `PLACE_DENSITY_LB_ADDON`，否则 util.tcl 优先 LB_ADDON 而忽略 `PLACE_DENSITY`）。
- **sky130hs 聚焦批次（uart+fifo × 3 index × 3 family）**：**24/24 flows 成功**（rc=0，0 失败）；
  capture **18 条 transitions**（17 NEUTRAL + 1 FAIL 负证据：`fifo:0:PLACEMENT_DENSITY_RECOVERY`）；
  canonical/physical store 从 50 增至 **66**，unique graph contexts **13 → 20**（+4 新 base digest）。
- **held-out 距离骤降**：sky130hs SPI held-out 最近距离 **4.27–4.65 → 1.55–1.58**，3/3 全部落进
  OOD ceiling 3.0。校准策略 9 个中 **2 个转 ready**（sky130hs `PLACEMENT_DENSITY_RECOVERY`、
  `ROUTING_CAPACITY_RECOVERY`，empirical coverage 1.0）；sky130hs `DENSITY_RELIEF` 支持度足够但
  **coverage_failed**（41.7% < 80%）：SPI 的 density-relief delta（area 79–108）小于 uart/fifo
  训练 context 的响应，观测值落在校准区间下界之外——真实分布差异，非可调 knob。
- ihp-sg13g2 与 sky130hd strict_clean 三 family 仍 `insufficient_support`（本批未覆盖；ihp 距离
  431 固有困难）。parametric view 维持 `NOT_IMPLEMENTED` / `DEFERRED_INSUFFICIENT_EVIDENCE`
  （2/9 ready + 仅 1 条独立 held-out lineage）。
- honesty gates 全绿（66 transitions / 336 views / 102 artifacts 校验通过）。
- v4 当前可复核产物是 `campaign_manifest.json`、`campaign_state.json`、
  `physical_graph_contexts.json` 和 `sky130hs_batch_run.log`；
  `add_designs_report.json` 当前缺失，因此不再把它列为已生成结果。
  v3 calibration 的 `calibration_report.json` 与 `parametric_readiness.json` 仍可复核。
- **capture fail-closed（2026-08-27）**：`run_orfs_diversity_campaign.py` 的 pair capture
  现在按 `verification.oracle_complete` 决定 learner admission；缺少 DRC/timing 等
  obligation 的 route-clean pair 仍保留为 calibration observation，但写入
  `learner_eligible=0`，不会被训练查询误收。一次受限的 exact-toolchain
  `ROUTING_CAPACITY_RECOVERY`（sky130hs/gcd，base→0.05）preflight 已验证两臂
  `flow_rc=0` 与 route/DEF graph，但仅 `obligation_coverage=1/3`、timing violated、
  DRC/LVS 缺失，故不构成 support evidence，canonical 未变。
  若同一 incomplete transition 已被旧 campaign 错误登记为 training/eligible，strict
  capture 会拒绝追加 calibration membership 并要求新 staging/人工审计，避免
  `EXISTS` learner 查询被历史矛盾行重新污染；旧 membership 不会被静默覆盖。
  `run_orfs_add_designs_campaign.py` 的下一轮 prepare 现在必须先执行
  `--phase freeze`：source-freeze 同时绑定 campaign 参数、ORFS config/SDC/RTL
  字节摘要与 toolchain fingerprint。prepare 会重算这些摘要并记录每个 pair 的
  `input_bindings` 和 `timing_contract`；任何 freeze 后漂移都会在 materialize 前
  fail-closed，observe/capture 也会再次校验，不能把改过的输入当成同一实验。
  `run_orfs_batch0.py` 的 freeze 也不再只是 prepare 前置文件：run、equivalence、
  signoff、graph、observe 和 staging/report 入口会重放 source spec、TEHM 执行源码、
  ORFS 依赖面与 toolchain fingerprint；observe 还会逐 pair 重检 materialized
  config/SDC/RTL binding 与 timing contract。任一依赖或输入漂移都会停止该 phase，
  防止长批次在中途更换 flow 后继续复用旧结果。

### Batch-0 `riscv32i` exact-toolchain replay（2026-08-27）

在上述 source-freeze 下只选择一个 source-disjoint support pair：
`sky130hs:riscv32i:u50->u40`。同一 packaged ORFS tree 的两个 arm 均完成
synth/floorplan/place/CTS/route/finish，route/DRC clean，RCX complete，独立 RTL
equivalence 为 `PASS`。但两臂 Netgen LVS 均为 `netgen_topology` mismatch，setup timing
仍为 severe（before WNS/TNS `-0.498750/-345.517 ns`，after
`-0.249082/-102.761 ns`），strict signoff 两臂均 `FAIL`，def-graph 因 strict gate
被 fail-closed 为 `invalid`。`CORE_UTILIZATION 50->40` 使面积 `173876->217123 µm²`
（`+43247`）和功耗 `0.0507941->0.0510368 W`（`+0.0002427 W`），所以 utility 明确为
`HARMFUL`。7 条 manifest observation 全部是 `INCOMPLETE_EXTERNAL_ONLY`，
`learner_eligible=0`、staging imported `0`、`promotion_attempted=false`，canonical
digest/transition count 不变。该结果是实际 ORFS 负证据和 provenance 链路验证，不是
support rule、capability gain 或 promotion；机器可读摘要见
[`evidence/tehm-orfs-batch0-riscv32i-replay-r1/replay_report.json`](../evidence/tehm-orfs-batch0-riscv32i-replay-r1/replay_report.json)。

因此下一步不是扩大到完整 14-arm，而是先修复 LVS/topology 与 timing contract，重新
生成有效 def-graph，再在至少两条独立 lineage 上取得 non-harmful、完整 oracle 的
support cohort，随后才重跑六项 authority gate。

完整 oracle 还显式要求 `reports/strict_signoff.json` 的聚合 `status=pass`。单独存在
`drc.json`、`lvs.json`、`rcx.json` 等组件报告不能替代同一 bounded checker run 的严格
签核收据；缺失或失败时仍分类为 `INCOMPLETE_EXTERNAL_ONLY`。这修复了“组件报告看似
齐全但没有执行 strict gate”可能穿过 support admission 的 authority 缺口，并已由
`memory/tests/test_batch_lane.py` 回归覆盖。

### 2026-08-27 hierarchical power connectivity repair（diagnostic-only）

对上一节 `sky130hs:riscv32i:u50->u40` 的 Netgen 拓扑错配做了定向复现。错配并非
顶层 VDD/VSS 的排列问题，而是 `6_final.v` 保留的两个 RTL-generated child module
没有 power ports；对 child 内标准单元补脚后，VDD/VSS 变成每个 child 的隐式局部网，
于是 Netgen 看到 `6128 vs 6132` nets。新增
`r2g-skills/signoff-loop/scripts/flow/normalize_power_connectivity.py`，由
`propagate_hierarchical_power_ports()` 在派生 schematic 中闭合 powered module 的
`inout VDD/VSS`，并在父实例上增加 named `.VDD(VDD), .VSS(VSS)` 连接；不改 RTL、ODB
或 layout，且用 receipt 绑定输入/输出 digest。

在兼容的 packaged OpenROAD 26Q3/RCX 工具链上重跑两臂后，
`riscv32i_before_u50`（5878 devices、6128 nets）与 `riscv32i_after_u40`
（5932 devices、6196 nets）均得到 DRC `clean`、LVS `clean`（Netgen
`Circuits match uniquely`）和 RCX `complete`；独立 RTL equivalence 仍为 `PASS`。
这确认原 LVS blocker 是层级电源闭合问题，而不是顶层端口顺序。两臂 strict signoff
仍为 `FAIL`，但原因已收敛为 aggregate timing gate：setup WNS 分别为
`-0.498750 ns` 与 `-0.249082 ns`。`CORE_UTILIZATION 50->40` 的 utility 仍为
`HARMFUL`，因此不构成 support cohort、graph validity 或 capability gain。

该 probe 仅是 diagnostic evidence，`learner_eligible=false`、不进入 staging 或
canonical。默认 `_env.sh` 选择的 OpenROAD/RCX 仍报 database schema `0.139 > 0.98`
（packaged writer/RCX 才是本次可复现组合）；若 writer 失败，`6_final.v` fallback
还缺少 layout 中 route 插入的 5 个 antenna-diode instances。下一步应固定兼容工具链
指纹，先解决 timing contract 并重新生成有效 def-graph；只有取得至少两条独立 lineage
的 non-harmful、完整 strict-oracle support 后，才重新计算六项 authority gate。机器可读
摘要见 `evidence/tehm-orfs-batch0-riscv32i-replay-r1/{replay_report.json,power_connectivity_probe.json}`。

### 2026-08-27 current exact-toolchain support cohort（staging-only）

`run_orfs_add_designs_campaign.py` 现在把自定义 RTL 的真实 top/clock 绑定写入
materialized SDC，并按 `run → equivalence → strict signoff → graph → capture` 顺序
执行；capture 可选的 `require_full_oracle` 会把完整 Batch-0 检查集（含 aggregate
strict signoff、toolchain、artifact、input binding 与 timing contract）重新绑定到
transition，而不是只凭 route/DRC/timing 组件报告判断完整。

在同一 packaged ORFS tree 上，`selector_crc16` 与 `selector_uart16` 的
`ROUTING_CAPACITY_RECOVERY default→0.05` 各完成一条独立 sky130hs pair。四个 arm
均为 equivalence/ORFS/strict signoff/graph 全通过，两个 transition 的 physical
delta 均为零，utility=`NEUTRAL`，因此形成两条完整、非 harmful 的 staging support
lineage。此前 `selector_fifo16 DENSITY_RELIEF 50→40` 虽然 full-oracle 完整，但
utility=`HARMFUL`，被保留为 negative control，不纳入 support。

只读审计脚本
`memory/scripts/audit_orfs_support_cohort.py` 生成了
`evidence/tehm-orfs-current-support-routing-r1/support_cohort_audit.json`：
`obligation_coverage=PASS`、`harmful_rate=PASS`，但 `rollback_verified`、
`registry_verified`、`cross_lineage_te` 与 `conformal_coverage` 仍为
`NOT_ESTABLISHED`。因此决定仍是 `DENY_CANONICAL_IMPORT`，
`promotion_attempted=false`，canonical memory 未改变；这些观察只能作为
evaluation/staging evidence，不能进入 production runtime。

随后针对独立 held-out 候选 `selector_alu16` 做了 80→70、70→60、60→50
的有界 ORFS 探索：80→70 在 placement 因利用率超过 100% 失败，70→60 的 after
臂在 global-route 仍有拥塞，60→50 也未形成完整双臂 oracle。所有失败均留在
scratch 的 external-only 日志中，未写入 support staging。该结果说明当前
routing support 不能直接外推为 density fail→pass transfer；必须先得到完整、
non-harmful 且 source-disjoint 的 held-out pair，才能重新计算 `cross_lineage_te`。

### 2026-08-27 semantic fail→pass contract 与 L4 transfer（evaluation-only）

新增 source-bound `orfs-semantic-oracle-v1`，直接从 materialized
`constraints/config.mk` 计算配置失败/通过，补足“物理 before 完整但语义上失败”的
可执行 witness；物理 ORFS 14 项仍是独立硬门。UART 与 `uart_no_param` 两条 training
lineage 形成 L3 path `causal_path_69ab879fe338f882`，独立 held-out
`selector_fifo16` 的 `70 FAIL→60 PASS` pair 在 `--require-full-oracle` 下通过 L4
batch replay，ledger receipt 可重放且 source DB 不变。density utility 仍为
`HARMFUL`，所以这只是 mechanism-transfer evidence，不能升级为 authority；
`promotion_attempted=false`、canonical memory 和 production runtime 均未改变。
详见 [`evidence/tehm-orfs-l4-transfer-r4/transfer_report.json`](../evidence/tehm-orfs-l4-transfer-r4/transfer_report.json)。

### 2026-08-27 routing semantic fail→pass 与跨 lineage L4（evaluation-only）

`orfs-semantic-oracle-v1` 新增 source-bound `config_presence` 合约，直接从
materialized `constraints/config.mk` 判断 `ROUTING_LAYER_ADJUSTMENT` 是否存在；
因此 default before 的 semantic verdict 为 `FAIL`，routing after 为 `PASS`，而不是
接受调用方传入的布尔值。该 receipt 绑定配置字节摘要、观测值和 pair digest，物理
14-check full oracle 仍独立强制。

复用完整 ORFS 的 `selector_crc16`、`selector_uart16` training arms 构造两条
`ROUTING_CAPACITY_RECOVERY` lineage，controlled path
`causal_path_54ba6f410c35d0b2` 达到 `L3_REPLICATED_EFFECT`。独立 held-out
`selector_arbiter8` 以 `split=heldout`、`learner_eligible=false` 捕获；两侧 14 项
physical oracle 完整、semantic `FAIL→PASS`、utility=`NEUTRAL`。batch transfer
replay 返回 `L4_TRANSFER_SUPPORTED_MECHANISM` 和 `batch_status=PASS`，isolated
ledger receipt replay verified，source DB 未改变。机器可读证据见
[`evidence/tehm-orfs-l4-transfer-routing-r5/transfer_report.json`](../evidence/tehm-orfs-l4-transfer-routing-r5/transfer_report.json)。

这只证明 routing 机制的跨 lineage 可迁移性，不是 authority 或收益结论：
`promotion_attempted=false`，canonical memory、learner 和 production runtime 均未
写入；rollback、registry、obligation、cross-lineage TE、harmful-rate、conformal
coverage 仍需独立 gate receipt，Parametric 仍为 shadow-only。

### 2026-08-08 第二条物理 held-out 外部复核（已完成）

- 新增独立物理 lineage `orfs-heldout-v5:sky130hs:gcd:base3`，位于
  `/data1/zhangdy/tehm-campaigns/orfs-v5-heldout-sky130hs-gcd7/`：sky130hs/gcd 的
  3 个 baseline × 3 个 transformation family，**12/12 ORFS flow 成功**，形成 9 个
  可评估、3 个独立 graph-context digest 的只读 calibration samples。
- samples 只进入校准器，不调用 capture/record/crystallize/lifecycle；合并 SPI held-out
  后，物理 memory count 保持 **66 → 66**。training lineage（23 条）与两条 physical
  held-out lineage disjoint；RTL held-out `req_ack_bug4` 仍单独披露、不计入 physical
  lineage diversity。
- v0.2 校准结果：sky130hs 的 placement/routing 两族保持 `ready`；density-relief
  因 coverage 不足而 fail-closed；sky130hd strict_clean 与 IHP 三族仍
  `insufficient_support`。因此 `parametric_readiness` 仍为
  `DEFERRED_INSUFFICIENT_EVIDENCE`，Parametric View 继续 `NOT_IMPLEMENTED`。
- 当前下一证据优先级是补齐 sky130hs density-relief coverage，并为 sky130hd/IHP
  建立足够的同平台 strict/research support；在 distance、coverage、uncertainty、
  lineage diversity 四项同时通过前，不进入 shadow 或任何 parametric implementation。

每个阶段实现时遵循同样的纪律：honesty gates 先行、确定性测试、与 legacy 严格隔离。

### 2026-08-27 external/staging → rule authority（仍为 shadow）

新增 `tehm.lifecycle.build_external_observation_authority_evidence()`，把选定的
external ORFS receipt 投影为数据库绑定的 authority evidence。投影器会重放 JSONL
hash-chain，以只读一致性快照绑定 staging DB，并要求每个 case 同时满足：
`ELIGIBLE_POSITIVE`、`calibration/heldout`、`learner_eligible=false`、唯一
`record_id → tehm_transitions.provenance_json`、action/delta/verifier/lineage 一致，
以及同 campaign 的 audit membership。utility 只有在 transition 与 external
record 同时明确记录时才形成 `harmful_rate`；conformal 只接受 calibration 的显式
coverage/counts。每个 payload 绑定 observation/staging digest、receipt、transition
和 lineage，防止把文件级 gate map 伪装成 rule authority。

为让 calibration/heldout receipt 真正具备可绑定的 transition witness，
`tehm.batch_lane.import_audit_to_staging()` 现在提供独立的 audit 导入路径：只写
`calibration/heldout/ab` membership、强制 `learner_eligible=0`、跳过 support，并用整批
savepoint 与 canonical digest 守护。之后才可用上述 projector 生成 authority rows；也可使用
`record_rule_authority_from_external_observations()`，由系统自动组合 external rows
与 trial/transfer authority，避免调用方手工拼接 gate。

对应的 operator 入口是
[`scripts/record_external_rule_authority.py`](scripts/record_external_rule_authority.py)：
它只向指定 authority DB 写 rule evidence/receipt ledger，输出仍可为
`NOT_ESTABLISHED`，不导入 transition、不修改 lifecycle，也不触发 promotion。

当 calibration、held-out 或 A/B 证据分散在多个 campaign 时，使用
[`scripts/record_external_rule_authority_batch.py`](scripts/record_external_rule_authority_batch.py)
和 `external-authority-sources-v1` manifest。系统会按 campaign、路径和 case selection
稳定排序，逐 source 重放 hash-chain/staging snapshot，并拒绝重复 case、receipt、
record 或 transition；因此不需要调用方手工合并 evidence rows。严格入口还会把
external record 的 action domain/transformation family 绑定到当前 rule definition。

该接口只产生 `harmful_rate` 与 `conformal_coverage` rows；rollback、registry、
obligation、cross-lineage TE 仍必须由 activation/trial/transfer 的独立 evidence
建立。rows 可以交给 `record_rule_authority()`，但缺失其余 gate 时仍是
`NOT_ESTABLISHED`，不会写 canonical memory、改变 lifecycle 或进入 production。

### 2026-08-28 activation → produced-transition provenance binding（仍不授予晋级）

修复 activation 产出 transition 的 provenance 绑定：`capture_produced_transition()`
现在使用实际持久化的 content-addressed `ActivationRecord.activation_id`，而不再构造
独立的 `act:<rule>:<state>` 临时 ID。因此 produced transition 的
`provenance_json.record_id` 与 `tehm_activations.activation_id` 一一对应，trial/authority
可以从数据库重放 activation → transition 关系；新增回归断言覆盖这一绑定。该修复只
纠正 provenance/lineage 可追溯性，不改变 canonical capture 的 verified 条件、rule
lifecycle、promotion gate 或 production runtime authority。

### 2026-08-28 canonical-import authority selection binding（仍需独立 authority）

进一步收紧 external → staging → canonical 边界：canonical-import authority 现在除了
六项 gate 和 observation/staging/canonical 快照哈希，还必须绑定规范化的
`case_ids` selection digest、campaign、`promotion_attempted=false` 与
`gate_evaluation.all_gates_established=true`。因此同一 observation 文件内改选另一组
case、跨 campaign 重放 authority 或把已发生的 canonical mutation 伪装成预授权都会
fail-closed。external observation hash-chain 也拒绝重复 `case_id`，避免一次 authority
选择导入同一逻辑 case 的多个 receipt。该改动只加强可审计的 authority/provenance
约束；gate evaluation 的逐项 `checks` 还必须与 authority 顶层六门完全一致，不能只
伪造一个顶层 `eligible` 布尔值。六门未建立时仍不允许 canonical import，Parametric 与
production runtime 边界不变。

### 2026-08-28 runtime retrieval lifecycle-row firewall

`retrieval.index.build_index()` 之前只按 SQL 的 `tehm_rule_status.status` 过滤，
因此一条被篡改为 `promoted` 但 `status_version`、provenance 或更新时间损坏的
derived row 仍可能进入检索/activation。现在所有被选中的 `candidate/promoted`
行都会通过 `lifecycle.rule_status.get_status()` 完整重放；任何 malformed row 都被
记录为 rejected 并从 index 排除。这样 runtime 仍只消费经 authority 写入且可重放的
`promoted` 状态，不能靠直接修改状态列绕过 lifecycle authority；该修复不新增 gate、
不写 canonical memory，也不改变 Parametric shadow-only 边界。

### 2026-08-28 lifecycle consumers revalidate authority

runtime consultation 在把 retrieval receipt 的 rule ID 解析回定义时，现在再次使用
`promoted` lifecycle filter；如果 retrieval 与 definition lookup 之间发生 demotion 或
状态损坏，旧 receipt 不会被转成 live strategy。ORFS candidate/promoted trial lane
也先用同一 lifecycle reader 规范化 status/version，再生成 trial identity 和 lifecycle
decision。Capability-gap detector 对 promoted rule family 的覆盖判断同样不再信任裸
`status` 列，损坏状态不能抑制新的 gap receipt。这些都是 derived-state replay 修复，
不授予新 authority，不写 canonical memory，并保持 Parametric shadow-only。
### 2026-08-30 TEHM toolchain manifest lock

R2G discovery and the TEHM ORFS runners now have one explicit replay boundary:
`scripts/record_orfs_toolchain_manifest.py`.  It records a local,
content-addressed lock containing the ORFS `flow/Makefile` and git identity,
OpenROAD/Yosys path/version/SHA256, the Yosys capability probe, and PDK marker
digests.  The lock is metadata only—no EDA binary is committed to the
repository—and a clean ORFS checkout plus a tree-packaged or `R2G_PREFIX`-owned
tool pair is required for a production lock.

Record and verify a selected tree as follows:

```bash
python3 memory/scripts/record_orfs_toolchain_manifest.py record \
  --orfs-root "$ORFS_ROOT" \
  --prefix "$R2G_PREFIX" \
  --openroad "$OPENROAD_EXE" \
  --yosys "$YOSYS_EXE" \
  --pdk-root "$PDK_ROOT" \
  --output "$R2G_PREFIX/tehm-orfs-toolchain-manifest.json"
python3 memory/scripts/record_orfs_toolchain_manifest.py check \
  --manifest "$R2G_PREFIX/tehm-orfs-toolchain-manifest.json"
```

Pass the resulting path to `run_orfs_batch0.py` or
`run_orfs_diversity_campaign.py` with `--toolchain-manifest` (or
`R2G_TOOLCHAIN_MANIFEST`).  Freeze and every later phase replays the same lock
before starting an EDA process; a changed tree, executable, PDK marker, or
capability probe is blocked.  `--allow-external` and `--allow-dirty` are for
operator diagnostics only and do not satisfy a production internal-toolchain
gate.  On this host the current ORFS tree is dirty and lacks a matching
tree-packaged OpenROAD, so a production manifest and the full ORFS batch remain
intentionally blocked until that installation is repaired.

`bootstrap.sh --dry-run --prefix /data1/zhangdy/Tools/tehm-toolchain` now reports
the existing `/usr/bin/openroad` as a missing core dependency rather than
silently calling it provisioned. For a complete user-owned setup, review the
plan and then run `bootstrap.sh --hermetic --yes --prefix
/data1/zhangdy/Tools/tehm-toolchain`; hermetic mode requires all selected
OpenROAD/Yosys/frontend/PDK/graph paths to live outside `/usr` and `/opt`, and
installs missing tiers into that prefix. This was not run here because it
downloads/builds large external packages and requires explicit operator
approval. The current inventory has no OpenROAD binary under
`/data1/zhangdy/Tools`; it has Yosys 0.65 at `Tools/yosys/yosys` and at
`Tools/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys` (different SHA256
builds).

### 2026-08-30 hermetic core 安装与解析修复

按上面的入口实际完成了个人目录的 core 安装：Miniconda 位于
`/data1/zhangdy/Tools/tehm-toolchain/miniconda3`，OpenROAD 位于其 `eda` 环境
（版本标识 `f12e2f474102bfb875eeee57fb610d7d7de17770`，SHA256
`19e18e5ae901f6f8c12e9367d8999fd88789f1531ac2bcb711071a935742fe88`）。Yosys 选择
自有 ORFS 树中与 flow 配套的 `tools/install/yosys/bin/yosys`（Yosys 0.65，SHA256
`951defe968ce33f4265b733f87cfc8c0b14faad02a4985a996d86c2bf08119ba`）；同一 conda
环境中的 Yosys 0.38 缺少当前 flow 所需的 `read_liberty -unit_delay`，已由
preflight 明确拒绝，不能仅因路径在个人目录就当作兼容工具。

四份 `_env.sh` 现在保持字节一致，并在 `R2G_HERMETIC=1` 时拒绝 `/usr`、`/opt`、
`/bin`、`/sbin` 的回退；writer 也会清除历史诊断 pin，避免旧的 `/usr/bin/openroad`
污染新安装。期间发现并修复了 `ensure_conda()` 捕获 Miniconda stdout 的 bug，增加了
脚本回归覆盖。当前 fresh-shell pin 已写入 `R2G_HERMETIC=1`、用户 OpenROAD 和
ORFS-packaged Yosys；Icarus/VVP、PDK、graph 仍未安装，因此完整 hermetic ORFS
strict flow 尚未就绪。

当前 preflight 对该组合报告 `status=bound_internal`、`compatibility=mixed_internal`：
OpenROAD 来自个人 conda prefix，Yosys 来自 ORFS 配套目录。当前 ORFS checkout 仍有
本地未提交改动，所以生成的
`/data1/zhangdy/Tools/tehm-toolchain/tehm-orfs-toolchain-manifest-diagnostic.json`
（digest=`36b91777fe8d5a369e0f6c9630fb57dc4163195f6a9c646d8c118031d0ed3ee6`）仅是
`--allow-dirty` diagnostic lock（可重放但不能作为 production freeze）。必须先
获得 clean、与该 OpenROAD 匹配的 ORFS source freeze，并补齐剩余 oracle 工具后，才可
运行单设计 strict smoke 和后续真实 support cohort；本轮没有启动完整 batch，也没有
改变 canonical memory、rule authority 或 production runtime。

### 2026-08-30 direct Tools toolchain migration

按操作要求，工具链已从“conda 优先”迁移为 `/data1/zhangdy/Tools/tehm-toolchain`
下的直接用户目录。当前解析和 `check_env.sh` 在完全没有激活 conda、且
`R2G_HERMETIC=1` 时得到：

```text
openroad/bin/openroad.bin             v2.0-17598-ga008522d8 (openroad wrapper alongside)
yosys/bin/yosys                       Yosys 0.65 (flow-matched)
oss-cad-suite/bin/{iverilog,vvp}      Icarus 13.0
oss-cad-suite/bin/verilator            Verilator 5.035
klayout/bin/klayout.bin               KLayout 0.29.12 (klayout wrapper alongside)
magic/bin/magic, netgen/bin/netgen    Magic 8.3.105 / Netgen 1.5.133
sta/bin/sta                           OpenSTA 2.6.0
pdks/sky130A                          full copied sky130A tree (1.1G)
```

OpenROAD 的 wrapper 同时携带 deb 中的 OR-Tools libraries；OSS CAD Suite 使用其
自己的 loader/lib，KLayout、Magic、Netgen 和 OpenSTA 的可执行文件也都已放在该
prefix。系统的 glibc/Qt/Tcl 等基础运行库仍属于操作系统 ABI，不会把整个 Linux
用户空间重复复制一份。运行时不再从 `/usr/bin`、`/opt` 或 `/data2` 解析 EDA
可执行文件和 PDK。

核心锁已记录在
`/data1/zhangdy/Tools/tehm-toolchain/tehm-orfs-toolchain-manifest-direct.json`
（`bound_internal`，digest=`9b5f179b01bebde6da87f6443729f2589d8fab218fc63478628c2286e1940b1c`）。
全量工具、版本、SHA256 与 PDK marker 的盘点在
`/data1/zhangdy/Tools/tehm-toolchain/tehm-direct-toolchain-inventory.json`
（inventory SHA256=`f835903adb766de49a72bb0686ad140afbaec491586381899c763c93ebbf8be2`）。
该锁绑定 clean worktree `/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean`、
`openroad-matched` 与 flow-matched Yosys，`allow_dirty=false`，且已通过
`record_orfs_toolchain_manifest.py check`。原始 `/data1/zhangdy/Tools/OpenROAD-flow-scripts`
仍保留为脏的 operator 工作区，不参与正式 replay；正式 ORFS batch 仍须先通过单设计
strict smoke 和后续 oracle gate。

`bootstrap.sh --direct --dry-run --prefix /data1/zhangdy/Tools/tehm-toolchain` 现在是
不调用 conda 的 fail-closed 入口；缺少 direct artifact 时只会报告缺口。旧的
`miniconda3` 目录已不存在，三份 skill 的 `references/env.local.sh` 均已重写为上述
direct paths，旧的 env backup 也已清理。该迁移只改变工具链解析和 provenance，不写
canonical memory、authority 或 production runtime。

### 2026-08-30 clean ORFS root resolver 与单设计兼容性检查

为避免个人目录之外的脏 ORFS checkout 被隐式调用，已从 commit
`eb14d768b6c34cf4f8c5177f3531422b94cf2544` 建立 clean worktree
`/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean`。四份 `scripts/flow/_env.sh` 现
在路径派生前恢复调用方的 `ORFS_ROOT`，并在加载 ORFS `env.sh` 后重新固定
`FLOW_HOME=$ORFS_ROOT/flow`；新增 stale env-file 回归，bootstrap 定向测试为
`25 passed`。

使用 direct bundle 对 clean `gcd` 运行有界 ORFS smoke 时，synth 通过，但 floorplan
阶段 direct OpenROAD 在 `rsz::Resizer::tieLocation` / `repairTieFanout` 触发
`SIGSEGV`，尚未进入 signoff/equivalence/graph/capture。该结果只说明当前 direct
OpenROAD 与该 ORFS source/PDK 组合尚未兼容，不是 learner、canonical memory 或
authority evidence；full ORFS batch 继续保持阻塞。下一步必须先获得匹配的 OpenROAD，
重新生成 production manifest，再通过 single-design strict smoke 后才扩大批量。

### 2026-08-30 online event payload 类型闭环

B1 online event log 现在在写入和 replay 两端都要求 payload 是 JSON object。空串、
malformed JSON、数组或标量均 fail-closed，避免损坏 payload 被当作可重放的 shadow
event；该修复不改变 canonical、learner admission、authority 或 production runtime。

### 2026-08-30 causal query plan 类型校验

A3 matcher 现在会拒绝非法的机制签名、causal path features、effect 列表和 prior
action digests，避免错误查询静默退化为无约束 recall；结果只影响 evaluation/shadow
causal retrieval，不改变 production authority。

### 2026-08-30 causal matcher support witness 防火墙

A3 causal matcher 现在不会把损坏的 `support_json` 或非 mapping 的
`mechanism_signatures` 降级为空证据后继续匹配；两类输入分别返回明确的 fail-closed
reason。该修复只影响 evaluation/shadow causal retrieval，不改变 production runtime
或 promotion authority。

### 2026-08-30 causal transition facts 的 JSON 防火墙

因果 mechanism extractor 已不再把 malformed 或非对象的 transition JSON 默认为 `{}`。
`action_json`、`observation_delta_json`、`verifier_json` 现在在 causal node/edge 写入
前严格校验；state snapshot/manifest 只有明确的历史 `NULL` 可兼容，存在的值必须是
对象 JSON。新增 malformed payload 回归，异常时不会留下 causal shadow rows，也不会
改变 canonical、authority 或 production runtime。

### 2026-08-30 direct graph runtime 补齐

按 direct bundle 规则，图阶段运行时也已安装到
`/data1/zhangdy/Tools/tehm-toolchain/pyenvs/r2g-graph`：torch `2.13.0+cpu`、
torch_geometric `2.8.0.post1`、pandas `3.0.5`。宿主机没有 `python3-venv`，因此没有
退回 Conda；安装器改为复用 OSS CAD Suite 自带 Python/pip，把依赖放入隔离的
`site-packages`，并生成携带 `PYTHONPATH` 的 wrapper。wrapper 已通过三项 import
检查，三个 consumer `env.local.sh` 均已 pin `R2G_GRAPH_PYTHON`。首次失败的 venv
残留链接已清理，原始 OSS CAD Suite `libexec/python3.11` 已校验恢复；当前
`/data1/zhangdy/Tools` 及个人目录没有实际 Miniconda/Conda 安装（测试缓存中的
临时 fixture 不参与 resolver）。

这一步仅闭合 direct toolchain 的 graph 依赖，不改变 ORFS source/binary compatibility
结论；OpenROAD 与 clean ORFS 的 floorplan SIGSEGV 仍是负兼容性证据，production
 manifest 和 full ORFS batch 继续等待匹配的 OpenROAD。

### 2026-08-30 shared causal source witness 与 replication provenance 防火墙

复核 L2/L3 authority、replication、held-out transfer、causal retrieval 与 online
novelty 时发现，各路径曾各自解析 `source_transitions_json`，且 replication 会把
数字 witness 强制转换为字符串，造成同一份 causal path 在不同边界得到不同语义。
现统一使用 `causal.witness.parse_source_transition_ids()`：输入必须是非空 JSON
list，元素必须是非空字符串且不得重复；malformed、空值、数字和重复 witness 均
fail-closed，禁止通过类型强制伪造 source identity。replication 的
`provenance_json` 也要求 mapping；声明的 `run_id`/`run_tag` 必须是非空字符串，任一
坏 provenance 行都会使 run witness 不完整，不能被其他有效行掩盖，并保留明确的
`requires_distinct_run_witnesses` 失败原因。

新增 shared-parser、numeric/duplicate source、malformed provenance 回归；本次只收紧
shadow/evaluation 证据入口，不改变 canonical memory、lifecycle、六项 promotion
gate 或 production runtime。

### 2026-08-30 causal transition facts typed semantic replay 防火墙

在 shared source witness 之后继续复核 A2/A3 replay，发现仅验证 transition JSON 为
object 仍不足以保证因果解释：`Action`、`ObservationDelta` 和 `VerifierSnapshot`
的宽松 `from_dict()` 可能把缺失/错类型字段转换成默认值或字符串，且
`action_domain`、`outcome`、`primary_effect_key` 这些重复/派生列可被直接 SQL 篡改。
现由 `causal.mechanism.load_transition_facts()` 在生成任何 causal node/edge 前执行
无 coercion 的 typed contract：action identity/payload、delta enum/list/object、verifier
enum/container 必须满足 canonical schema；同时重算并核对 content-addressed
`transition_id`、`action_domain`、`outcome` 和（存在时）`primary_effect_key`。任何
不一致都 fail-closed，不留下 causal shadow rows。

causal retrieval 的 canonical utility/risk replay 已改为复用同一 loader，避免质量评分
路径使用另一套宽松解析。新增 8 个语义错类型与派生列篡改回归；全量 `memory/tests`
为 `687 passed`。本轮仍只收紧 shadow/evaluation 证据入口，不改变 canonical 写入、
lifecycle、六项 promotion gate 或 production runtime。

### 2026-08-30 causal learner witness API 输入闭环

继续检查 shared witness 的公开调用面时发现，`learner_edge_transition_coverage()`
仍会把调用方传入的数字或重复 source ID 强制为字符串并去重，可能绕过
`parse_source_transition_ids()` 的严格契约。现要求该 API 的 source 输入必须是非空
list/tuple、非空字符串且无重复；任一错类型、空值或重复输入直接返回空覆盖，不会
形成 learner support。新增数字/重复 source 回归；该修复只收紧 causal shadow
evidence firewall，不改变 canonical、lifecycle、promotion gate 或 production runtime。

### 2026-08-30 canonical capture typed ingestion 与迁移兼容

继续沿 canonical evidence → causal replay 的依赖顺序审计 capture 入口，发现
`Action.from_dict()`、`ObservationDelta.from_dict()` 与 `VerifierSnapshot.from_dict()`
会把数字、列表或缺失字段隐式转换成合法对象；`ExecutionRecord.from_dict()` 也可能把
pair-list 转成 mapping。现在 capture/staging 在任何 artifact、canonical row 或 view
写入前执行严格 typed ingestion：record 的 state/payload section 必须是 object；action
的 domain/family/payload、delta 的 failure/list/object 字段、verifier 的 enum/list/
mapping 字段均拒绝错型；action 必需 key 和 delta 的 `original_failure` 缺失直接
fail-closed。早期 verifier 记录省略 `verdict`/`oracle_type`/`confidence_tier` 的既有
格式继续迁移为 `UNKNOWN`/`H` 默认，因此不会破坏历史 canonical replay。

新增 malformed section、错型字段和缺失 required key 回归；capture 会在副作用之前
验证，保证坏证据不会留下 artifact/canonical/shadow projection。定向 capture/causal
回归为 `72 passed`；随后完整 `memory/tests` 为 `708 passed, 1 warning`；该修复只收紧 evidence ingestion，不改变 canonical authority、
Parametric shadow-only、六项 promotion gate 或 production runtime。

### 2026-08-30 direct ORFS toolchain 完成匹配构建与真实 smoke

为消除旧 direct OpenROAD 与 ORFS source 不匹配的问题，已从 clean ORFS checkout
`/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean` 的 `tools/OpenROAD` gitlink
`49bd051a10f0dd5bb89eba9acf668e8362b883d8` 源码构建 OpenROAD，使用个人 GCC 12、CMake
3.31.9、SWIG 4.3.1、OR-Tools 9.14.6206、Boost 1.87、fmt/spdlog/yaml-cpp/Lemon，
安装到 `/data1/zhangdy/Tools/tehm-toolchain/openroad-matched`。可执行文件报告
`26Q2-1846-g49bd051a10`；`openroad` 目录现指向该匹配安装，旧发行包仅保留为明确命名的
`openroad-legacy-v2.0-17598`，resolver 不会选择它。Yosys 0.65、Icarus/VVP、Verilator、
KLayout、Magic、Netgen、OpenSTA、sky130A PDK 和 graph runtime 仍全部位于同一 direct
toolchain root；`miniconda3` 已删除。

四份 `_env.sh` 的 direct 候选顺序已把 `openroad-matched` 置于旧路径之前，并保留
`openroad`/`yosys`/`sta` 的个人 pin。新的 manifest
`/data1/zhangdy/Tools/tehm-toolchain/tehm-orfs-toolchain-manifest-direct.json` 已
`record → check` 通过，`binding_status=bound_internal`，digest=
`d106e9c8d15b28ef4ec4357cba93a10663726840f0b9af609eda452eefed961e`。

使用该 direct manifest 和 clean ORFS，对最小 `sky130hd` arbiter 进行了真实全阶段
`synth → floorplan → place → cts → route → finish`，全部成功；KLayout DRC 为 0
violations，OpenRCX 独立抽取 16 nets/7297 bytes SPEF 成功。LVS 返回真实 netlist
mismatch，且 signoff wrapper 检测到其修正 CDL 导致布局 digest 变化，因此该 run 只能作为
工具链/流程可执行性证据，不能进入 strict-clean、learner 或 authority。另已修复
`run_rcx.sh` 在切换输出目录后用相对路径加载 helper 的脚本 bug。下一步仍需用真实、
source-disjoint 设计形成完整 ORFS/strict/equivalence/graph/capture lineage，不能仅凭
本次 smoke 建立六项 promotion gate。

### 2026-08-30 effective routing hook 与 direct ORFS 重跑

继续按 causal online capability 计划推进时，发现 `sky130hs/fastroute.tcl` 把
`ROUTING_LAYER_ADJUSTMENT` 固定为 `0.2`，此前 routing pair 虽然 config 不同，实际
执行路径却相同。现已在 clean ORFS worktree 提交 `0c162cf5f`：无配置时保持 `0.2`
默认，有配置时由 Tcl 直接消费 `$::env(ROUTING_LAYER_ADJUSTMENT)`；capture
preflight 从 `NO_OP/INAPPLICABLE` 变为 `EFFECTIVE`。direct toolchain manifest
重新 `record → check` 通过，digest=`9b5f179b01bebde6da87f6443729f2589d8fab218fc63478628c2286e1940b1c`；campaign source freeze
digest=`a04e7ec20894a77b629798834b68f66863dc296dc7735b85df9b4e2c56278ca4`。
该源修复可由 `tools/patch_sky130hs_fastroute.py` 对任意 clean ORFS checkout
幂等重放，并通过 `--check` 验证 hook 是否真正消费 knob。

为避免旧 GDS/receipt 被复用，修复 `run_orfs_diversity_campaign.py` 使
`R2G_FORCE_CLEAN_RUN=1` 在 reusable-success 快速路径之前生效，并对 FIFO before/after
各生成新的 backend `RUN_*`（每个 ORFS flow 完整到 finish，约 285/319 秒）。新的
KLayout `.lyt` 已生成绝对 LEF 路径，Magic 8.3.682 与 Netgen 1.5.323 均能工作；两臂
LVS 均 clean，但 sky130hd sibling DRC deck 在相同位置报告 2 个 `m3.2` 违例，故
strict signoff、full oracle 和 learner eligibility 仍为 false。FIFO 的 PPA utility
为 `HARMFUL`（power 增加约 `4.5e-05 W`），不能解读为机制收益；UART 旧 arm 仍有
`WNS=-0.0203174 ns`。capture 使用新的 staging DB 保留不可变旧证据，新的 routing
receipt 为 `EFFECTIVE`，但当前两个 pair 仍缺 timing/DRC/strict/graph 等门。

另外修正 batch-lane toolchain fingerprint replay：若 receipt 含
`toolchain_root`，重放现在将其纳入 fingerprint（无该字段的旧 external receipt
保持兼容）。本轮仍没有 canonical memory mutation、rule authority promotion 或
production runtime import；下一步应先解决 sky130hs DRC 的 `m3.2` 真实/平台 deck
问题，再为至少两个 source-disjoint lineage 建立完整 oracle、graph 与 A/B efficacy
证据。

### 2026-08-30 direct diversity cohort：参数化、完整执行与 fail-closed capture

为避免把过高利用率造成的 placement overflow 误当成记忆机制结果，
`run_orfs_diversity_campaign.py` 现在支持显式的
`--density-before/--density-after` 与 `--routing-before/--routing-after` 参数，并把
四个值写入 manifest 的 `parameterization`。在 clean ORFS
`/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean` 与 direct toolchain manifest
（fingerprint=`99a262e2…`）上重新冻结 campaign
`/data1/zhangdy/tehm-campaigns/orfs-diversity-direct-v2-density50`，参数为 density
`50→40`、routing `0.55→0.15`；新增回归通过，默认参数保持向后兼容。

该 campaign 的 16 个 before/after arm 均完成并写入 receipt：10 个 `SUCCESS`、5 个
`FLOW_FAILURE`（AES density 两臂、GCD routing 两臂、AES routing 两臂）、1 个
`TIMEOUT`（AES routing after）。成功 arm 的 strict signoff 进一步证明：sky130hs/gcd
两臂 DRC/LVS clean 但 timing 为 `pass_with_caveats`，gf180 八臂 DRC/LVS 在当前平台
能力表中为 skipped；因此没有任何 pair 达到 strict-clean learner 条件。def-graph
只为 5 个有成功 before DEF 的 transition 建立 research-tier context，3 个失败/超时
source arm 明确 `not_available`。

capture 只写 staging DB；8 个 pair 均为 `dataset_split=calibration`、
`learner_eligible=false`，campaign retrieval metrics 的 `applicable/retrieved` 均为
0。strict signoff 后再次 capture 时，旧 verifier 不被覆盖而是保留在 staging 的不可变
历史（审计看到 13 条 incomplete transition；当前 manifest 仍绑定 8 个 pair），证明
recapture 也不能把旧证据重写。这证明 direct ORFS 执行、strict signoff、graph attachment
与 admission firewall 已经串联，但当前 cohort 仍是诊断/校准证据，不是 canonical memory、Parametric
promotion 或 production runtime 输入。下一步应先补齐平台级 DRC/LVS 能力与 timing-clean
设计，再以新的 source-disjoint lineage 重跑；不得把上述 flow failure 或 timeout
重标为 harmful utility，也不得放宽 promotion gate。
需要特别注明：本 diversity runner 的 lineage firewall 已绑定在 manifest，但尚未像
add-designs pipeline 那样生成独立 `source_freeze.json` 并在每个后续 phase 重验证；因此
本轮的 “source-disjoint” 仍是 campaign-level 诊断保证，不能替代 authority 所需的
source-freeze/hash-chain。下一轮应先补齐该 freeze seam，或改用已具备 freeze 校验的
campaign runner，再生成 learner/authority 候选。

### 2026-08-30 diversity source-freeze seam

已将上述缺口补入 `run_orfs_diversity_campaign.py`。`prepare` 现在会在任何 case
materialization 之前创建独立的 `source_freeze.json`；显式 `--phase freeze` 还可以为
已有的诊断 campaign 绑定 freeze，而不重跑 EDA。freeze 绑定固定 diversity matrix、四个
参数化 knob、SPI held-out identity、ORFS design/SDC/RTL/platform inputs、TEHM 与
signoff/graph 解释代码、repo/ORFS git revision 与 working-tree diff digest，以及可选的
content-addressed toolchain fingerprint。

`heldout`、`run`、`capture`、`graph`、`ab`、`predict` 和 `report` 在读取 manifest 后均
先重算这些 digest；freeze 缺失、篡改、源码/ORFS 输入漂移、toolchain fingerprint 漂移或
manifest 参数不一致都会 fail-closed。没有声明 toolchain manifest 的微型 fixture 只会
记录 `toolchain.status=not_checked`，但仍不能绕过实际 `run` 阶段的工具链预检。定向
diversity 回归为 `10 passed`；这一步只加强 source/evidence replay boundary，不创建
canonical memory、authority promotion 或 production runtime 写入。现有
`orfs-diversity-direct-v2-density50` 仍需在最终提交后用 `--phase freeze` 迁移并通过
`report` replay，之后才能把新 cohort 作为可审计的后续输入。

在提交 `4b2ff7e` 后已完成该迁移：原 campaign 未重跑任何 EDA，只生成并绑定
`source_freeze.json`（digest=`8ddb4d2881d25fc0922dfd1608ce71c16b7004d49a4821940dc8c77812571843`，
toolchain=`bound_internal`），随后 `--phase report` replay 返回 0。原有 8 个 capture
和 13 条 staging transition 未被覆盖；这只证明历史诊断 evidence 可按当前源码/ORFS/
toolchain 重放，仍不改变其 `learner_eligible=false`、六项 promotion gate 未建立及
Parametric shadow-only 状态。

### 2026-08-30 support cohort membership replay firewall

复核只读 `audit_orfs_support_cohort.py` 时发现，审计器此前只信任 campaign manifest
中的 `dataset_split` 与 `learner_eligible`，没有重放 staging DB 的
`tehm_dataset_membership`。这样一个完整的 calibration/held-out row 被误传为 support
root 时，可能被计入 support lineage。现在 support audit 对每条 transition 要求：

* staging 中恰好一条 membership，且 `validate_membership_row()` 通过；
* 持久化 membership 必须是 `split=training` 且严格 `learner_eligible=1`；
* manifest 的 split/learner flag 必须与 DB row 完全一致；
* 缺失、重复、弱类型或矛盾 membership 会记录
  `support_firewall_errors`，强制 `DENY_CANONICAL_IMPORT`，不会只被当作普通
  `NOT_ESTABLISHED`。

审计器的所有 staging/transfer DB 读取也改用 SQLite `mode=ro&immutable=1`，保证只读
replay 不会创建 WAL/SHM sidecar。新增 held-out、manifest mismatch、字符串布尔和合法
training membership 回归；相关 batch/rule-authority/causal-transfer 回归共 `67 passed`。
该修复只收紧 support authority 的证据重放，不写 canonical memory、不改变六项 gate
阈值、不触发 promotion，也不改变 Parametric shadow-only 或 production runtime 边界。

### 2026-08-30 support cohort source-freeze replay firewall

support audit 进一步不再只重放 membership：每个被选为 support root 的 campaign 必须
绑定可读取的 `source_freeze.json`，且 manifest 中的文件 SHA256 与 payload 内部
`freeze_digest` 同时通过。审计器还要求 freeze 带有非空的 source-tree/input digest；
若 freeze 的 `request` 提供 ORFS root 或 toolchain manifest，也必须与 campaign manifest
逐项一致。缺失、篡改、自洽哈希不完整或 manifest 绑定漂移都会记录
`source_freeze_errors`，令该 campaign 的 transition 不具备 `support_eligible` 资格并
保持 `DENY_CANONICAL_IMPORT`。

该层只重放历史 freeze envelope，不要求旧 external ORFS 源树仍挂载，因此不会把“当前
无法复跑旧树”误写成新执行结果；真正的 source/input digest 生成与每阶段 drift 检查仍由
campaign runner 负责。新增 missing/tampered freeze 回归，现有 valid training、held-out、
membership mismatch 与弱类型 learner flag 回归保持通过。此修复继续只强化
external→staging→canonical 防火墙，不写 canonical/authority/runtime，也不改变六项
promotion gate 或 Parametric shadow-only 边界。

### 2026-08-30 online learner verified-transition admission

按设计 4.1/4.8，`observe_transition()` 现在在 learner lane 先重放
`load_transition_facts()`，再要求 transition 具备明确的 `PASS/FAIL` verdict、
`oracle_complete=true`，且 oracle 不能是 `UNKNOWN`、`COMPILE` 或 `LINT`。若存在
expanded `full_oracle`，其 `before`/`after` arm 也必须逐项 `complete=true`。只有该
verified-execution predicate 和同 campaign 的 `training ∧ learner_eligible=1`
membership 同时成立，才允许生成 learner online event、causal fragment 与
consolidation preview。

training membership 不能再单独把 partial/compile-only receipt 升格为 memory；失败会在
任何 derived write 前 fail-closed。held-out/calibration 仍保留 audit-only 的
`NOT_LEARNER_ELIGIBLE` 路径，不因该前置条件被误当作 learner evidence。online 单测已
改为显式 deterministic complete-oracle fixture，并新增 incomplete/compile-only 拒绝回归；
该 fixture 只验证机制，不构成真实 RTL/ORFS 结果。

### 2026-08-30 learner event writer replay seam

verified-execution gate 已从 `observe_transition()` 提升为所有 learner-derived event
写入口的共同约束。`append_memory_event(..., learner_eligible=true)` 对
`transition`、`causal_fragment`、`activation` 反向解析出的 canonical transition
重放完整 executable oracle；`verify_event_chain()` 回放时再次执行同一检查。因而
partial、compile-only、unknown 或内容已损坏的 source 不能通过直接事件写入或后置 SQL
修改进入 learner chain。谓词集中在 `tehm.evolution.verification`，不改变
canonical/authority/runtime 的 shadow-only 边界。

同一谓词也已接入 batch lane：support staging import、authority 的 staging witness
replay 与 canonical import 在各自 savepoint 内重放完整 executable oracle。伪造外部
`classification`/membership/gate 输入不能把 incomplete transition 变成 support witness；
失败会回滚本次导入，不写 canonical memory。

crystallization 同样在 learner 分组前重放完整 verified execution；不完整或
compile-only transition 只留在 raw/preflight audit，不会生成 candidate rule。旧 R2G
诊断记录因此不会阻塞审计，也不会凭默认 membership 获得 learner rule support。

### 2026-08-30 direct learner-derived seam closure

继续审计发现，直接调用 activation utility、consolidation trigger、rule revision、
replication 或 held-out transfer 仍可能绕过 online manager。现已统一收紧：

* `update_rule_utility(..., learner_eligible=true)` 必须绑定 activation 及其
  produced canonical transition，并重放完整 executable oracle；无 activation 或不完整
  transition 都不会更新 rule utility；
* trigger/revision 的独立入口分别重放 transition/evidence refs，不能仅凭 membership、
  event chain 或 caller boolean 建立 consolidation/revision 证据；
* replication、causal authority 与 held-out transfer 只把 complete verified execution
  计入 L2/L3/L4 支持，partial/compile-only/unknown 仅保留 shadow/audit 诊断。

这些改动没有提升任何 gate 分数，也没有写入 production runtime；它们只把已有
`training ∧ learner_eligible=1` 分区约束与 verified-execution 约束统一到所有可直接调用
的 learner-derived 入口。新增回归只验证 fail-closed 边界，不构成真实 ORFS/RTL 经验结果。

### 2026-08-30 action32 source-disjoint calibration firewall

为修复 calibration 支持泄漏，`build_orfs_calibration_evidence.py` 现在从只读 authority
快照的 `tehm_states.lineage_id` 重放支持 lineage；任何 calibration sample 与 authority
lineage 重叠都会在写出 external observation 之前 fail-closed。旧的
`orfs-action32-calibration-v1` 快照由 v113–v115 同源构成，重新运行明确拒绝三条重叠
lineage，不再把零距离自匹配误报为有效 calibration。

随后用全新 v117/v118/v119 sky130hs action32 训练 lineage 建立独立 authority snapshot
(`/data1/zhangdy/tehm-campaigns/orfs-action32-support-v1/authority_snapshot/tehm.sqlite`，
3 transitions/3 physical effects，`integrity_check=ok`)，三条 pair 均完成 ORFS、等价性、
strict signoff、graph 与 capture，utility 均为 `PARETO_SAFE`。用不重叠的 v113/v114/v115
样本和该 snapshot 重新计算 `/data1/zhangdy/tehm-campaigns/orfs-action32-calibration-v2/`
得到 `ready_for_shadow`：3 个 lineage、12 个 metric comparisons、harmful rate `0.0`、
positive utility rate `1.0`，source-disjoint overlap `[]`。materialized policy 的
`max_distance=1.7`、`min_unique_contexts=3`、`canonical_memory_mutation=none`、
`promotion_eligible=false`，因此它只是可审计的 shadow predictor。

对独立 v116 held-out row 的只读预测距离为 `1.420285`，会进入该 shadow policy 的 OOD
范围，但真实 held-out utility 为 `HARMFUL`；该结果正是继续保留 held-out 防火墙和六项
promotion gate 的理由，不能回灌 support、canonical memory 或 production runtime。

### 2026-08-30 authority/trial/L2 execution witness closure

继续沿 learner-derived seam 追踪 authority 入口，补上三处此前仍可由直接调用绕开的
execution witness 检查：external authority 投影在绑定 staging transition 后重放完整
executable oracle；trial authority 从 activation 的 produced transition 投影 utility
前先重放该 transition；causal controlled-pair 只有在 control/treatment 两侧都满足
Verified Execution 时才建立 L2 edge。partial、compile-only、unknown 或不完整 full
oracle 现在最多留下失败的审计 receipt，不会进入 harmful/conformal、A/B utility、L2
controlled support 或 cross-lineage rule binding。定向 authority/causal 回归 62 passed，
扩展 activation/online/transfer/authority/L2 回归 127 passed；本轮没有 canonical
promotion 或 production runtime mutation。

### 2026-08-30 action32 held-out safety follow-up

在同一 direct toolchain/source freeze 下又完成了两条不重叠的 held-out campaign：v120
和 v121。v120 的 ORFS、equivalence、strict signoff、graph、capture 均完整，真实 delta
为 area `-269.15 um2`、power `-1.8e-05 W`、WNS `+0.00055 ns`，utility 为
`PARETO_SAFE`；v121 的物理流程和 graph 完成，但 `oracle_complete=false`，真实 delta
为 area `-452.63 um2`、WNS `-0.00167 ns`，只能保留为不完整审计证据，不能进入 safety
分母。结合 v116，当前完整 held-out 分母为 2 条，其中 1 条 `HARMFUL`、1 条
`PARETO_SAFE`，harmful rate 为 `0.5`，所以 action32 policy 不具备安全 promotion
资格。

用 v117–v119 support snapshot 对三条 held-out 做只读 shadow replay，nearest distance
分别为 v116 `1.420285`、v120 `1.478473`、v121 `0.556491`，均低于 policy 的
`max_distance=1.7`；但 v116 的实际 WNS 回退和 v121 的 oracle 不完整说明“进入 OOD
范围”不等于“可晋级”。三条结果均未写入 canonical memory、authority 或 production
runtime，当前六项 gate 仍保持未建立/拒绝状态。

### 2026-08-30 utility contract hard-oracle firewall

为避免“generic Pareto 安全”覆盖 typed utility contract 的硬约束，
`run_calibration_expansion.py` 现在在 prepare 时把 contract id 固定进 action identity、
source freeze 和每个 pair manifest。contract cohort 的独立 equivalence receipt 由固定
Yosys/source-identity oracle 生成；缺少 `equivalence.json`、DRC/LVS/timing receipt 或
任一报告非 PASS 时，样本只保留为 external audit（`ABSTAINED`/`FAIL`），不会进入
calibration support。只有逐样本 `evaluate_observed_contract` 为 `PASS` 的 support 才能
进入 staging-only replay；held-out 的任一 FAIL/ABSTAIN 会关闭 grouped shadow admission，
即使 generic Pareto 报告为 `ready_for_shadow` 也不能 materialize policy。所有路径仍保持
`canonical_memory_mutation=none`、`promotion_eligible=false`。

此前 v122–v127 物理 campaign 的 ORFS/strict reports 生成于该 receipt 接入前，不能回填为
contract 通过证据；需在新的 source freeze 下重跑 samples/evaluate，明确记录每条硬约束
的 PASS、FAIL 或 ABSTAIN，再决定是否继续 shadow 试验。

### 2026-08-30 v122-v127 contract cohort replay

已在新的 source freeze（digest=`777631170481a1be061283feab0a3775e5396c6cc7d614832ac17ca221eb4d60`）下
完成 `/data1/zhangdy/tehm-campaigns/tehm-contract-action32-v122v127-r3` 的完整物理复跑：
12/12 ORFS case 成功，12/12 strict timing clean，12/12 source-identity equivalence
通过。修正 ORFS `geometry.die_area_um2` baseline 提取后，contract observation 明确为：
v122=`PASS`、v123=`FAIL(power_budget_exceeded)`、v124=`FAIL(wns_delta_below_objective)`、
v125=`PASS`、v126=`FAIL(power_budget_exceeded)`、v127=`FAIL(power_budget_exceeded)`；每条
样本的 DRC/equivalence/LVS/timing checks 均为 `PASS`。

随后以 v122 作为唯一 contract-bound support、v125–v127 作为 fresh held-out 重放
`evaluate`。support gate 为 `FAIL`（1 PASS/2 FAIL），fresh gate 为 `FAIL`（1 PASS/2
FAIL），因此 grouped calibration 为 `shadow_calibration_failed`，policy 为
`insufficient_support`，`promotion_eligible=false`，`shadow_policy_materialization` 为
`not_materialized`。历史 v113–v115 因缺少可验证的内部 toolchain preflight 被排除；没有
任何 canonical memory、authority 或 production runtime 写入。下一步必须扩展新的、独立
且满足 contract 的 support/held-out cohort，不能通过放宽硬约束或回灌旧证据来推进。

### 2026-08-30 second contract cohort v128-v133

在提交 `1865e0d` 预注册并冻结第二组 source-disjoint cohort（freeze digest=`f9eee0e1564bb0456d5a06b7c632a53423c25da48684faf07f52efef2328623e`）后，
`/data1/zhangdy/tehm-campaigns/tehm-contract-action32-v128v133-r1` 的 12 个 ORFS arm
全部 `rc=0 SUCCESS`；12/12 strict timing clean，12/12 独立 source-identity
equivalence 通过。contract observation 为 v128=`FAIL(power_budget_exceeded)`、
v129=`PASS`、v130=`PASS`、v131=`FAIL(wns_delta_below_objective,power_budget_exceeded)`、
v132=`FAIL(wns_delta_below_objective)`、v133=`PASS`。

与此前 v122 唯一通过的 support 合并做 disjoint `evaluate` 后，support gate 为
`FAIL`（1 PASS/2 FAIL），fresh gate 为 `FAIL`（3 PASS/3 FAIL），grouped calibration
仍为 `shadow_calibration_failed`、policy 为 `insufficient_support`，且
`shadow_policy_materialization=not_materialized`。失败行保留在分母，未通过筛选删除；
没有 canonical/authority/runtime mutation。该结果说明当前 action32 contract 在这些
独立 RTL 结构上仍不稳定，下一步应转向更有语义约束的 transformation family/contract，
而不是继续无界 knob sweep。

### 2026-08-30 contract sample split provenance firewall

第二 cohort replay 暴露出一个评估边界：manifest 已有 `screen_split`，但旧版
`prospective_samples.json` 没有持久化 support/held-out 角色，evaluate 只能依赖调用者
选择文件和 suffix。现已在 `run_calibration_expansion.py` 增加
`contract-sample-split-v1`：样本同时记录原始 `screen_split` 与规范化的
`dataset_split`，contract training 只接受 `training`，fresh 只接受 `heldout`；角色
缺失、未知或错配会写入显式 `ABSTAINED`/排除 receipt，不会被默认为 learner support。

新增角色映射、training/fresh 分区和缺失角色回归后，全套 `memory/tests` 为 `740 passed`。
该修复改变了 sample provenance schema，因此既有 v122–v133 物理输出必须在新的
source freeze 下重新执行 `samples/evaluate` 才能作为当前评估输入；原始 ORFS/strict/
equivalence 结果仍保留为旧 freeze 的不可变审计证据。该 seam 不写 canonical、authority
或 production runtime。

### 2026-08-31 current-code split-bound replay

按上述 schema 变更，新建 r2/r4 replay roots 并重新生成当前代码的 source freeze；复用的
是既有 ORFS 成功 run receipt，不宣称重新执行硬件流程。r2 的 v128–v133 样本现在明确
记录 `contract_support_v2→training` 与 `contract_heldout_v2→heldout`；r4 的 v122–v127
同样完成 split-bound samples。使用 r4 support 输入和 r2 的 v131–v133 fresh 输入做
`evaluate` 时，角色错配的 v125–v127 被显式记录为
`sample_split_mismatch:expected=training:got=heldout`，没有进入 training。

当前 role-bound gate 为 support `FAIL`（1 PASS/2 FAIL，另有 3 条 held-out 错配被排除），
fresh `FAIL`（1 PASS/2 FAIL）；grouped calibration 仍为
`shadow_calibration_failed`，`promotion_eligible=false`，canonical/authority/runtime
均未写入。该 replay 证明 split provenance 防火墙生效，也确认 action32 contract 尚未
具备足够的可复用 support/held-out 证据。

### 2026-08-31 routing capacity contract pre-registration

action32 在两个 source-disjoint cohort 中仍未满足 contract，因此下一步不再继续无界
`CORE_UTILIZATION` sweep。针对当前 shadow 证据最强、且已有真实 ORFS routing hook 的
`ROUTING_CAPACITY_RECOVERY`，新增独立的
`ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005` typed utility contract：固定
`ROUTING_LAYER_ADJUSTMENT=0.05`（`default->0.05`），要求 WNS/TNS 不回退、area/power
不增加，并要求 equivalence、DRC、LVS、timing 全部 `PASS`。contract 的 status 是
`PRE_REGISTERED_FOR_PROSPECTIVE_COHORT`，`canonical_memory_mutation=none`、
`promotion_eligible=false`；routing semantic receipt（hook consumed、digest-bound）
仍是独立的必要证据，不被 PPA contract 代替。

`run_orfs_add_designs_campaign.py` 现在支持
`--utility-contract-id ROUTING_CAPACITY_RECOVERY_NONREGRESSION_005`。该参数会在
materialization 前把 contract identity 固定进 source freeze、manifest 和每个 pair
action，并拒绝混合 family 或不匹配的 action signature；capture 后的 contract id
继续沿 external receipt→staging 传播，但不会自动打开 canonical 或 production runtime。
已有 `tehm-orfs-l4-transfer-routing-r5` 记录没有该 prepare-time binding，保持原有
shadow/evaluation-only 身份，不追溯重评分。下一次 routing cohort 必须使用新的 source
freeze、源代码/RTL 分离 lineages，并完成 ORFS、equivalence、strict signoff、semantic
hook、graph、contract observation 与 held-out calibration，之后才有资格重新检查六项
promotion gate。

### 2026-08-31 selector_crc16 routing contract execution

按上述 contract 新建 `/data1/zhangdy/tehm-campaigns/tehm-routing-contract-selector-crc16-v1`，
使用 direct toolchain manifest（freeze digest=`3a0a37c1af0edefef2f8e92926b145fc2af1e1105f0b537d6a66ec36cf933495`）和
`sky130hs/selector_crc16` source-disjoint RTL。base 与 `ROUTING_LAYER_ADJUSTMENT=0.05`
两臂均真实 `rc=0`，equivalence、strict signoff、DRC/LVS/timing、semantic hook 和
DEF graph 均通过；graph context 为 `strict_clean`，full oracle 两臂均 complete。
`sky130hs` 没有独立 ORFS DRC deck，DRC receipt 明确记录了同一 sky130A 工艺的
`sky130hd/drc/sky130hd.lydrc` sibling fallback 及其 SHA256，未把该 fallback 隐瞒为
sky130hs 专属 deck。

合同观察为 `FAIL(wns_delta_below_objective)`：WNS delta=`-0.00285 ns`，虽然
power delta=`-1e-06 W`、area delta=`0`，但 WNS 非回退门槛未满足。样本保留为
`calibration`/`learner_eligible=false`，raw Pareto 仍单独记录为 `HARMFUL`；合同结果已
随 transition verifier 持久化，且 `canonical_memory_mutation=none`、
`promotion_eligible=false`。本次结果建立了 routing contract 的真实 fail 分支，不能作为
promotion support；下一步需换取新的 source-disjoint RTL/平台 cohort，只有出现足量且
稳定的 contract `PASS` support 与 held-out PASS，才可进入 shadow calibration 和六项 gate
复核。

### 2026-08-31 incremental crystallization witness typing

按 B2 的 learner-derived 写入边界，`preview_affected_groups()` 与
`crystallize_affected_groups()` 现在要求 `transition_ids` 是非空 `list/tuple`，元素必须
是非空字符串且不得重复；数字、空值、重复 ID 或其它容器均在 SQL/derived-state 操作前
fail-closed。该输入不会再通过字符串 coercion 或静默去重改变 evidence set；增量路径仍
比较 affected/full rebuild、重放 raw-evidence digest，并在 savepoint 内写入 rule/event/
revision。该修复只强化 B2 shadow/candidate provenance，不改变 canonical promotion、
Parametric shadow-only 或 production runtime 边界。

### 2026-08-31 P1 current-valid-state resolver（shadow-only）

按新的 `TEHM_R2G_State_Causal_Online_Asset_Capability_Upgrade_Plan.md`，新增
`memory/tehm/state/`：`tehm_memory_relations` 保存不可变、content-addressed 的
`SUPERSEDES`/`INVALIDATES`/`CONTRADICTS` 等关系，`tehm_state_resolution_snapshots`
保存 scope-aware resolver 的输入 digest、active IDs、suppression receipt 和冲突结果。
P1 以 additive v4 表实现，保留已冻结 v4 schema 与历史 SQLite 不变；旧 v4 store 在首次
调用 state API 时惰性建立这两张 derived 表。

`resolve_current_state()` 目前只进入 shadow lane：支持 target scope/compatibility
scope、替换方向、cycle detection、缺失对象、损坏 digest、authority mismatch 和
ambiguous contradiction 的 fail-closed 行为；未绑定 authority 的关系只能出现在
`shadow_relation_ids`，`mode="production"` 会返回 `UNRESOLVED_AUTHORITY`。resolver
只写自己的 resolution snapshot，不修改 states/transitions、rule/asset/capability
lifecycle、authority ledger 或 production runtime；`verify_resolution_snapshot()` 会
重放当前输入并拒绝 snapshot 漂移。

新增 P1 回归覆盖 supersession、scope 隔离、cycle、relation/snapshot 篡改、现有 v4
store 兼容、authority/contradiction fail-closed 和 canonical/lifecycle 不变性。当前验证结果：定向 state 测试 `5 passed`；
新增最后一个用例前的全套 `memory/tests` 基线为 `757 passed`，本次新增用例未修改生产代码；该阶段仍未打开 causal/asset production routing。

### 2026-08-31 P2 Experience Value（shadow-only）

在 P1 resolver 之上新增 `evolution/value.py` 与 `value_receipts.py`。Value
层从 canonical transition、dataset membership、novelty/conflict、promoted
rule/asset、intervention pair、activation prediction/ablation 等已存在的 typed
witness 计算 `novelty`、`severity`、`capability_gap`、`causal_discrimination`、
`surprise`、`counterexample`、`memory_interference`、`redundancy` 八个有界分量，
以固定权重得到可重放的 `P0_CRITICAL`–`P3_LOW` priority 和
`STATE/CAUSAL/RULE/ASSET/CAPABILITY/NONE` update layers。没有 RL critic，也不接受
模型自行授予分数。

`tehm_experience_values` 是 additive v4 shadow 表；旧 v4 store 首次调用时惰性
建立，`tehm_meta.schema_version` 仍为 `tehm-v4`。`observe_transition()` 现在在同一
savepoint 内并行保存 value receipt，并继续返回原有 trigger/operation；replay 会校验
receipt digest。低价值证据仍保留，非 training evidence 的 layers 固定为 `NONE`，任何
Value 结果都不会写 canonical/lifecycle/authority/runtime。

P2 回归覆盖 routine/new-mechanism、promoted-memory counterexample、memory
interference、prediction surprise、controlled intervention、held-out audit-only、
receipt 篡改和 manager replay：定向 `test_experience_value.py` 为 `5 passed`，
online/schema 回归为 `42 passed`。下一阶段是 P3：将已有 causal path 转为独立的
Mechanism Knowledge claim，仍先保持 shadow/candidate、禁止自动 promoted。

### 2026-08-31 P3 Intervention-grounded Mechanism Knowledge（shadow/candidate-only）

新增 `memory/tehm/knowledge/`，把已验证可重放的 causal path 转换为独立、内容寻址的
`MechanismKnowledge` claim；claim 内容与 evidence ledger、生命周期状态、适用性判断、
revision/supersession relation 和 authority receipt 分离。L0/L1 path 只能生成
`shadow`，L2+ 才允许显式注册为 `candidate`；L3/L4 只参与 authority evaluation，
任何 API 都不能自动写入 `promoted` 或进入 production runtime。

P3 additive 表为 `tehm_mechanism_knowledge`、`tehm_mechanism_knowledge_status` 和
`tehm_mechanism_knowledge_evidence`，旧 v4 store 通过惰性 schema 建表且保持
`tehm_meta.schema_version = tehm-v4`。claim 的 causal-path evidence 强制绑定到
training、learner-eligible campaign；负面适用性只从同一 training campaign 推导，
不会读取 held-out/calibration evidence。resolver 复用 P1 current-valid-state，支持
scope-local/global claim、正/负适用性、shadow supersession，并在 production mode
无 promoted knowledge 时 fail-closed。

P3 回归覆盖 path→claim、L0/L1 shadow-only、evidence split firewall、negative-context
隔离、candidate authority、不可变 digest/replay、适用性、revision supersession、
fresh/old-v4 schema 兼容；新增定向测试 `8 passed`，完整 `memory/tests` 为
`772 passed in 12:29`。该阶段仍未改变 canonical evidence、rule/asset lifecycle 或
production routing。

### 2026-08-31 P4 Online localized revision（shadow-only）

新增 `evolution/attribution.py` 与 `evolution/local_revision.py`。Failure Attribution
从 typed transition、activation、Experience Value 和 State Resolution receipt 中区分
`STATE_RESOLUTION_FAILURE`、`APPLICABILITY_FAILURE`、`CAUSAL_MODEL_FAILURE`、
`BINDING_FAILURE`、`ASSET_EXECUTION_FAILURE`、`VERIFICATION_FAILURE`、
`AUTHORITY_FAILURE`、`MEMORY_INTERFERENCE` 等原因，并给出可重放的 update target；
模型输出不参与归因或授予 authority。

`observe_transition()` 现在在原有 savepoint 内并行生成 P1 `StateResolutionReceipt`、
P4 `MemoryFailureAttributionReceipt` 和 `LocalizedUpdatePlan`。计划只选择
`UPDATE_NONE/STATE_RELATION/CAUSAL_KNOWLEDGE/RULE/ASSET/CAPABILITY` 之一，保留完整
candidate targets、operation（包括 `SUPERSEDE/INVALIDATE/REACTIVATE`）、evidence refs、
knowledge/rule refs 和 resolution ID；它不会执行 rule/knowledge/asset mutation，也不
改变既有 trigger、preview、event cardinality 或 production runtime。新增 event vocabulary
已登记，但当前 receipt 继续复用既有不可变 snapshot，兼容历史在线链。

P4 回归覆盖 verification failure attribution、memory-interference 的非 runtime 计划、
observer P4 receipt replay，以及既有 P1/P2/causal 回归；新增定向 `11 passed`，完整
`memory/tests` 为 `775 passed in 12:15`。下一步可进入 P5：NO_SKILL / memory router
shadow mode，仍不得把 causal retrieval 直接接入 production pipeline。

### 2026-08-31 P5 NO_SKILL / memory router（shadow-only）

新增 `contracts.MemoryRoutingDecision`、`NoSkillReceipt` 与
`tehm/retrieval/memory_router.py`。router 先通过 P1 current-valid-state resolver
解析 scope，再检查 validated mechanism knowledge、L2 causal path、asset binding 与
独立 authority receipt；它输出 `APPLY`、`CONSIDER`、`ABSTAIN`、`INAPPLICABLE` 或
`NO_SKILL` 的内容寻址 receipt。每次路由都保留至少一个 no-memory 候选；shadow
budget 最多允许两个 memory/causal 候选，`ABSTAIN`/`INAPPLICABLE`/`NO_SKILL` 永不
分配 memory budget。未满足 validated knowledge、状态解析、OOD 或负面适用性门控时，
系统分别 fail-closed 为 `NO_SKILL`、`ABSTAIN` 或 `INAPPLICABLE`。

`TehmMemoryBackend` 现在提供 `resolve_state()`、`route_memory()`、
`retrieve_assets()` 和 `record_memory_outcome()` seam；默认仍只运行 shadow router，
`mode="production"` 明确拒绝。现有 `retrieve_query()` promoted-rule pipeline 与
evaluation-only `causal_recall` 未被旁路改写；asset candidate 尚未加入
`MemoryCandidate.source`，因此不会伪造 executable asset。outcome seam 只调用 P4
failure attribution，不执行任何 lifecycle/canonical/authority 更新。

P5 回归新增 `memory/tests/test_memory_router.py`（8 passed），覆盖 fresh
`NO_SKILL`、预算约束、receipt replay、state/OOD abstain、production firewall、
candidate knowledge 隔离和 backend seam；与 backend/knowledge/state/retrieval/
attribution 兼容回归合计 55 passed；当前完整 `memory/tests` 为 `783 passed in
12:13`。`memory/docs/` 继续由 `.gitignore` 排除，设计文档仅作为本地输入，不进入
release commit。

### 2026-08-31 P6 candidate-pool A/B（evaluation-only）

新增 `tehm/retrieval/candidate_pool.py`，把候选池组成与 runtime execution 完全
分离，提供四个可回放 arm：`NO_MEMORY`、`ALWAYS_MEMORY`、
`APPLICABILITY_GATED`、`CAUSAL_NO_SKILL`。每个 arm 都先保留至少一个 no-memory
candidate；Memory Advisor 最多贡献 1 个 candidate，`candidate_budget=1` 时不会
挤占 no-memory。`CAUSAL_NO_SKILL` 只有在 P5 routing receipt 为 `APPLY`/`CONSIDER`、
因果状态为 `SUPPORTED`、存在 selected path 且 candidate ref 被 router 选中时才放行。

每个 pool 生成 content-addressed receipt，记录 routing ref、来源、候选预算、action
family / mechanism hypothesis diversity、normalized search entropy 与 memory admission。
显式 oracle outcome 可进一步计算 `MemoryInterferenceRate`、`AbstentionUtility`、
memory harm、candidate budget efficiency 和 diversity；`UNKNOWN` 不会被当作 PASS，
未成对 outcome 不进入 interference 分母。该模块不执行 candidate、写 canonical、
修改 lifecycle，也不把 asset 提前扩展成 `MemoryCandidate.source`。

P6 定向回归为 `memory/tests/test_candidate_pool.py`（7 passed），覆盖四类 arm、
budget/no-memory firewall、causal/NO_SKILL gate、receipt replay、unknown outcome
隔离和 interference metrics；P5 router 与 backend 兼容测试保持通过。当前完整
`memory/tests` 为 `790 passed in 12:28`。

### 2026-08-31 P7 knowledge-grounded Asset selector（shadow-only）

新增 `tehm/retrieval/asset_selector.py` 与 `TehmMemoryBackend.select_assets()`。
严格 selector 只接受同时满足以下条件的 `RTL_REWRITE_TEMPLATE`：当前
`MechanismKnowledge` 已验证且正向适用性通过、负向适用性未触发、L2+ causal path
可重放、asset 内容/生命周期完整、manifest binding proof 有效，并且 asset provenance
显式绑定到相同的 knowledge object。每次最多选择一个 advisor asset，返回独立的
content-addressed `AssetSelectionReceipt`；selector 不写 canonical/lifecycle/authority，
不执行 RTL action，也不把 `tehm_asset` 加入 `MemoryCandidate.source`。

旧 fixture 可通过调用方显式传入 `compatibility_mode=True` 保持可观察，但该路径仍是
shadow-only，不能绕过 production gate。缺少 knowledge、binding、authority 或状态
一致性时分别 fail-closed 为 `ABSTAIN`/`NO_SKILL`，并保留可审计原因。asset proposal
现在可选记录 `provenance.mechanism_knowledge_ids`，供 strict campaign 建立绑定。

P7 定向回归为 `memory/tests/test_asset_selector.py`（4 passed）；`memory/docs/` 继续由
`.gitignore` 排除，设计文档只作为本地输入，不进入 release commit。下一步是 P8：把
knowledge/asset delta 与 attribution、state-resolution receipt 接入 capability audit，
仍先保持 evaluation-only。

### 2026-08-31 P8 Expanded Capability Attribution（evaluation-only）

扩展 `tehm/capability/attribution.py`、`delta.py`、`authority.py` 与 campaign harness，
为既有 C1–C8 增加对象级 witness：`KnowledgeDeltaReceipt`、`AssetDeltaReceipt`、
`RoutingReceipt`、`StateResolutionReceipt` 和 `MemoryFailureAttributionReceipt`。严格
P8 campaign 需要五类 witness 全部存在且可重放；knowledge/asset changed IDs 必须属于
同一份 concrete memory delta，routing 必须绑定同一个 state resolution，数据库入口还会
重放 state snapshot。缺失、跨状态、digest 不一致或重复 receipt 均 fail-closed，并以
`P8:*` 原因出现在 attribution receipt 中。

authority payload 会保留 expanded witness bundle，后续 replay 可重新验证 KΔ/AΔ、
routing/state/failure attribution，而不依赖内存中的原始对象。该扩展只增强 capability
evidence 与审计，不改变 C1–C8 gate 含义、不写 production capability status、不改
canonical evidence，也不把任何 asset 接入 runtime candidate source。

P8 定向回归为 `memory/tests/test_capability_expanded_attribution.py`（4 passed），并与
既有 capability attribution 回归合计 51 passed；`memory/docs/` 继续由 `.gitignore`
排除，不进入 release commit。下一步是 P9 production gate：只有完成 harmful-rate、
repair-gain、NO_SKILL calibration 等真实证据审计后，才讨论 production integration。

### 2026-08-31 P9 production gate（evaluation-only，fail-closed）

新增 `tehm/retrieval/production_gate.py` 与 `TehmMemoryBackend.evaluate_production_gate()`。
P9 只评估一份显式 evidence manifest，不写 SQLite、不改变 canonical memory、
lifecycle 或 runtime；`route_memory(mode="production")` 仍然拒绝。gate 需要同时具备：

* 成对 oracle 的 efficacy 证据：memory harmful activation 严格下降，或在明确
  `controlled_harm=true` 下 repair rate 严格提升；
* 有非空分母的 NO_SKILL precision/recall calibration；
* paired candidate-pool、interference-rate 与 diversity；
* content-bound authority receipt、rollback receipt 以及带 digest 的 immutable
  evidence refs。

缺失值报告为 `NOT_ESTABLISHED`，显式失败报告为 `FAIL`；NaN、UNKNOWN、未成对样本、
调用方布尔 gain 或无 digest 的 opaque reference 均不能通过。receipt 自身内容寻址且
可 replay，`production_integration` 永远是 `not_attempted`。当前已审计证据仍不足以
通过 P9：ORFS capability 报告的六项 rule authority gates 仍缺失，历史 campaign 也有
`harmful_rate=0.875` 的失败记录，尚无完整 NO_SKILL calibration manifest。因此本阶段
只建立 production decision seam，不执行任何 promotion 或 runtime integration。

P9 定向回归为 `memory/tests/test_production_gate.py`（7 passed）；`memory/docs/` 继续由
`.gitignore` 排除，设计文档不进入 release commit。后续需先生成真实、成对、可重放的
NO_SKILL / interference / controlled-harm evidence，再由独立 authority review 决定是否
增加 production adapter。

`scripts/build_production_gate_report.py` 是 P9 的证据装配入口：manifest 只声明已经
产生的 oracle 报告和显式 metrics，脚本会重新计算每个本地文件的 SHA256，拒绝 stale
digest，再生成带 `receipt_id`/`receipt_digest` 的 gate report。对现有 ORFS density
efficacy、support-routing 和 action32 calibration 报告的实际审计结果为：`evidence=PASS`，
但 `efficacy=FAIL`（repair gain 没有 `controlled_harm` 标记），`authority`、`rollback`、
`candidate_pool`、`no_skill_calibration` 均为 `NOT_ESTABLISHED`；命令以非零码退出，
不会把这些片段误报为 production-ready。该审计输出保存在个人临时目录，不作为仓库
证据提交；后续真实 campaign 可复用同一 manifest/builder 闭环。

receipt replay 还会独立校验 gate status、`eligible` 合取、证据引用格式和
`production_integration=not_attempted`；即使调用方移除 digest，也不能通过结构化字段
篡改来伪造 production authority。

### 2026-09-01 P10 semantic repair（shadow/evaluation-only）

知识修订现在区分 same-claim 与 identity-changing structural revision：只有
`REVISE` 保留 `knowledge_id`、递增 version 并产生 `SUPERSEDES`；`SPECIALIZE` /
`GENERALIZE` 强制新 identity 并分别产生 `SPECIALIZES` / `GENERALIZES`，因此语义
关系不会错误地从 current-valid-state 中压制父 claim。新增 `split_knowledge()` 与
`merge_knowledge()`，前者要求每个 child 的 partition witness，后者要求每个 parent
的 multi-parent witness；两者均只写不可变 shadow relation，不授予 authority。

P1 relation resolver 现在显式区分 informational、state-affecting 和 conflict 三类。
informational edge（`DERIVED_FROM`、`SPECIALIZES` 等）在 production replay 中不需要
authority，也不改变 active set；state-affecting edge 仍必须绑定可验证 authority，
否则 production fail-closed；`CONTRADICTS` 两端同时 active 时保持 unresolved。
新增 `RelationAuthorityReceipt` 作为独立的 relation→authority 审计边界；receipt 现在
要求显式 approved effect，并以 content-addressed `replay_digest`/`receipt_id` 做
from-dict 重放与篡改拒绝。该 typed receipt 仍只是 authority evidence seam，不会自行
修改 relation、lifecycle 或 production runtime。

fixture manifest binder 已明确标记 `binding_source=fixture_manifest`、
`runtime_eligible=false`。新增 `bind_asset_to_repair_context()` 只接受 RTL 结构、
failure/config/reports、Mechanism Knowledge 和 localization receipt，拒绝 `fix`、
gold patch、repaired RTL、held-out answer 等字段；结构歧义返回不 eligible 的
`RuntimeBindingReceipt`，不会触碰 canonical/lifecycle/production runtime。

P5/P6 routing receipt 的 memory advisor 上限统一为 1；历史调用传入的 2 仅用于保持
总候选预算账本兼容，实际决策永远不会分配两个 memory candidate。P10 回归新增覆盖
semantic specialization、split/merge witnesses、informational relation firewall、
gold-leakage-safe runtime binding 与 ambiguity abstention；`memory/docs/` 仍由
`.gitignore` 排除，不进入 release commit。后续依赖顺序仍为 P11 structured candidate、
P12 real candidate execution、P13 shadow online update、P14 capability causal chain，
在这些真实 evidence 完成前 production routing 继续关闭。

### 2026-09-01 P11 StructuredRepairCandidate（evaluation-only）

P7 的 `AssetSelection` 现在可以继续构造成独立的
`StructuredRepairCandidate`，但不会修改 `MemoryCandidate.source`，也不会进入
既有 activation/runtime pipeline。构建器会重新检查 routing 与 selection 的
`resolved_state_id`、knowledge object、causal path、asset ID 和 runtime binding，
并要求 non-empty obligations、applicability、authority、risk、provenance 与
content-addressed candidate digest 全部存在。

候选 action 只由 asset action 与 `RuntimeBindingReceipt.selected_binding` 合成；
未解析 `$H`、`fix`、gold patch、repaired RTL、held-out answer 会 fail-closed。
候选和 `StructuredCandidateReceipt` 永远带 `evaluation_only=true`，backend 只提供
显式的 `build_structured_candidate()` seam。这样 P11 已打通
`Routing → AssetSelection → RuntimeBinding → StructuredCandidate`，但还没有执行
候选或声称 repair gain。P12 的 `tehm/evaluation/candidate_executor.py` 现在提供
只读 oracle adapter 和 `CandidateExecutionReceipt` replay；没有注入 oracle 时结果
严格保持 `UNKNOWN`，不会由 action 或调用方布尔值推断 PASS。下一步仍需在固定 N=3
的四臂 paired cohort 上接入真实 R2G executor/oracle，并记录 toolchain/oracle digest。
同一模块还提供 `execute_paired_candidates()`：强制 `NO_MEMORY`、
`ALWAYS_MEMORY`、`APPLICABILITY_GATED`、`CAUSAL_NO_SKILL` 四臂齐全，固定 case/budget，
并拒绝 toolchain/oracle digest 漂移；它仍只是实验 receipt，不把执行结果写回记忆。

### 2026-09-03 P12-B Icarus candidate oracle（controlled fixture）

新增 `tehm/evaluation/rtl_candidate_oracle.py`，把 P11 的结构化 RTL candidate
接入真实 `IcarusOracle`。frozen case 只允许显式提供 `rtl_source`、target test、
frozen regression 和可选的附加 RTL 文件；候选动作在临时目录应用，随后对同一
source freeze 执行 target + regression。该 adapter 不打开 `manifest.json`，不消费
`manifest.fix`，不写 canonical transition，返回的 `produced_transition_id` 永远为
`None`，因此四臂结果仍只能进入 evaluation evidence。

`IcarusCandidateOracle` 可直接注入 `execute_paired_candidates()`。固定 fixture
`req_ack_bug` 已验证：`NO_MEMORY` 的 target arm 失败，而三个 memory arms 在同一
toolchain/oracle digest 下通过，source 原文件保持不变。这个阶段只是 Stage A
controlled-fixture path/oracle/receipt 验证，不是 frozen R2G cohort 的 repair gain
或 capability 结论；下一步仍需准备 source-disjoint 的固定 toolchain R2G cohort。

### 2026-09-03 P12-C fixed-environment cohort boundary（evaluation-only）

新增 `tehm/evaluation/rtl_cohort.py`，把多个 P12 paired case 装配成可 replay 的
`RtlPairedCohortReceipt`。cohort 入口要求每个 case 显式绑定 source digest、
toolchain/oracle digest、platform digest 和 PDK digest，并要求 campaign manifest
digest；会拒绝重复 source 内容、环境 digest 漂移、case/arm 缺失以及 source 在执行
期间被修改。每个 case 仍强制四臂和固定候选预算，结果只保留原始 arm outcome counts，
不把 paired delta 解释成 production 或 capability gain。

当前两个独立 handshake fixture 已通过真实 Icarus cohort smoke：无记忆臂均失败，
三个 memory 臂均通过，receipt 可 replay。它们仍属于 Stage A/受控 RTL evidence；
真正的 Stage B 需要另外冻结、source-disjoint 的 R2G/ORFS cohort 和真实固定工具链，
不能用这些 fixture 代替。

### 2026-09-03 P12-D ORFS candidate oracle（evaluation-only）

新增 `tehm/evaluation/orfs_candidate_oracle.py`，沿用现有 R2G
`lifecycle.orfs_trial._execute_arm()` 作为 flow/signoff 执行权威。每次执行都从冻结
project 建立临时副本，只允许结构化 `flow.CONFIG_DELTA` 的标量 `config_edits`，并在
执行前后校验 source snapshot digest；显式绑定 ORFS root、OpenROAD/Yosys 可执行文件、
PDK、toolchain/oracle digest 和 source digest。固定 pin 不能被 case 或注入环境覆盖，
工具路径必须可执行。adapter 不读取 fixture manifest/gold answer，不写 canonical 或
lifecycle，`produced_transition_id` 永远为 `None`；执行结果仅通过
`CandidateExecutionReceipt` 的 `oracle_metadata` 保留 run/config/source witness。

新增 fake-R2G flow 测试覆盖临时 project、baseline/candidate 四臂分离、source 不变、
非 `flow.CONFIG_DELTA` 拒绝和固定环境覆盖拒绝。真实 ORFS 配置若引用 project 外的
RTL/SDC，必须通过 `source_inputs=[{path, sha256}]` 显式绑定；否则不能进入
source-disjoint cohort。新增 `tehm/evaluation/orfs_cohort.py`，在四臂执行外再固定
source、toolchain/oracle、platform/PDK 和 campaign manifest，并支持 receipt replay。

用 clean ORFS 与匹配的 `/data1/zhangdy/Tools/tehm-toolchain` 对已有
`sky130hs/v116` frozen case 做了一次真实四臂 smoke：`NO_MEMORY`、三个 memory arm
均为 `route/signoff PASS`，耗时约 256 秒，paired receipt 为中性结果（没有
`paired repair delta`），产物仅在
`/data1/zhangdy/tehm-campaigns/tehm-p12d-orfs-v116-stageb-20260903/`。该单 case 仍
不是 source-disjoint 多 lineage 统计证据，也没有导入 canonical 或触发 promotion。
fake seam/cohort 定向回归现为 `18 passed`。下一步是在同一 exact manifest 下增加新的
source-disjoint design/lineage，跑完整 flow/equivalence/strict signoff/PPA/DEF graph，
再将真实四臂 cohort 交给 P13 shadow update；在此之前 canonical memory、promotion
和 production runtime 均保持关闭。`memory/docs/` 继续由 `.gitignore` 排除，不进入
提交。

### 2026-09-03 P13 localized shadow update executor

新增 `tehm/evolution/apply_update.py`，把 `LocalizedUpdatePlan` 从“只生成计划”推进到
“只在隔离 staging 执行并立即丢弃”。executor 先对 source SQLite、raw canonical
evidence 和当前 resolution 做 digest，再用 SQLite backup 建立内存副本；causal/rule
plan 只能消费同 campaign、training、learner-eligible 且已 verified 的 transition，
relation/asset/capability plan 必须提供对应 typed evidence。所有派生 rule/revision、
state relation、shadow asset 或 candidate capability 都只存在 staging，永远不调用
production authority，也不导入 runtime。

`AppliedShadowUpdateReceipt` 固定记录 before/after resolution、created object/relation
IDs、source/staging/raw-evidence digest，并强制
`canonical_rows_changed=false`、`production_authority_changed=false`、
`canonical_memory_mutation=none`、`lifecycle_mutation=isolated_staging_only` 和
`staging_discarded=true`；source connection 与 canonical evidence 在返回前再次校验。
P13 定向回归覆盖 relation、RETAIN、真实 Icarus causal crystallization、asset、
capability、非 learner 防火墙和 receipt replay/tamper，共 `7 passed`。这证明了
shadow update 的执行边界，但还不是 P14 capability attribution 或 promotion gate
证据；下一步应把 source-disjoint ORFS cohort 的四臂 receipt 转换为可回放的 shadow
update 触发，再建立 anti-forgetting、held-out regression 和 P14 C1-C8 lineage。

新增 `scripts/run_p13_shadow_update.py` 作为上述触发到执行之间的显式 replay seam：
它只接受 `p13_eligible=true` 且逐 case `triggered=true` 的 typed trigger report，
再由独立 manifest 提供逐 case `LocalizedUpdatePlan` 与 evidence。runner 以只读方式
打开 source SQLite，调用 shadow executor 的内存 staging copy，逐条校验 plan/trigger
campaign 与 digest witness，并在打开 source 前预检所有非 `RETAIN` 计划的
`AntiForgettingWitness` 四项 gate，最后验证 source 文件字节 digest 未变并丢弃 staging；输出
`AppliedShadowUpdateReceipt`、source/manifest digest 和固定的
`canonical_memory_mutation=none`、`production_runtime_imported=false`。因此该入口
可以回放真正 eligible 的 P13 proposal，但不会写 canonical、lifecycle 或 production
runtime；当前真实 ORFS cohort 仍因缺少独立 evolution reason 而不能调用它。

当前 manifest 还必须显式绑定 `trigger_report_digest` 与 `source_db_sha256`。runner
在打开 source SQLite 前同时校验这两个跨文件摘要，避免把合法 trigger report 或 plan
接到另一份数据库快照上；输出 receipt 会保留期望的 source digest。该约束只加强
evaluation/shadow replay 的 provenance，不授予 canonical memory 或 production authority。

对于非 `RETAIN` mutation，manifest 还必须通过 `anti_forgetting_receipts` 引用上述
binder 生成的 report；runner 会重新校验 report 文件 SHA256、版本、campaign/case、
嵌套 witness digest 和 `eligible` 摘要，并将绑定 witness 注入执行 evidence。内联手工
witness 只有在与该文件完全一致时才可作为重复证明，不能绕过 file-bound provenance。

新增 `scripts/build_p13_anti_forgetting_witness.py` 作为 anti-forgetting provenance
binder：manifest 必须分别提供 target replay、non-target regression、held-out audit 和
rollback 四个不同文件、显式 gate 布尔值及匹配的完整 SHA256。binder 只绑定这些声明并
生成 `AntiForgettingWitness`，不从文件内容推断结果；任一 gate 失败会保留为
`eligible=false` 审计 receipt，不能被 P13 runner 消费，且 manifest/evidence/output
路径不能互相复用。

### 2026-09-03 Revision2 reason-aware NO_SKILL / State Shift

依据 Revision2 设计，`MemoryRoutingDecision` 现在保留兼容的顶层 `NO_SKILL`，并增加
typed `no_skill_reason`：`NO_MATCH`、`STATE_SHIFT`、`RISK`；同时可绑定
`state_shift_receipt_id` 或可重放的 content-addressed `risk_receipt_id`/`risk_receipt`
（含 current resolution、expected utility、evidence refs 和 risk model）。旧 receipt 缺少 reason 时只做兼容映射
到 `NO_MATCH`，不会把 `ABSTAIN` 或 `INAPPLICABLE` 混入主动拒绝语义；memory slot
仍最多 1 个，no-memory arm 始终保留。

新增 `tehm/state/support_envelope.py`、`shift.py` 和 `shift_receipts.py`。`SupportEnvelope`
只接受 training、learner-eligible、PASS/complete oracle 的 typed facts，拒绝 held-out、
calibration 或不完整 evidence 扩大支持域；`evaluate_state_shift()` 按 structural、
mechanism、flow、constraint、oracle、history 六个维度 deterministic 比较当前状态，
输出可 replay 的 `StateShiftReceipt`。缺失状态事实不会被猜成 shift，仍由上层
`ABSTAIN` 处理。Router 在收到显式 envelope/current-state facts 时会返回
`NO_SKILL + STATE_SHIFT`，而显式、可重放的负 expected utility 才会返回 `NO_SKILL + RISK`；
不完整 risk/shift evidence fail-closed 为 `ABSTAIN`。

四臂 `PairedCandidateExecutionReceipt` 以及 RTL/ORFS cohort receipt 现在可携带
`no_skill_reason` 与对应 shift/risk receipt ID，并提供 reason-stratified counts；这使
`CAUSAL_NO_SKILL` 的 refusal 语义可以原样进入后续 calibration，而不从执行结果反推。

P12-F 接口还为 paired receipt 增加可选 `lineage_id`，cohort 暴露
`lineage_ids`/`lineage_count`；调用方设置 `min_lineages>1` 时，入口在执行前强制每个
case 显式声明 lineage 且要求足够的 distinct lineages，避免跑完昂贵 flow 后才发现
cohort 不是 source-disjoint multi-lineage evidence。该 gate 已由真实 2-lineage
ORFS cohort 重放验证，但 cohort 的 paired delta 为中性且尚未形成独立 NO_SKILL /
evolution evidence，因此仍不能据此声称能力或 promotion 证据。

P12-F 现在还保留 `routing_receipt_id`，并在 candidate-pool 与 paired receipt 中贯通
`no_skill_reason`/state-shift/risk receipt；对于 `RISK`，除 ID 外还保留经过
content-addressed 校验的完整 `risk_receipt` payload，因而下游可以独立重放预期效用证据；
`tehm.evaluation.paired_metrics` 提供
统一的 unknown-safe paired 统计。原始 `UNKNOWN` outcome 仍会计入 outcome counts，
但会从对应 paired harm/repair denominator 排除，并报告 routing receipt coverage，
避免不完整 oracle 被误算成失败或安全证据。该层仍是 evaluation-only，不改变 canonical
memory、authority 或 production runtime。

同时新增 `tehm.evolution.retrieval_attribution`：只有 eligible candidate set、candidate
pool、已选 memory candidate 的失败执行，以及明确标记为 counterfactual 且通过 oracle 的
漏召回 candidate 同时存在时，才生成 `RETRIEVAL_FAILURE` attribution；单独的失败、
`UNKNOWN` 或不完整 counterfactual 会 fail-closed。该 receipt 只进入 shadow attribution，
不会凭失败结果直接修改 retrieval 或 canonical memory。

Revision2 定向回归新增 6 个测试，覆盖 support-envelope training-only、state-shift
六维 receipt replay、legacy reason compatibility、fresh `NO_MATCH` 和 router
`STATE_SHIFT` 路由。下一步依赖顺序已调整为：先把 reason-aware receipt 接入四臂
P12 cohort 并建立 source-disjoint 多 lineage cases，再把真实 no-memory oracle
结果送入 P13 shadow update；P14 capability attribution、P15 分 reason CI calibration
和 production pilot 仍未建立，canonical memory 与 production runtime 继续关闭。

### 2026-09-03 P12→P13 typed shadow trigger bridge

新增 `tehm/evolution/p12_shadow_trigger.py`，将 RTL/ORFS 四臂 P12 cohort receipt
转换为逐 case、可 replay 的 `P12ShadowUpdateTriggerReceipt`。触发前强制检查
source-disjoint 与 source-restore、显式 distinct lineage、routing receipt、训练
`learner_eligible` 断言，以及 `NO_MEMORY` 与指定 memory arm 两侧都具备
`oracle_available=true`、完整 compile/functional/signoff verdict。缺失任一证据只生成
不可触发 receipt，结构性篡改直接拒绝。routing receipt 还必须通过调用方提供的
`MemoryRoutingDecision` 重新计算 `routing_receipt_id`/digest，并且只能由 `APPLY` 或
`CONSIDER` 路由触发（state-shift 观察的唯一例外见下文）；该层不从 outcome 推断
capability gain、failure cause 或 promotion。

Revision2 的 8A.9 state-shift teaching path 是唯一的 no-memory 例外：带有
`no_skill_reason=STATE_SHIFT`、匹配的完整 `StateShiftReceipt` payload、
`state_shift_receipt_id` 和完整 paired historical-memory oracle 的 `NO_SKILL` route
也可以生成 P13 trigger；P12 replay 会拒绝只有 ID、过期 digest 或篡改后的 witness。
`NO_MATCH`、`RISK`、`ABSTAIN` 和
`INAPPLICABLE` 仍不会触发 mutation；该例外只把已路由的 transfer-boundary observation
送入 shadow lane，不改变 no-memory 决策，也不授予 canonical/production authority。

`apply_localized_update_shadow()` 现在可消费显式 `p12_shadow_trigger` evidence，但
要求 trigger digest 同时出现在 `LocalizedUpdatePlan.evidence_refs`，并把该 digest
记录到 shadow receipt metadata。P13 仍只在隔离 SQLite staging 中执行并丢弃，canonical
evidence、lifecycle、authority 与 production runtime 均不变。真实多-lineage ORFS
cohort 仍需由外部固定 toolchain/ORFS 运行产生；当前新增的是闭环入口与 fail-closed
证据约束，不等同于能力或 promotion 证据。

同日已用固定 direct toolchain 实跑双 lineage ORFS cohort：v116（heldout）与 v117
（training）各执行 `NO_MEMORY`、`ALWAYS_MEMORY`、`APPLICABILITY_GATED`、
`CAUSAL_NO_SKILL` 四臂，8 次 flow/signoff 均为 `PASS`，`lineage_count=2`，paired
repair delta 三个 memory arm 均为 `0.0`，interference rate 均为 `0.0`。可复核产物位于
`/data1/zhangdy/tehm-campaigns/tehm-p12f-orfs-v116-v117-20260903/`。该 cohort 的
routing receipt coverage 为 `0.0`，因此 P12→P13 审计明确输出
`p13_eligible=false`、`triggered_count=0`；这建立了真实 execution evidence，但不能
被写成 retrieval/capability gain 或 P13 online update。下一步必须为同一 cohort 补齐
真实、可 replay 的 `MemoryRoutingDecision` receipt，再重放 trigger 与 P13 staging。

P13 learner admission 现在还要求调用方提供覆盖全部 case 的
`case_learner_eligibility` manifest binding。`learner_eligible=true` 不再足以授权一个
混合 training/held-out/calibration cohort；缺少 per-case map 或发现混合分区会直接
fail-closed，必须拆成 training-only learner cohort 与 audit-only cohort。这样
held-out 结果可以继续做 transfer/anti-forgetting 审计，但不会通过 P12 trigger
扩展 learner support envelope 或进入 shadow mutation。该约束只影响 P13 shadow
admission，仍不写 canonical memory、不改变 lifecycle/authority，也不打开 production
runtime。

P13 的任一非 `RETAIN` shadow mutation 还必须携带 content-addressed
`AntiForgettingWitness`：target replay、non-target regression-free、独立 held-out audit
和 rollback pointer 四项均需显式 receipt/digest 且全部通过；缺失、篡改或任一 gate
失败都会拒绝 mutation。该 witness 只证明隔离 shadow 试验具备 anti-forgetting 前置
条件，不等价于 capability authority 或 production rollback。

此外，P12→P13 trigger 不再把“两个 oracle 完整且为 PASS”当作自动演化事件。调用方
必须为每个 case 提供 Revision2 规定的显式 `evolution_reasons`（`NOVELTY`、
`CONFLICT`、`COUNTEREXAMPLE`、`REPEATED_FAILURE`、`CAPABILITY_GAP` 或
`MEMORY_INTERFERENCE`）；没有信号的完整 cohort 只生成 `no_evolution_signal` 的
`RETAIN` receipt。旧 v0.1 receipt 仅可 replay/审计，不能绕过该信号门进入当前
mutation；因此普通 PASS 不会自动 consolidate，canonical memory 与 production
runtime 仍保持关闭。

P14 归因的 `MemoryDeltaReceipt` 现在也显式包含 immutable state-relation 的
`added_relation_ids` / `removed_relation_ids`。relation 不允许原地 revise；relation
delta 与 knowledge/asset/object delta 一样进入 `changed_ids`，并对同一 relation 同时
添加和移除执行 fail-closed overlap 检查。这样只有 relation/state 变化也能被 C1 的
memory delta 观察到，而不会被误报成“没有 memory change”。

同时新增 P14 `CandidateLineageReceipt`：它把 `StructuredRepairCandidate` 的 digest
串接到 routing、asset selection、runtime binding 以及实际
`CandidateExecutionReceipt`，并在 strict expanded attribution 中作为必需的 C5
witness。任一 state、asset、knowledge、binding 或 execution candidate 不一致都会
拒绝 lineage；该 receipt 仍只用于 attribution/replay，不授予 capability authority。

P13→P14 之间现在有一个 typed `memory_delta_from_shadow_update()` 适配器：它只能消费
`AppliedShadowUpdateReceipt`，从 receipt 内部绑定的 source/staging digest 与创建对象/关系
清单派生 C1 `MemoryDeltaReceipt`，调用方不能另行注入 digest 或 changed-memory 布尔值。
`episode`/`rule_revision` 等派生 bookkeeping 行不会伪装成 memory change；任何 canonical、
production、raw-evidence 或 staging 隔离不变量漂移都会 fail-closed。该适配器只生成
evaluation/attribution 证据，不写 canonical memory、不改变 lifecycle/authority，也不允许
production runtime 导入。

Capability attribution 现在可直接接收 `shadow_update_receipt`：C1 会由该 typed receipt
派生，若同时提供手写 `memory_delta` 则必须与派生结果逐字段一致。authority receipt 会保存
这份 shadow receipt，并在后续 replay 中重新解析 replay/receipt digest、隔离不变量和对象
清单；删除或篡改任一绑定都会使 C1 失效，而不会退回到两个 opaque memory digest 的比较。
`MemoryDeltaReceipt` 本身也带有 content-addressed `receipt_digest` 与 `from_dict()` replay
校验；authority 在重放时会重新计算规范化 delta、changed IDs、eligibility 和 reasons，防止
只篡改 receipt 内部字段而绕过 C1。

### 2026-09-03 P15 reason-aware calibration seam

新增 `tehm.evaluation.no_skill_calibration`，把 P15 的 calibration 输入冻结为显式的
预测/独立 oracle 二元标签：`USE_MEMORY` 或带 typed reason 的 `NO_SKILL`（`NO_MATCH`、
`STATE_SHIFT`、`RISK`）。receipt 同时给出总体 precision/recall/F1/USE_MEMORY coverage、
每个 reason 的 precision/recall、四标签 reason confusion matrix、逐维
`mechanism_family/design/platform/flow_regime/model_identity/state_shift_dimension`
分层、Wilson 95% 区间和基于预测置信度的 ECE。重复 case、缺失 reason、NaN、未知
stratum 或不完整 confidence 会 fail-closed；样本不足或 reason 分母不足只生成
`NOT_ESTABLISHED`，不会被当作 calibration PASS。receipt 带样本摘要和 digest，
`evaluation_only=true`、`canonical_memory_mutation=none`。
每个 calibration sample 还可以绑定实际 `routing_receipt_id`；P15 report 单独记录
`routing_receipt_coverage`，缺少 route witness 时即使标签和置信度齐全也保持
`NOT_ESTABLISHED`，避免脱离 router 行为的手工标签进入 gate。

`build_no_skill_calibration_samples()` 还可从四臂 paired receipt、typed
`MemoryRoutingDecision` 和独立 oracle label map 装配这些样本：`APPLY/CONSIDER` 映射为
`USE_MEMORY`，只有真实 `NO_SKILL` 才携带其 reason；`ABSTAIN/INAPPLICABLE` 不会被偷换
成 `NO_SKILL`，route ID 不匹配或 case 覆盖不完整会直接拒绝。

report builder 现在支持 `paired_routing_index`、`routing_decisions`、`oracle_labels`
三件套作为输入模式，自动由 typed route 生成预测；与 `samples` 同时出现会拒绝，避免
两套标签来源不一致。该模式只保存 route receipt 索引，不读取四臂 outcome，独立 oracle
仍必须由外部 manifest 提供。

`evaluate_production_gate()` 在收到该结构化 receipt 时改用总体 precision/recall 的
Wilson lower bound，并记录 reason metrics/confusion matrix；同时当 evidence 提供
`memory_interference_cases` 时使用 MIR Wilson upper bound（旧的点估计 manifest 仍
保持兼容）。这只是 P15 decision seam，不代表已有真实 calibration、authority 或
production runtime；当前真实 ORFS cohort 尚未产生独立 NO_SKILL oracle 标签，仍不能
据此推动 canonical memory 或 runtime promotion。

`scripts/build_no_skill_calibration_report.py` 现在提供 P15 证据装配入口：manifest 必须
显式给出二元预测/独立 oracle labels、`oracle_label_source` 和带 SHA256 的 immutable
`evidence_refs`。脚本拒绝 `outcome`、repair 或 gold-answer 字段，因此不会从 ORFS
PASS/FAIL 反推 `Should Use Memory`；输出仍固定为
`canonical_memory_mutation=none`、`promotion_attempted=false`、
`production_integration=not_attempted`。没有真实独立标签时，该入口不会生成可通过的
calibration gate。

Production gate 的 efficacy 现在也保留 Revision2 的 Pareto 约束：当 evidence 同时
提供 baseline/memory repair rate 时，harm reduction 不能用 repair collapse 换取；若
进一步提供成对的 `repair_paired_cases`、`repair_regression_cases` 与
`repair_improvement_cases`，gate 会自行计算连续性校正的 McNemar 95% 回归检验，显著
的 baseline-pass → memory-fail 回归直接 `FAIL`，不会接受调用方布尔标记替代统计证据。
旧的仅 harm-rate 或显式 controlled-harm repair manifest 仍保持兼容，但不因此获得更强
的统计保证。

`tehm.evaluation.paired_metrics` 已升级为 `p12-paired-metrics-v0.2`：每个 memory arm
同时输出 MIR point estimate 与 Wilson 95% interval、baseline-pass → memory-fail 和
baseline-fail → memory-pass 的 paired discordant counts，以及连续性校正 McNemar
结果。`UNKNOWN` 仍只保留在原始 outcome counts，不进入这些 paired 分母；因此该 receipt
可直接作为 P15 manifest 的统计来源，但不会单独授予 authority。

### Revision2 support-envelope provenance hardening

`build_support_envelope()` 现在要求每条 source transition 显式绑定
`split=training`、`learner_eligible=true`、`verdict=PASS` 和
`oracle_complete=true`（同时接受带同等字段的 `verification` object），并要求至少
一个带真实 `transition_id` 的训练 transition。缺失字段、非训练/不合格 evidence 或
空 source collection 均 fail-closed；因此仅凭 Knowledge 的 applicability 描述不能扩大
`SupportEnvelope`。该约束仍只影响 shadow/evaluation transfer 判断，不写 canonical
memory、不改变 authority，也不把 held-out/calibration evidence 变成 learner support。

### 2026-09-03 P12→P13 replay report boundary

新增 `scripts/build_p13_shadow_trigger_report.py` 与
`P13EvolutionReasonReceipt`，把 frozen RTL/ORFS cohort、campaign manifest、typed
`MemoryRoutingDecision` 和可选的 typed evolution-reason receipt 绑定为一份
可回放的 P13 审计报告。manifest 必须逐 case 给出 learner partition；route 文件若提供则
必须覆盖全部 case 并通过 digest/类型校验；reason receipt 还必须绑定当前 campaign/cohort
digest、`label_source` 和至少一个带 SHA256 的 immutable `evidence_ref`。脚本不会从
PASS/FAIL、repair 或 oracle outcome 推导演化原因：缺少 route 只生成 `missing_routing_*`，
完整但没有显式 reason receipt 的 cohort 只生成 `no_evolution_signal` retain receipt。报告固定声明 `canonical_memory_mutation=none`、
`production_runtime_imported=false` 和 `isolated_staging_only`，不能替代 P13 anti-forgetting
或 P15 promotion gate。

该 replay boundary 现在也要求 trigger report 自身带可重算的 `report_digest`，每条
trigger 必须带匹配的 `receipt_digest`；runner 会在打开 SQLite 前校验这些摘要以及
`canonical_memory_mutation`/production/isolated-staging 不变量，避免手工改写 report
后继续执行。report builder 同时拒绝把 cohort、manifest、routing 或 reason 输入复用为
独立 reason evidence，也拒绝输出覆盖任一输入。新增回归已穿过
`NO_SKILL/STATE_SHIFT` route → trigger report → P13 runner 的边界；这只证明
evaluation/shadow replay 可达，仍不会写 canonical memory 或 production runtime。

P13 runner 还要求每个 update manifest 的 `LocalizedUpdatePlan` 带可重算的
`plan_digest`，并要求 evidence 中的 `p12_shadow_trigger` 与当前 report 中同 case 的
receipt 完全一致；因此不能把一个合法 trigger、另一个 case 的 plan 或另一份 report
拼接后执行。该校验仍发生在打开 source SQLite 之前，保持 P13 仅为 isolated staging。

该入口已对现有真实 `p12g-orfs-v117-v119-training-20260903` receipt 重放：2 个 distinct
training lineage、typed route coverage 完整，但没有 Revision2 evolution signal，因此当前
v0.2 结果为 `trigger_count=2`、`triggered_count=0`、`p13_eligible=false`、
`blocked_reasons=["no_evolution_signal"]`。旧的 v0.1 replay 不能绕过这一门禁；下一步仍需
由独立、可审计的 campaign 记录生成绑定后的 reason receipt，并在真实 source-disjoint cohort 上重放，之后
才有资格进入 P13 isolated staging。

同时新增 `scripts/run_orfs_p12_cohort.py` 作为 manifest 驱动的真实 P12-F 执行入口。
它要求每个 case 显式冻结 project/source/toolchain/oracle/PDK 绑定，并为四个 arm 提供
候选文件或明确的 `null`；`ALWAYS_MEMORY` 不允许缺候选。候选通过
`StructuredRepairCandidate.from_dict()` 重放后才交给现有 ORFS oracle，输出顶层可直接
供 P13 replay 使用的 cohort receipt，同时保留候选文件 digest。该入口不打开 SQLite，
不推断 `NO_SKILL`，不写 canonical/lifecycle/production；执行失败或 UNKNOWN 仍作为原始
oracle evidence 保留，由后续 P12/P13 gate 决定是否可用。

若提供 `--routing-decisions`，runner 会在启动 ORFS 前要求 typed route 覆盖全部 case，
校验 `routing_receipt_id`、`no_skill_reason`、`state_shift_receipt_id` 与
`risk_receipt_id`，并把 route digest 摘要写入 paired receipt；缺失或漂移会在昂贵 flow
之前 fail-closed。

随后在固定 direct toolchain 上重放了 2 个 source-disjoint training lineage
（v117/v119），每个 lineage 的四个 arm 共 8 次 ORFS flow/signoff 均为 `PASS`，
`candidate_budget=3`、`lineage_count=2`、`UNKNOWN=0`，并保留了 case-level
`routing_receipt_id`。receipt 位于外部 campaign
`/data1/zhangdy/tehm-campaigns/tehm-p12f-runner-orfs-v117-v119-20260901/`，
cohort digest 为
`sha256:70c2bf50904b2bb1830a5a3f21f2f6d74ae9e527160a15f1459be221d8704e7a`。
将同一 receipt 与 typed routing decision 送入 P13 replay 后，结果是
`trigger_count=2`、`triggered_count=0`、`blocked_reasons=["no_evolution_signal"]`；
因此执行、source restore、lineage 与 route witness 已闭合，但没有独立的
Revision2 evolution reason，仍不能进入 shadow mutation，更不能写 canonical 或
production。下一步必须由独立可审计的事件产生 reason receipt（不能从这次全 PASS
结果反推），再在满足 anti-forgetting 的前提下重放 P13。

为降低人工拼接风险，`scripts/build_p13_evolution_reason_receipt.py` 接受预先独立
编写的 `p13-evolution-reason-label-manifest-v1`，重新解析当前 ORFS/RTL cohort，校验
campaign/cohort digest、逐 case reason 覆盖以及 evidence 文件 SHA256，再输出可直接
供 P13 replay 使用的 `P13EvolutionReasonReceipt`。该脚本拒绝 outcome、repair 和 gold
字段，不读取或推断 ORFS 结果；evidence 不能复用 cohort 或 labels manifest 本身，
输出也不能覆盖任一输入；没有外部 label manifest 时不会生成 reason。
该 receipt 仍兼容旧的全局 `evidence_refs`，但新 manifest 可额外提供
`case_evidence_refs`：每个 case 必须有独立、不可变且可重算 SHA256 的证据集合，
replay 会校验 case 集合完全覆盖，避免一份笼统事件被重用为所有 case 的演化依据。

### 2026-09-01 P13 typed Knowledge structural shadow executor

P13 shadow executor 现在可在隔离 staging 中消费 typed `MechanismKnowledge` 的
`REVISE`、`SPECIALIZE`、`GENERALIZE`、`SPLIT` 和 `MERGE` proposal。每个 claim 都由
`MechanismKnowledge.from_dict()` 严格解码，禁止 validated/production status 和
gold-answer 字段；parent object IDs、training evidence refs、split partition witness
及 multi-parent merge witness 必须显式绑定。same-claim revision 的版本也进入
inventory，结构变化则写入 `SPECIALIZES`/`GENERALIZES` relation，便于 receipt replay。

这些 Knowledge objects 和 relation edges 只存在于一次性的 SQLite staging copy，随后
立即丢弃；`canonical_memory_mutation=none`、lifecycle/authority 与 production runtime
均不变。该实现补齐了 Revision2 P13 的结构操作执行层，但没有把“执行成功”当作演化
原因或 promotion evidence；真实 ORFS cohort 仍须先获得独立 reason receipt 和四项
anti-forgetting witness，之后才能进入该 shadow lane。`memory/docs/` 继续仅作本地设计
输入，由 `.gitignore` 排除，不会提交到仓库。

### 2026-09-01 Knowledge authority replay ledger

新增 `tehm_knowledge_authority_evidence` 与
`tehm_knowledge_authority_receipts` 两个 additive ledger 表，以及
`record_knowledge_authority()` / `verify_knowledge_authority()`。Knowledge authority
现在绑定 claim content digest、target scope、status version、完整 claim evidence
refs 和可重放 gate projection；证据行与 authority receipt 通过 savepoint 原子写入，
相同 receipt 可幂等重放，冲突或篡改会 fail-closed。

`set_knowledge_status(status="validated")` 不再接受只有 `.eligible=true` 的纯计算
receipt，必须消费当前 status version 匹配且在 ledger 中存在的 authority receipt。
该 ledger 仍不提供 `promoted` 状态，也不自动晋级或导入 production runtime；
`memory/docs/` 仍由 `.gitignore` 排除，不进入提交。

### 2026-09-02 Revision3 P15-B production-readiness preflight

新增 `tehm.evaluation.production_readiness` 与
`scripts/audit_r3_production_readiness.py`，把进入 production shadow/canary 之前的
七类证据固定为可重放的 `PASS/FAIL/NOT_ESTABLISHED` 投影：multi-lineage、reason-
stratified calibration、MIR upper CI、repair Pareto、anti-forgetting、authority replay
和 rollback。它同时复用现有 `evaluate_production_gate()`，但不会把 summary boolean 当作
authority；所有输入文件和嵌套 cohort refs 都按 SHA-256 重放。当前真实 Revision3
证据审计结果为：multi-lineage、reason calibration、repair Pareto、anti-forgetting、
rollback=`PASS`，MIR upper CI=`FAIL`，authority replay=`NOT_ESTABLISHED`；因此整体
`eligible=false`，production gate 的 efficacy/candidate-pool/authority 仍未建立，且
`production_integration=not_attempted`。该 preflight 不创建 mirror、不修改 canonical
memory、不写 production runtime；`memory/docs/` 继续排除在提交之外。

### 2026-09-02 Revision3 P16 schema/contract freeze

新增 `tehm.schema_contract` 与 `scripts/freeze_r3_schema_contract.py`，冻结当前
`schema.sql` 字节摘要、可执行 SQLite object inventory、线性 migration chain 及可选
TEHM DB 的 read-only schema observation。P16 replay 会重新计算 schema/migration/object
digest，拒绝 WAL/SHM sidecar、缺失或额外 schema object、版本漂移和篡改 receipt；因此
它把后续 campaign 的输入契约固定下来，但不创建新 authority、不修改 canonical memory、
不导入 production runtime。报告显式写入 `memory_docs_submitted=false`、
`promotion_attempted=false`；`memory/docs/` 仍由 `.gitignore` 排除，不能进入提交。

### 2026-09-02 Revision3 Validation Cohort V0 freeze

新增 `tehm.evaluation.validation_freeze` 与
`scripts/freeze_r3_validation_cohort.py`，将完成的 all-PASS、source-disjoint
P12 cohort 和其 zero-trigger replay 绑定为 `lane=VALIDATION`、
`expected_action=RETAIN` 的 content-addressed negative-control receipt。冻结门会
重新校验四臂 outcome、至少两条 lineage、cohort digest、`triggered_count=0` 和
`no_evolution_signal`，并在 replay 时重新读取两个输入报告与校验文件 digest。
该 receipt 只证明 `execution != evolution`，不写 canonical memory、不导入
production runtime；输出字段明确为 `memory_docs_submitted=false`。

### 2026-09-02 Revision3 P1-R3 counterexample reason

新增 `CounterexampleReceipt` 与 `detect_counterexample()`：检测器必须同时绑定
显式 Knowledge prediction、`APPLICABLE` applicability witness、`BOUND` binding
witness、structured candidate execution，以及由 real oracle 提供的
`observed_outcome`/`observed_effects`。候选 `FAIL` 本身不会生成
`COUNTEREXAMPLE`；只有预测和完整 oracle 观察发生确定矛盾时才生成 typed reason。

`derive_counterexample_reason()` 和 admission gate 只产生 evaluation/shadow
证据，要求 learner-eligible、有效 applicability/binding 和完整 oracle contradiction，
不修改 canonical memory，也不改变 production authority。真实 Icarus/vvp challenge
位于 `scripts/run_r3_counterexample_challenge.py`，报告记录 candidate `FAIL`、两类
矛盾、source digest 不变及 `production_promotion_eligible=false`。

### 2026-09-02 Revision3 P1-R4 repeated-failure reason

新增 `RepeatedFailureReceipt` 与 `detect_repeated_failures()`：仅聚合当前
campaign 的 training/learner-eligible `FAIL/REGRESSION` transition，并通过
`require_verified_transition()` 重新确认每条证据具有完整 executable oracle；至少需要
两条 failure 且跨两个 lineage 或 resolution，不能把同一 case 重跑当作独立失败。新增
`derive_repeated_failure_reason()` 与 admission 分支后，`REPEATED_FAILURE` 不需要 P12
paired counterfactual，但仍不能直接修改 canonical memory 或 production authority。

`scripts/run_r3_repeated_failure_challenge.py` 对两个 RTL fixture 应用真实的
constant-false guard action，并由 Icarus/vvp 确认 target+regression aggregate `FAIL`；
外部 shadow SQLite 中得到 2 条完整 oracle failure、2 个 lineage、typed derivation 和
admission 均通过。该报告明确记录 `oracle_complete=[true,true]`、
`canonical_memory_mutation=none`、`production_promotion_eligible=false`，不将该负向
challenge 当作能力增益。

### 2026-09-01 State-shift observation and repeated-shift proposal seam

新增 `STATE_SHIFT_OBSERVED` 事件桥接与 `load_state_shift_observations()`，要求
state-shift receipt、canonical transition、campaign 和 learner partition 显式绑定；事件
只进入 append-only shadow log，不能扩展 SupportEnvelope 或改变 Knowledge authority。
新增 `propose_repeated_state_shift()`（别名 `plan_repeated_state_shift()`）：至少两份
相同 Knowledge、不同 resolution 的不可迁移 receipt 才能形成提案，并逐 case 绑定
no-memory/historical-memory oracle outcome 与 evidence refs。安全的重复迁移产生
`REVISE`（SupportEnvelope expansion），历史记忆不安全但 no-memory 正常时产生
`SPECIALIZE`；当前 oracle 不安全则 `RETAIN`，`SPLIT` 永不自动推断且必须提供显式
partition evidence。该提案器只读、evaluation-only、shadow-only，仍需 P13
anti-forgetting witness 才能尝试 isolated staging；`memory/docs/` 继续不入仓。
另提供 `propose_repeated_state_shift_from_events()`，从同一 campaign 的事件链按
transition 顺序重放上述提案；它拒绝 learner/audit 混合、缺失 event digest 或 receipt
ID witness，不能用调用方布尔值升级 audit-only 观测。

新增 `scripts/build_state_shift_evolution_proposal.py` 作为上述边界的可复现命令入口：
它只接受冻结的 TEHM SQLite 快照和显式 outcome/evidence manifest，先用
`connect_read_only()` 校验事件链，再输出带 source DB digest、proposal ID/digest 的
报告；可选地通过 `state_shift_proposal_to_localized_plan()` 输出 P13
`LocalizedUpdatePlan`。存在 WAL/SHM sidecar、source digest 漂移、缺失 event/receipt
witness、输入输出碰撞或 gold/repair 字段时均 fail-closed。该命令不会从 PASS/FAIL
推断 evolution reason，不写 canonical memory、authority 或 production runtime；生成的
plan 仍必须经过 P12 trigger 与 anti-forgetting gates 才能尝试 isolated staging。

命令也支持 `--paired-receipts`：输入 `p12-paired-receipts-map-v1`（按 transition ID
索引的 typed `PairedCandidateExecutionReceipt`）后，脚本从 paired 的
`NO_MEMORY`/`ALWAYS_MEMORY` arm 读取 oracle outcome，并自动绑定 paired、route 与
execution digests；此模式拒绝 manifest 中同时存在手写 outcome，避免人工标签覆盖真实
执行 receipt。

同时新增 `append_routed_state_shift_observation()`，用于把实际的 typed
`MemoryRoutingDecision(decision=NO_SKILL, no_skill_reason=STATE_SHIFT)` 绑定为
`STATE_SHIFT_OBSERVED`。它强制校验 route 的 `state_shift_receipt_id`、resolved
state 与 receipt 一致，并要求 route 携带与事件 receipt 完全相同的可 replay
`StateShiftReceipt` payload；完整 route digest/id 写入事件 payload，重放时再次解码
校验。旧的通用 append API 仍可用于兼容/审计，但没有完整 matching router witness 就
不能通过这个推荐的 8A.9 路径制造 state-shift teaching signal。

`propose_repeated_state_shift_from_paired_receipts()` 进一步把 P12 四臂
`PairedCandidateExecutionReceipt` 接到 proposal：只接受带 matching
`STATE_SHIFT` route witness、完整 `NO_MEMORY`/指定 historical-memory oracle、且共享
toolchain/oracle digest 的 paired cases，自动绑定两侧 execution digest，拒绝手写 outcome
错配。它仍是 evaluation-only proposal，不能替代 P12/P13 的 learner partition、reason
receipt 或 anti-forgetting gate。

### 2026-09-02 Revision3 policy semantics and typed evolution-reason derivation

P12 四臂现在按真实 policy 执行，而不是把每个 memory arm 都强制当作
`structured_memory`：`NO_MEMORY` 永不接受候选，`ALWAYS_MEMORY` 必须执行候选，
`APPLICABILITY_GATED` 可在无候选时执行 no-memory fallback，`CAUSAL_NO_SKILL` 在显式
`NO_SKILL`/`ABSTAIN`/`INAPPLICABLE` route 下执行 no-memory fallback，在
`APPLY`/`CONSIDER` 下才执行 memory。fallback receipt 的 metadata 记录 route、typed
reason、fallback reason 和被忽略的候选 ID；paired receipt 记录并校验
`routing_decision`，同时继续兼容旧的未带 route 字段 receipt。这样后续 state-shift 和
interference 统计测量的是 policy outcome，而不是被拒绝的 memory candidate outcome。

新增 `tehm.evolution.reason_derivation`：`EvolutionReasonDerivationReceipt` 是
content-addressed、evaluation-only、canonical-mutation-free 的 reason witness，输入
只能是已存在的 typed immutable receipt，结构上拒绝 mutation plan、replacement
Knowledge、shadow after-state 和 production authority 字段。当前已实现两个
deterministic detector：`derive_state_shift_reason()` 要求 non-transferable
`StateShiftReceipt` 与 matching `NO_SKILL/STATE_SHIFT` route；
`derive_memory_interference_reason()` 要求完整 oracle 的 paired receipt，并只在
`NO_MEMORY` 为正、forced memory 为 harmful 或创建 regression 时产生
`MEMORY_INTERFERENCE`。任何 `UNKNOWN` 或不完整 oracle 都不会产生 reason。

`p13_reason_receipt_from_derivations()` 可将每 case 的 typed detector receipts 聚合为
现有 `P13EvolutionReasonReceipt`，不再需要手写 label 才能形成 reason envelope；
`tehm.evolution.admission` 提供 reason-specific `EvolutionAdmissionReceipt`，当前对
`STATE_SHIFT` 强制 typed shift + route + paired counterfactual，对
`MEMORY_INTERFERENCE` 强制可重放 paired detector，其他 reason 在 v0.1 中保持拒绝而
不会放宽 mutation authority。上述所有路径仍是 shadow/evaluation-only，未改变
canonical memory 或 production runtime。

本 Revision3 还提供 `scripts/run_r3_memory_interference_challenge.py` 作为真实 RTL
Evolution Challenge 入口。脚本只复制 `rtl/` 与 `tb/`，不读取 fixture
`manifest.json` 中的 gold/fix 字段；它用 Icarus/vvp 对两个 source-disjoint lineage
执行固定 baseline 与 harmful memory counterfactual，并把 paired、typed
`MEMORY_INTERFERENCE`、P13 trigger 和 admission receipts 输出到仓库外的
`--artifacts` 目录。当前一次可重放结果为 2 cases / 2 lineages：`NO_MEMORY=PASS`
为 2/2、`ALWAYS_MEMORY=FAIL` 为 2/2、typed trigger=2/2、admission=2/2；该结果只
证明 detector/admission 链路能捕捉负迁移，不是 ORFS 全流程、能力增益或 promotion
证据。challenge 输出标记 `evaluation_only=true`、`canonical_memory_mutation=none`，
不写 canonical memory、authority 或 production runtime。

新增 `tehm.evolution.interference_revision` 与
`scripts/run_r3_memory_interference_shadow.py`，完成 Revision3 R3-8 的第一条真实
负迁移吸收链。该 proposal seam 只接受至少两个独立 lineage 的完整 paired
`NO_MEMORY=PASS` / forced-memory harmful receipts，并在重放
`derive_memory_interference_reason()` 后生成 `SPECIALIZE` / negative-applicability
计划；detector 不读取 replacement Knowledge、plan 或 shadow after-state。脚本在外部
SQLite 中注册 shadow parent，加入 `asap7` + `unguarded_completion_transfer` 负适用性
上下文，消费真实 Icarus/vvp 的 P12 trigger、admission 和四项 anti-forgetting witness，
再将 specialized child 与 `SPECIALIZES` relation 只写入 disposable staging。

一次实跑结果为 2 cases / 2 lineages：更新前 `ALWAYS_MEMORY=FAIL` 2/2，且
`APPLICABILITY_GATED` / `CAUSAL_NO_SKILL` 也因旧 route 执行 harmful candidate 而
`FAIL` 2/2；更新后强制 memory 仍保持 audit counterfactual `FAIL` 2/2，但两个真实
policy fallback 均为 `PASS` 2/2，负适用性 veto=2/2，safe fallback rate=1.0。
source DB row/digest 均未变化，`canonical_memory_mutation=none`、
`production_authority_changed=false`、`staging_discarded=true`；这证明的是
shadow-only 的“何时不信任 memory”机制，不是 production promotion 或能力增益。

本地 governing design 文档目录 `memory/docs/` 由根目录 `.gitignore` 排除，既不在
release tree 中，也不应被 `git add`；发布时只提交代码、README、测试和可复现脚本。

### 2026-09-02 Revision3 first real StateShift Evolution Challenge

新增 `scripts/run_r3_state_shift_challenge.py`，把 Revision3 的第一条真实
`STATE_SHIFT` teaching signal 链路固化为可重放命令：脚本先用两个 RTL fixture 的
真实 Icarus/vvp 结果捕获 training transitions，再构造 training-only SupportEnvelope，
将 `sky130` 支持域与 `asap7` 当前 flow 形成 typed `flow_shift`，并通过两个
source-disjoint lineage 执行四臂 P12。路由是显式
`NO_SKILL/STATE_SHIFT`，所以 `CAUSAL_NO_SKILL` 走 no-memory fallback；四臂均由
真实 oracle 完成，`ALWAYS_MEMORY` 与 `APPLICABILITY_GATED` 的结构化候选均为
PASS，随后生成 typed StateShift reason、P13 trigger/admission、重复 shift 的
`REVISE/SUPPORT_ENVELOPE_EXPANSION` proposal，并在外部 SQLite staging 中执行一次
Knowledge v2 revision。

同一命令还将 target replay、non-target regression、独立 held-out Icarus audit 和
rollback pointer 绑定为文件摘要，消费四项 anti-forgetting gate，随后由
`memory_delta_from_shadow_update()` 生成 C1 receipt。一次实跑结果为 2 cases / 2
lineages，两个 case 都是 `STATE_SHIFT`、trigger/admission 均 2/2、P13 operation
为 `REVISE`；canonical row counts 未变化、`canonical_memory_mutation=none`、
`production_authority_changed=false`、`staging_discarded=true`。脚本还在独立的
evaluation projection 中重放 child claim，持久化并 replay `StateResolution`，生成
新的 `CONSIDER` route、结构化 candidate、真实 Icarus PASS execution 与
`CandidateLineageReceipt`，因此当前 `L1_SELECTION_OR_L2_STRATEGY_EVOLUTION` 的
P14 C1–C5 五项结构 gate 已全部闭合。由于 StateShift cohort 的起点本来就是已修复
RTL，传统 capability attribution 的 `target_gain` 不被伪造（C5 capability、C6–C8
仍为未宣称）；这不是 production promotion、能力增益或 ORFS 经验积累。后续按
Revision3 继续真实 held-out/ΔMemory ablation 与 P15 reason-stratified calibration。
脚本只向 `--artifacts` 指定的仓库外目录写入证据，绝不复制 fixture `manifest.json`，
也不提交 `memory/docs/`。

### 2026-09-02 Revision3 R3-7 held-out/Delta-M 与 P15 calibration

`run_r3_state_shift_challenge.py` 现继续执行 Revision3 的 R3-7：在同一个外部
evaluation projection 中加入两个与 training/evolution source-disjoint 的 buggy
held-out lineage（`req_ack_bug3`、`req_ack_bug4`）。每条 lineage 都真实执行
`M_t=NO_MEMORY`、`M_t+1=typed guard candidate`，并再次执行 `M_t+1-DeltaM`；一次
实跑结果为 `M_t=FAIL`、`M_t+1=PASS`、移除 DeltaM 后再次 `FAIL`，严格 P14 capability
`C1..C8` 全部为 `true`。该结果证明的是冻结环境下的 source-disjoint transfer 与
Delta-M attribution，不是 production promotion；原来的 StateShift strategy
projection 仍明确不宣称自身已有 target gain。证据写入外部
`receipts/p14_heldout_delta_m.json` 与 `receipts/p14_capability_heldout_attribution.json`。

新增 `tehm.evaluation.no_skill_calibration.derive_no_skill_oracle_label()`，只消费
完整 typed `PairedCandidateExecutionReceipt`（可选 non-transferable
`StateShiftReceipt`），从 `NO_MEMORY`/`ALWAYS_MEMORY` 的真实 oracle outcome 推导
`USE_MEMORY`、`NO_MATCH`、`RISK` 或 `STATE_SHIFT`，不读取 router prediction，也不
写 support envelope。`scripts/run_r3_p15_calibration.py` 使用四个 source fixture
构造 20 条 calibration split，复制的 source 仅追加 case comment 以满足 source digest
不重复；每条都用 Icarus/vvp 执行四臂 P12，随后由 typed paired receipt 生成 oracle
label。实跑结果为 20 cases / 20 lineages，`NO_MATCH=5`、`RISK=5`、
`STATE_SHIFT=5`、`USE_MEMORY=5`，P15 receipt `status=PASS`、`eligible=true`、
confidence/routing coverage 均为 `1.0`，整体 correct rate 为 `1.0`（95% Wilson
下界 `0.838875`）。calibration manifest、label derivations 和报告全部位于外部
campaign；`canonical_memory_mutation=none`、`production_authority_changed=false`、
`production_promotion_eligible=false`。这一步仅闭合 P15-A 的真实 calibration
证据，不解锁 production canary，仍需后续 statistical production evidence 与显式
authority/promotion gates。`memory/docs/` 继续由 `.gitignore` 排除，不进入提交。

### 2026-09-02 Revision3 R3-9 CAPABILITY_GAP non-P12 admission

新增 `derive_capability_gap_reason()` 与
`EvolutionAdmissionReceipt(reason=CAPABILITY_GAP)`：它消费
`CapabilityGapReceipt` 的 learner-eligible training evidence，重新检查至少两个
独立 lineage、至少两条 repeated source-failure evidence、没有当前 eligible asset
或成功 action family，并要求 typed `NO_SKILL/NO_MATCH` route。该 reason 不要求 P12
paired counterfactual，避免
`NO_SKILL/NO_MATCH` 因不存在 memory candidate 而被错误挡在 P13 入口之外。

新增 `CapabilityGapEvolutionProposal`，只生成 `ADD` 的
`ASSET_OR_KNOWLEDGE` shadow proposal，并绑定 gap、derivation、admission 与 transition
evidence；proposal 不注册 Asset、不写 Knowledge、不改变 canonical memory，也不具备
production-runtime eligibility。真实入口 `scripts/run_r3_capability_gap_challenge.py`
在仓库外 disposable SQLite 中对 `req_ack_bug` 与 `req_ack_bug2` 执行 Icarus/vvp，产生
2 lineages / 2 source-failure evidence：`CAPABILITY_GAP` derivation=1、admission=1、
proposal=1，`paired_counterfactual_required=false`，`canonical_memory_mutation=none`，
`production_promotion_eligible=false`；source DB digest 保持不变。这里的 failure
evidence 明确指真实修复前的 `original_failure=REMOVED`，不把修复后的 PASS 冒充 unresolved
FAIL，也不宣称能力增益或 production promotion。

`memory/docs/` 仍是本地 governing input，由 `.gitignore` 排除，既不在 release tree
中，也不会被本阶段提交。

随后用未参与首批 gap 实验的 `req_ack_bug3` 与 `req_ack_bug4` 做了第二批
source-disjoint 复核，campaign 为 `tehm-r3-capability-gap-20260902-r2`。两个新
lineage（`req_ack_fsm3`、`req_ack_fsm4`）均由真实 Icarus/VVP capture 产生
`original_failure=REMOVED` 的 source-failure witness；`CAPABILITY_GAP` derivation、
non-P12 admission 和 `ADD/ASSET_OR_KNOWLEDGE` shadow proposal 均为 1/1，且
`paired_counterfactual_required=false`。source canonical digest 在整个流程中保持
不变（`d808d3e66b452577a7402bdc7c4fffd03b3f54123e165e5a1f0e63c88a7f8997`），报告
摘要为 `ac2136cabe2707355ebdb829eaa783c4bda7dd518d024fd30600cbd131549ca6`。
这只扩大了 capability-gap 的真实、多 lineage 证据，不注册新 asset、不写
canonical memory、不授予 production authority；`memory/docs/` 仍由 `.gitignore`
排除，不进入提交。

### 2026-09-02 Revision3 P15-B authority replay boundary hardening

production-readiness preflight 现在只接受真正由
`scripts/replay_rule_authority.py` 产生的只读 replay receipt：必须明确绑定
`tehm-rule-authority-replay-v1`、六项 rule gate 全部 `PASS`、authority database
前后摘要不变、`read_only=true`、`ALLOW_AUTHORITY_REVIEW` 且
`promotion_attempted=false`。单独的 `verified=true`、记录过但未 replay 的 authority
receipt、缺 gate 或可写数据库摘要均会得到 `authority_replay=FAIL`，不会被当成
production authority。该 preflight 仍只生成 evaluation receipt，不修改 canonical memory
或 production runtime；`memory/docs/` 继续排除在提交之外。

P15-B 另增加 `r3-policy-mir-v1` routed-policy witness。它绑定 post-revision typed P12
cohort、逐 case routing receipt、baseline/policy arm 和完整 executable oracle，重算
`harmful_cases`、unknown-safe denominator、routing coverage 与 Wilson upper CI；因此
`ALWAYS_MEMORY` 的故意 harmful counterfactual 不会再被误写成真实 policy MIR。即使
观测到零 harm，有限样本的 upper CI 仍不会被截断为零，样本不足时继续保持
`mir_upper_ci=FAIL`，不打开 production mirror/canary。该 witness 仍为 evaluation-only，
不修改 canonical memory，也不提交 `memory/docs/`。

### 2026-09-02 Revision3 frozen campaign snapshot boundary

R3 StateShift 与 Interference shadow runner 在成功写入外部 campaign 后统一执行
`PRAGMA wal_checkpoint(TRUNCATE)` 再关闭 SQLite。这样生成的 `tehm.sqlite` 不带
`-wal/-shm` sidecar，可直接作为 P16 schema/read-only proposal 的 immutable snapshot；
若 checkpoint 失败则不会把未冻结数据库当成完成证据。StateShift anti-forgetting 的
rollback witness 现在绑定源库逻辑 dump digest，而不是仅绑定表计数，且该 digest 与
shadow update 前后不变。新外部验证结果位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-state-shift-challenge-20260902-r41/` 和
`tehm-r3-interference-shadow-p15b-20260902-r3/`：两条真实 Icarus cohort 均保持
`canonical_memory_mutation=none`、`production_authority_changed=false`、
`staging_discarded=true`，P16 freeze/replay 成功；readiness 仍因 MIR 样本量和缺少
独立 authority replay 保持 fail-closed。基于这两个最新快照重建的
`tehm-r3-production-readiness-r41-20260902/readiness.json` 已 replay 成功，状态仍为
`multi_lineage/reason_stratified_calibration/repair_pareto/anti_forgetting/rollback=PASS`、
`mir_upper_ci=FAIL`、`authority_replay=NOT_ESTABLISHED`，没有打开 production mirror。
MIR 上限现在作为 `--max-mir-upper-ci` 显式参数写入 readiness/production-gate
receipt，并在 replay 时重新读取；默认值仍是 `0.0`，因此有限样本的 Wilson upper CI
不会被截断为零，也不会因为新增参数而放宽当前 fail-closed 结论。

### 2026-09-02 Revision3 P2-R6 novelty/conflict typed adapters

保留原有 `detect_novelty()` / `detect_conflicts()` 判定逻辑，只新增可重放的
`NoveltyReceipt`、`ConflictReceipt`（含 lineage、content digest 与 tamper check），并
通过 `derive_novelty_reason()` / `derive_conflict_reason()` 接入统一
`EvolutionReasonDerivationReceipt`。`NOVELTY` 只接受无 learner path 的 typed
`NOVEL_MECHANISM`，`CONFLICT` 必须有冲突类型和独立 transition evidence；两条路径都
需要 learner admission，但都不要求 P12 paired counterfactual，也不携带 mutation plan。

`scripts/run_r3_reason_adapter_challenge.py` 在外部 disposable SQLite 中用两个真实
Icarus/vvp RTL fixture 运行：第一个产生 `NOVELTY`，第二个产生
`DEFINITION_CONFLICT`，两条 derivation/admission 均可 replay。该实跑只验证 reason
adapter 与 admission 的 typed provenance，不代表 conflict 已自动修改 Knowledge；
`canonical_memory_mutation=none`、`production_promotion_eligible=false`，source DB
digest 保持不变。`memory/docs/` 仍由 `.gitignore` 排除，不进入提交。

### 2026-09-02 Revision3 typed derivation replay binding

正式 typed P13 trigger 入口现在不再只信任 `typed-detector:` 前缀和
`receipt://` 引用形状；调用方必须同时提供底层
`EvolutionReasonDerivationReceipt`，入口会重放其 content-addressed ID/digest、
campaign/case、reason 集合以及 per-case/global evidence refs。缺失、跨 campaign、
重复或伪造引用都会 fail-closed；手工/audit manifest 仍仅保留在兼容性的旧入口，
不会被误认成 typed detector 输出。

该边界已由 21 条 P12/reason 定向测试、53 条 P13/P14 回归测试覆盖，并在真实
Icarus campaign 中重放：StateShift 为 2 cases / 2 lineages，typed derivation、
trigger、admission 均 2/2；Interference 为 2 cases / 2 lineages，自动
`MEMORY_INTERFERENCE` derivation/admission 均 2/2，shadow 后 routed policy
harmful=0/2。新证据位于仓库外的
`tehm-r3-state-shift-challenge-20260902-r41/`、
`tehm-r3-interference-challenge-20260902-r2/` 和
`tehm-r3-interference-shadow-p15b-20260902-r3/`；canonical/source DB 均未改变，
仍为 evaluation/shadow-only，`memory_docs_submitted=false`。

### 2026-09-02 Revision3 P15-B routed-policy MIR source-disjoint aggregation

新增 `tehm.evaluation.policy_mir` 与
`scripts/aggregate_r3_policy_mir.py`，将 routed-policy 的 MIR 分母提升为多个独立
typed `RtlPairedCohortReceipt` 的可重放聚合，而不是继续信任单个 summary 中的标量。
聚合器逐 cohort 校验文件摘要、receipt digest、campaign/case 唯一性、source digest
不重叠、固定 toolchain/oracle/platform/PDK/candidate budget、逐 case routing receipt、
`NO_MEMORY` baseline 与完整 executable oracle，再重算 known/unknown/harmful 与 Wilson
upper CI；任何漂移、重复或篡改都会 fail-closed。新增
`scripts/build_r3_policy_mir_interference.py` 只把已 replay 的 aggregate 包进
`MEMORY_INTERFERENCE` evaluation envelope，不写 canonical memory、不改变 authority、
不导入 production runtime。

新增 `scripts/run_r3_routed_policy_cohort.py`，使用真实 `iverilog/vvp` 从四个 held-out
RTL fixture 中分两批生成 source-disjoint cohort。实跑的 r1/r2 各为 2 cases / 2 lineages，
`NO_MEMORY=PASS` 与 routed `CAUSAL_NO_SKILL=PASS` 均为 2/2，routing receipt coverage
为 1.0；两批合并为 4 cases / 2 cohorts、known=4、harmful=0、upper CI=0.489891。
最新 readiness r42 已从该 aggregate replay：`multi_lineage`、
`reason_stratified_calibration`、`repair_pareto`、`anti_forgetting`、`rollback` 仍为
`PASS`，但默认 `max_mir_upper_ci=0.0` 下 `mir_upper_ci=FAIL`，
`authority_replay=NOT_ESTABLISHED`，整体 `eligible=false`，没有进入 production mirror。
这些外部 evidence 位于 `/data1/zhangdy/tehm-campaigns/`；`memory/docs/` 继续只作本地
governing input，由 `.gitignore` 排除且不提交。

### 2026-09-02 Revision3 real RTL authority replay

新增一次外部 disposable authority campaign：`scripts/run_rtl_campaign.py` 用真实
Icarus/vvp 在 `req_ack_bug`、`req_ack_bug2` 上训练，并在 source-disjoint held-out
`req_ack_bug3` 上运行 3 次独立 A/B。三次 B 臂均为 `PASS`，`rollback_verified=3/3`、
`obligation_coverage=1.0`、registry 保持 `candidate`；结果写入
`/data1/zhangdy/tehm-campaigns/tehm-r3-authority-rtl-20260902/`，没有 canonical
memory 或 production promotion。

该 trial 现在可投影为真实的部分 authority receipt，并由
`scripts/replay_rule_authority.py` 只读重放：`rollback_verified`、
`registry_verified`、`obligation_coverage` 为 `PASS`，`cross_lineage_te`、
`harmful_rate`、`conformal_coverage` 明确为 `NOT_ESTABLISHED`，最终
`DENY_CANONICAL_IMPORT`、`promotion_attempted=false`、数据库摘要不变。修正了
trial authority projector 对真实捕获中的 `utility_verdict=UNKNOWN` 的处理：它现在
表示 utility gate 尚未建立，而不是 malformed evidence；新增回归测试覆盖该 fail-closed
语义。下一步仍需独立 held-out transfer、harmful-rate 与 conformal calibration 证据，
才能建立六项 gate；`memory/docs/` 依旧由 `.gitignore` 排除，不进入提交。

### 2026-09-03 Revision3 authority semantic/action family binding

修正 rule authority 的 cross-lineage 绑定边界：causal path 与 source transition
episode 现在绑定观测到的语义机制族（例如 `HANDSHAKE_COMPLETION`），而 rule 的
`type`/`transformation_family` 只用于绑定实际可执行动作（例如
`GUARD_STRENGTHEN`）。二者不再被错误地要求字符串相等；held-out transition 的
action family 仍必须命中 rule 的可执行 family，source transition 仍必须逐条通过
verified-execution 与 learner firewall。这保持了 matcher 中 semantic mechanism 与
executable transformation 的严格分离，同时拒绝缺失或混合的 source mechanism witness。

在外部 disposable campaign
`/data1/zhangdy/tehm-campaigns/tehm-r3-authority-rtl-20260902-r2/` 上重放：真实
Icarus/vvp 训练、两个独立 training lineages、L3 replicated path、L4 held-out
transfer 与三次 A/B rollback 均保留；新的 authority receipt
`rule_authority_159b67752aba490af1ba` 将 `rollback_verified`、`registry_verified`、
`obligation_coverage`、`cross_lineage_te` 置为 `PASS`，`harmful_rate` 与
`conformal_coverage` 为 `NOT_ESTABLISHED`，因此整体 `eligible=false`。只读 replay
保持 `database_unchanged=true`、`DENY_CANONICAL_IMPORT`、`promotion_attempted=false`，
没有 canonical memory 或 production runtime 变化。`memory/docs/` 继续是本地
governing input，由 `.gitignore` 排除，不能进入提交。

### 2026-09-03 Revision3 RTL paired utility witness

补齐 RTL external A/B adapter 的 utility provenance：候选 transition 现在只在
control/candidate 两个 Icarus oracle 都给出确定 `PASS`/`FAIL` 后，由固定的
`_derive_rtl_utility_verdict()` 规则派生 `PARETO_SAFE`、`HARMFUL` 或 `NEUTRAL`；任一
臂为 `UNKNOWN` 时保持 `UNKNOWN`，绝不把单臂成功当作 utility。该 verdict 与
`experiment_kind=REPAIR` 一起写入 durable `observation_delta`，authority replay 因而
能从真实 paired witness 重算 harmful-rate，而不是读取 caller gate map。

在外部 disposable campaign
`/data1/zhangdy/tehm-campaigns/tehm-r3-authority-rtl-20260903-r5/` 中，以未参与
训练的 `req_ack_bug4` 运行 3 次真实 Icarus/vvp A/B，三次 utility 均为
`PARETO_SAFE`。新的 authority receipt
`rule_authority_cef5ba1450d13e9e58f0` 将
`rollback_verified/registry_verified/obligation_coverage/cross_lineage_te/harmful_rate`
均置为 `PASS`；`conformal_coverage=NOT_ESTABLISHED`，所以仍保持
`eligible=false`。只读 replay 为 `database_unchanged=true`、
`DENY_CANONICAL_IMPORT`、`promotion_attempted=false`，没有 canonical memory 或
production runtime 变化。conformal 仍需独立 calibration cohort，不能由这 3 次 A/B
结果推导。`memory/docs/` 仍由 `.gitignore` 排除，不进入提交。

### 2026-09-03 Revision3 P15 calibration statistical expansion

将 `scripts/run_r3_p15_calibration.py` 的 cohort 大小参数化（默认仍为历史 20
case，允许显式 `--case-count`，上限 100），用于统计敏感性实验而不改变任何生产
阈值。用当前 Revision3 training freeze 运行 `--case-count 40`，得到 40 条真实
Icarus/vvp paired calibration receipt、40 个显式 lineage，`NO_MEMORY`、各 memory
arm 与 typed oracle label 均无 `UNKNOWN`；整体正确率 Wilson 95% 下界为 `0.912378`，
reason-aware precision/recall 下界均为 `0.886487`，因此
`reason_stratified_calibration=PASS` 已在最新 readiness replay 中建立。

对应 readiness receipt 为
`r3_production_readiness_30ba9d4bbe33fdd042c9ae65`（digest
`sha256:30ba9d4bbe33fdd042c9ae65c4b78185facd980dd556a124c7900be8236b4e99`）；
`multi_lineage/repair_pareto/anti_forgetting/rollback` 为 `PASS`，但
`mir_upper_ci=FAIL`、`authority_replay=FAIL`，整体仍 `eligible=false`。该 cohort
只写 `/data1/zhangdy/tehm-campaigns/tehm-r3-p15-calibration-20260903-n40/`，
`canonical_memory_mutation=none`、`production_authority_changed=false`、
`production_promotion_eligible=false`；P15 router calibration 不能替代当前 RTL
rule 的 conformal authority evidence，也没有打开 production runtime。
`memory/docs/` 继续由 `.gitignore` 排除，不进入提交。

### 2026-09-03 Revision3 RTL conformal binding firewall

补强 `lifecycle/rule_authority.py` 的 external conformal projector：带有
`rtl.*` action 的 conformal row 现在必须同时携带方法名、`sha256:` calibration
digest，以及完全一致的 action domain、executable transformation family 和
compatibility profile；缺失或 relabel 都 fail-closed。旧的未绑定 DRC/兼容 fixture
仍可重放，但这条 contract 只保证证据身份边界，不把 metadata 当作 coverage，也不
生成 RTL conformal 数值。当前 r5 campaign 没有同域 conformal receipt，因此 gate
仍为 `NOT_ESTABLISHED`，canonical memory、production authority 和 runtime 均未改变。
`memory/docs/` 仍只作为本地 governing input，由 `.gitignore` 排除，永不提交。

### 2026-09-03 Revision3 real RTL conformal calibration

新增 `tehm.rtl.conformal` 与 `scripts/run_rtl_conformal_calibration.py`：在独立的
campaign-local staging DB 中用 Icarus/vvp 执行 6 个未参与 r5 authority 的
`rtl.GUARD_STRENGTHEN` fixture lineage，校准 target-test、frozen-regression 和
compile 三个 typed executable obligations。最新 r4 campaign 为 6/6 lineage、
18/18 obligation labels 覆盖，receipt 为
`sha256:ed6f5899859f5021868bbda207850be83c7155dadcb7acb3e9d0aed3e1409620`，
calibration manifest digest 为
`sha256:6c3f747f862c9813d7a3a3fe6daff107ba6a5cd2b60f3dbd1c831ed09c5a3a70`。

外部 projector 现在还会重放完整 content-addressed calibration receipt，并校验
case/lineage、action domain/family/profile 与 coverage 一致；基于该 r4 source 的
authority receipt `rule_authority_19d3db008cd68c05c7fe` 六项 gate 全部 `PASS`，只读 replay 为
`ALLOW_AUTHORITY_REVIEW` 且数据库未改变。该证据仍是 review-only：canonical memory
没有变化，production runtime 未导入，promotion 未尝试。

接入该 authority replay 的最新 readiness receipt
`r3_production_readiness_144b5ad29855a52ca9de900ef94cd6b962da63ee33747cc1939fd84d8b5179b3`
中 `authority_replay=PASS`，但 `mir_upper_ci=FAIL`，所以整体仍
`eligible=false`、`production_integration=not_attempted`。这一步闭合了 RTL 同域
conformal evidence 缺口，但不替代 MIR 上界、held-out attribution 和其它生产 gate。
最新 campaign artifacts 位于 `/data1/zhangdy/tehm-campaigns/tehm-r3-rtl-conformal-calibration-20260903-r4/`
及对应的 disposable authority snapshot；`memory/docs/` 继续由
`.gitignore` 排除，不进入提交。

### 2026-09-03 Revision3 routed-policy MIR replay binding

补强 `tehm.evaluation.policy_mir` 的 routed-policy replay：MIR 聚合现在除检查
`routing_receipt_id` 外，还必须重放合法的 routing decision，并核对
`CAUSAL_NO_SKILL` 的实际 source/fallback 与 `NO_SKILL`、`ABSTAIN`、`INAPPLICABLE` 或
`APPLY`、`CONSIDER` 决策一致；缺失 route、缺失 fallback witness 或 metadata 错配均
fail-closed。该约束防止将 forced-memory 或未证明的 no-memory 结果伪装成真实 policy
MIR 分母；production-readiness 对兼容的 v1 routed witness 也复用同一校验。既有
4-case/2-cohort aggregate、P0 smoke、Validation V0、P16 schema contract 和最新
production-readiness replay 均通过；MIR upper-CI 仍按预注册的
`max_mir_upper_ci=0.0` 保持 `FAIL`，没有打开 production runtime。`memory/docs/`
继续只作本地 governing input，不进入提交。fresh route-semantics replay 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-routed-policy-cohort-20260903-route-hardened/`：
4/4 `NO_MEMORY=PASS`、4/4 `CAUSAL_NO_SKILL` fallback=PASS，作为 adapter/replay
验证而非新的独立统计分母。

### 2026-09-03 Revision3 real selected-memory routed cohort

为补齐上一轮只覆盖 no-memory fallback 的路径，`run_r3_routed_policy_cohort.py`
现在提供显式 `--routing-decision CONSIDER` 模式：复制的 held-out RTL 保持原始
buggy source，route 记录一个 selected asset，只有 disposable oracle 内的结构化
`GUARD_STRENGTHEN` candidate 才可改写 source；`NO_MEMORY` baseline 不做预修复。
该模式仍不读取 fixture manifest、不写 SQLite/canonical memory，也不触碰 production
authority。新增 producer 回归锁定了 source mode 与 causal memory arm 的绑定。

真实 `iverilog/vvp` campaign 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-routed-policy-selected-memory-20260903/`：
4 cases / 4 lineages，四例均为 `NO_MEMORY=FAIL`、`ALWAYS_MEMORY=PASS`、
`APPLICABILITY_GATED=PASS`、`CAUSAL_NO_SKILL=PASS`，四例 candidate 均记录
`action_applied=true`。独立聚合
`/data1/zhangdy/tehm-campaigns/tehm-r3-policy-mir-selected-memory-20260903/`
得到 `known=4`、`harmful=0`、`routing_receipt_coverage=1.0`、Wilson
`upper_ci=0.489891`；该 cohort 没有与旧 fallback cohort 合并，避免重复
held-out lineage 被错误扩大分母。对应 readiness
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-selected-memory-20260903/readiness.json`
仍为 `eligible=false`：`mir_upper_ci=FAIL`（预注册 `max_mir_upper_ci=0.0`），其余
`multi_lineage`、`reason_stratified_calibration`、`repair_pareto`、
`anti_forgetting`、`authority_replay`、`rollback` 均为 `PASS`。这建立了真实
selected-memory 的安全/有效性观测，但尚不足以开启 production runtime；
`memory/docs/` 继续只作本地 governing input，不进入提交。

### 2026-09-03 Revision3 interference shadow upgraded to typed MIR v2

将 `scripts/run_r3_memory_interference_shadow.py` 的新鲜 shadow producer 从 legacy
v1 compact MIR witness 升级为正式 `r3-policy-mir-v2` aggregate：它直接绑定本次
post-revision `RtlPairedCohortReceipt` 文件摘要、receipt digest、route semantics、
固定 oracle/toolchain 与逐 case known/unknown/harmful 重算结果；v1 仍只作为旧证据的
兼容 replay 路径。新的
`/data1/zhangdy/tehm-campaigns/tehm-r3-interference-shadow-p17-v2-20260903/`
实跑仍由真实 `iverilog/vvp` 产生 `MEMORY_INTERFERENCE` 2/2，reason admission 2/2，
shadow `SPECIALIZE` 创建 typed child/relation；post-revision 的
`APPLICABILITY_GATED` 与 `CAUSAL_NO_SKILL` 均 2/2 通过安全 fallback，
`canonical_counts_unchanged=true`、`source_db_unchanged=true`、`staging_discarded=true`。

该 v2 policy MIR replay 为 `known=2`、`harmful=0`、`routing_receipt_coverage=1.0`、
Wilson `upper_ci=0.65762`；对应 readiness
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-interference-p17-v2-20260903/readiness.json`
仍 `eligible=false`，仅 `mir_upper_ci=FAIL`，其余 gate 均 `PASS`。这一步只提升
evidence contract 与 replay 强度，不将 shadow child 导入 canonical memory 或
production runtime；`memory/docs/` 仍不提交。

将 selected-memory 的 4 个 held-out lineage 与 interference shadow 的 2 个
post-revision lineage 做跨 cohort source/lineage/campaign disjoint 检查后，正式 v2
混合聚合位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-policy-mir-p17-v2-mixed-20260903/`：
`cohort_count=2`、`known=6`、`harmful=0`、`routing_receipt_coverage=1.0`、Wilson
`upper_ci=0.390334`，只读 replay 一致。它把 selected-memory 的真实 candidate
执行和 negative-applicability fallback 放在同一安全分母中，但没有与旧的重复
held-out lineage 合并；对应 readiness
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-p17-v2-mixed-20260903/readiness.json`
仍保持 `eligible=false`，因此不能据此开启 production。

### 2026-09-03 Revision3 MIR statistical sample-size plan

新增 evaluation-only 的 `tehm.evaluation.mir_sample_plan` 与
`scripts/plan_r3_policy_mir_samples.py`。脚本首先只读重放 typed
`r3-policy-mir-v2` aggregate，再以与 production readiness 完全相同的 Wilson 95%
上界和严格 `< threshold` 判断生成 content-addressed 规划 receipt；它不修改任何
MIR gate、canonical memory、production authority 或 runtime。

针对当前混合 aggregate（`known=6`、`harmful=0`、`upper_ci=0.390334`），外部计划
`/data1/zhangdy/tehm-campaigns/tehm-r3-policy-mir-sample-plan-p17-20260903/plan.json`
记录了在“不再新增 harmful case”的明确假设下：上界阈值 `0.10/0.05/0.02/0.01`
分别至少需要 `35/73/189/381` 个独立 known cases（相对当前还需
`29/67/183/375` 个）。注册的默认阈值 `0.0` 同时被记录为
`finite_wilson_upper_bound_is_positive`，没有有限样本解；因此当前 production
仍然严格关闭，而不是用规划结果替代实证或放宽门禁。该计划 receipt 可用同一脚本
`--replay` 只读复核，`memory/docs/` 仍由 `.gitignore` 排除、不进入提交。

### 2026-09-03 Revision3 diverse routed-policy challenge cohort

为获得不重复旧 cohort 的真实 routed-policy 观测，新增
`scripts/run_r3_routed_policy_diverse_cohort.py`。它不读取任何 fixture
`manifest.json`，而是把 14 个明确审计过的 P3 RTL action descriptor 固化在 producer
中，覆盖 `GUARD_STRENGTHEN`、`PRIORITY_REORDER`、`RESET_RESTORE` 和
`WIDTH_CORRECT`；每个 source 都复制到 disposable 目录并真实执行四个 P12 arms。
这不是 all-PASS validation 扩张：14 个 case 的 baseline 均为 `NO_MEMORY=FAIL`，
三种 memory arms 均为 `PASS`，且 14 个 lineage/source digest 与既有三 cohort
aggregate 不重叠。

真实 campaign 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-routed-policy-diverse-20260903/`，typed
cohort digest 为
`sha256:ad15ffa8b8eaf7f404b2b017426b2fd1e269923fcb069f81693c7e00d013a75d`。
与既有 selected-memory 4 case、interference post-revision 2 case 合并后，新的
只读 v2 MIR aggregate 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-policy-mir-p17-v3-diverse-20260903/`：
`cohort_count=3`、`known=20`、`harmful=0`、`routing_receipt_coverage=1.0`、Wilson
`upper_ci=0.161125`。这降低了上界但仍不足以通过注册的 `0.0` 阈值；production
runtime、canonical memory 和 authority 均未改变。

这里的“独立”严格限于 receipt gate 已证明的 source/lineage/campaign 不重叠；这些是
仓库内 P3 challenge fixtures，因此不把它们表述成 IID 抽样、真实用户分布或已经足够
开启 production 的 evidence。规划器中的样本量只是给定该明确假设后的统计敏感性结果。

该 cohort 还使 `rtl.RESET_RESTORE` 的 begin/end 路径进入真实验证；修复了 action
层缺失 `_balanced_end` 的异常，并增加对应 parser regression。`memory/docs/` 仍仅
作本地 governing input，不进入提交。

### 2026-09-03 Revision3 real external ORFS interference challenge

为把 Evolution Challenge 从仓库 RTL fixture 推进到真实外部 ORFS source，新增
`scripts/run_r3_orfs_interference_challenge.py`。脚本只接受显式 ORFS project，
从 `constraints/config.mk` 解析并 digest-bind 外部 `VERILOG_FILES`，不读取 campaign
manifest 或 gold fix；每个 case 在同一固定 ORFS/OpenROAD/Yosys/PDK pin 下运行四个
P12 arm。预注册的 challenge candidate 是 `flow.CONFIG_DELTA` 的
`CORE_UTILIZATION=99`，用于检测负迁移，不是 canonical memory rule。

真实 campaign 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-challenge-20260903/`，
覆盖 ORFS 自带的 `uart` 与 `fifo` 两条 source-disjoint lineage。四臂结果为：
`NO_MEMORY=PASS` 2/2，`ALWAYS_MEMORY=FAIL` 2/2，`APPLICABILITY_GATED=FAIL` 2/2，
`CAUSAL_NO_SKILL=FAIL` 2/2，且 `UNKNOWN=0`；真实 paired receipt digest 为
`sha256:b2d521ca585ecd167047eccd308c0c07723f4bfbd3ef7606cd3550c84364169a`。
因此 detector 从 paired oracle 自动派生 `MEMORY_INTERFERENCE` 2/2，P13 typed
trigger 2/2，reason-specific admission 2/2；`canonical_memory_mutation=none`、
`production_runtime_imported=false`，这一步只证明真实 ORFS 负迁移检测链闭合，不能
宣称 production 安全率或 capability gain。

同时修正 `orfs_candidate_oracle` 的临时工程 basename：每个 case/候选 arm 使用由
case 与 candidate identity 派生的独立 `FLOW_VARIANT`，避免并发 P12 arm 误共享
R2G workspace lock。`memory/docs/` 继续只是本地 governing input，不进入提交。

### 2026-09-03 Revision3 external ORFS interference isolated shadow

新增 `scripts/run_r3_orfs_interference_shadow.py`，把上一阶段真实 ORFS paired
receipt 继续送入 P13：脚本在 disposable SQLite 中捕获两条真实 ORFS training
pair，重放 `MEMORY_INTERFERENCE` derivation、proposal、typed trigger 和
reason-specific admission，并执行 `SPECIALIZE` 到隔离 staging。post-revision
cohort 重新运行同一 ORFS case：`NO_MEMORY=PASS` 2/2、`ALWAYS_MEMORY=FAIL` 2/2，
而 `APPLICABILITY_GATED` 与 `CAUSAL_NO_SKILL` 都是实际 `source=no_memory` 的
fallback 且 `PASS` 2/2；另有一条 source-disjoint held-out baseline 通过。

外部 campaign 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-shadow-20260903/`，
`SPECIALIZE` 创建 `knowledge:r3-orfs-density-relief-specialized@1` 和一条 typed
relation，`memory_delta_eligible=true`、anti-forgetting eligible，且
`canonical_counts_unchanged=true`、`source_db_unchanged=true`、
`staging_discarded=true`、`production_runtime_imported=false`。这只是外部 ORFS
Evolution Challenge 的隔离 shadow 证据，不是 promotion 或 production claim。
`memory/docs/` 继续由 `.gitignore` 排除，永不提交。

### 2026-09-03 Revision3 external ORFS interference P14 attribution

新增 `scripts/run_r3_orfs_interference_attribution.py`，只读重放上一阶段真实
ORFS shadow receipt，并把 `EvolutionReasonDerivationReceipt`、P13
`AppliedShadowUpdateReceipt`、`MemoryDeltaReceipt`、SPECIALIZE 后的 state
resolution、policy snapshot/load receipts 串成 evaluation-only P14 链。所有写入
都发生在外部 disposable projection SQLite，不改变 shadow 源库；本次 replay
确认 `source_unchanged=true`、`strategy_attribution_eligible=true`。policy 的
`M_t/M_t+1` 标签继续绑定不可变 P13 `MemoryDeltaReceipt`；projection SQLite
只用于重放 child/state 可加载性，不把重放时生成的临时时间戳伪装成 canonical digest。

外部结果位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-p14-20260903/`：真实
ORFS 的 harmful `ALWAYS_MEMORY=FAIL` 被 SPECIALIZE 后的
`INAPPLICABLE` route 拦截，gated arms 使用 `source=no_memory` 并 `PASS`。这属于
`L2_STRATEGY_EVOLUTION` 的安全路由证据；标准 capability attribution 明确保留
`C5/C6/C8` 未通过，因而没有 L3 capability gain、promotion 或 production
runtime claim。`memory/docs/` 仍由 `.gitignore` 排除，永不提交。

### 2026-09-03 Revision3 external ORFS P15 calibration slice

新增 `scripts/run_r3_orfs_p15_calibration.py`，把外部 ORFS interference challenge
的冻结 `CONSIDER` routing receipt 与真实 paired `NO_MEMORY/ALWAYS_MEMORY` oracle
分开装配：oracle 自动派生两条 `NO_SKILL/RISK` 标签，未读取 router 预测来生成
标签，也未回流 training memory。

外部报告位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-p15-calibration-20260903/`：
`sample_count=2`、`routing_receipt_coverage=1.0`、`calibration_error=0.95`，两条
旧 `CONSIDER` 预测均被独立 oracle 判为 `NO_SKILL/RISK`，且 `NO_MATCH` 与
`STATE_SHIFT` 分层缺失，receipt 为 `NOT_ESTABLISHED`。这是一条真实 backend-specific
负校准证据，不是完整 P15 或 production readiness；post-revision 的
`INAPPLICABLE` safety veto 仍保持在二元 P15 contract 之外，canonical memory、
authority 与 production runtime 均未改变。

### 2026-09-03 Revision3 cross-backend P15 calibration aggregate

新增 `scripts/aggregate_r3_calibration.py`，只读取两个已经 replay 过的 typed
calibration manifest，将 RTL n=40 与外部 ORFS n=2 作为不同 campaign 合并；脚本
检查 campaign/case ID 不重叠，并保留每个输入 manifest 及其 evidence refs 的摘要，
不会把 calibration 样本写回 learner memory。

外部 aggregate 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-p15-calibration-cross-backend-20260903/`：
`sample_count=42`、`case_id_disjoint=true`、三类 reason 均达到最低支持，receipt
为 `PASS`，`calibration_error=0.002381`。其中 ORFS 两条样本仍保留旧
`CONSIDER → NO_SKILL/RISK` 的真实 false-negative，未被重标；aggregate 只是
cross-backend 统计 evidence，不能替代 MIR upper-CI、candidate pool 或 authority，
`production_promotion_eligible=false`。

同时，`tehm.evaluation.production_readiness` 已支持对该 cross-backend aggregate 做
严格回放：重新校验子 manifest 的文件 digest、typed routing/oracle triplet、RTL/ORFS
cohort 的 case/lineage/source-disjoint 绑定，并从样本重算 calibration receipt，而不
信任 aggregate 的汇总布尔值。新的 readiness 预检位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-cross-backend-20260903/`；
`multi_lineage` 与 `reason_stratified_calibration` 为 `PASS`，但 MIR Wilson upper-CI
仍为 `FAIL`，所以 `eligible=false`、production integration 仍为
`not_attempted`。`memory/docs/` 继续仅作本地 governing input，已由
`.gitignore` 排除且未被 Git 追踪。

### 2026-09-03 Revision3 P15-B calibration recall expansion

为补齐 production gate 中独立的 NO_SKILL recall 统计证据，新增外部 41-case RTL
calibration campaign，并与前述 2-case ORFS slice 重新聚合；ORFS 的两条旧
`CONSIDER → NO_SKILL/RISK` false-negative 保持不变，没有通过重标修复指标。新证据位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-p15-calibration-cross-backend-n41-20260903/`，
总样本为 43、case ID disjoint、RTL/ORFS 两个 backend、43 条 lineage，receipt
`status=PASS`，`calibration_error=0.003488`。NO_SKILL recall 为 `0.939394`，95%
Wilson 下界为 `0.803938`，因此 production gate 的 `no_skill_calibration` 从
`FAIL` 转为 `PASS`。

基于该 calibration aggregate 重新生成并 replay 的 readiness 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-cross-backend-n41-20260903/`；
`multi_lineage`、`reason_stratified_calibration`、`no_skill_calibration` 及既有
anti-forgetting/authority/rollback/Pareto 均通过，但 MIR Wilson upper-CI 仍未满足
注册的 `0.0` 阈值，candidate-pool/efficacy/authority production checks 仍未建立，
`eligible=false`、`production_integration=not_attempted`。该统计扩展仍是
evaluation-only，不写 canonical memory、authority 或 runtime；`memory/docs/` 继续
由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 P15-B routed-policy MIR sample expansion

为推进文档要求的统计 production evidence，`run_r3_routed_policy_diverse_cohort.py`
新增可审计的 `--cohort-tag`：tag 会同时进入复制 source 的非执行注释、case ID 和
lineage ID，避免第二批样本与原 cohort 的内容/身份绑定混淆，但不改变 RTL 行为。以
固定 `iverilog/vvp` toolchain 运行 `mir35a`（14 cases）和 `mir35b`（1 case）两个
source/lineage-disjoint cohort；两批均为 `NO_MEMORY=FAIL`、routed
`CAUSAL_NO_SKILL=PASS`，无 UNKNOWN，且不写 canonical memory 或 authority。

将这两批与既有 selected-memory、post-revision interference 和 diverse cohort 合并后，
外部 MIR aggregate 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-policy-mir-p17-v4-correct-20260903/`：
`cohort_count=5`、`known=35`、`harmful=0`、`unknown=0`、Wilson `upper_ci=0.098901`。
聚合 freeze/replay 均通过；旧的 pre-revision harmful counterfactual 没有被混入。

基于 35-case MIR 和 43-case cross-backend calibration 的 readiness 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-p17-v4-20260903/`。
校准、multi-lineage、anti-forgetting、authority replay、rollback 和 Pareto gates 均为
`PASS`；MIR 仍未满足注册的严格 `max_mir_upper_ci=0.0`，production gate 的
candidate-pool/efficacy/authority 也仍未建立，故 `eligible=false`、
`production_integration=not_attempted`。该扩展只增加统计 evidence，不放宽策略阈值，
`memory/docs/` 继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 typed candidate-pool evidence seam

新增 `tehm.evaluation.candidate_pool_evidence` 与
`scripts/build_r3_candidate_pool_evidence.py`，为 P15-B/P9 的 candidate-pool gate
提供独立的 evaluation-only 装配入口。输入必须显式列出每个 case 的 query、候选
payload 和 `CandidatePoolReceipt`；replay 会重新读取并校验 RTL P12 cohort 的文件与
receipt digest、route/case/arm、执行 candidate ID、candidate source、候选 action-family
与 mechanism hypothesis，并从 typed paired outcomes 重算 paired denominator、MIR point
和 diversity。receipt 中手写的 metrics、NaN、gold-answer 字段、重复 case 或跨 cohort
绑定均 fail-closed；候选池 evidence 不写 canonical memory、不改变 authority，也不导入
production runtime。

`audit_r3_production_readiness.py` 现在可通过 `--candidate-pool-evidence` 显式消费这份
receipt；只有其重放成功且 paired/MIR 数值与 readiness 的 routed-policy 分母一致时，才
会投影到下游 P9 gate。当前 35-case readiness 尚未提供完整 P6 pool receipts，因此本阶段
没有伪造 candidate diversity，production gate 仍保持 `candidate_pool=NOT_ESTABLISHED`、
`efficacy=NOT_ESTABLISHED`、`eligible=false`、`production_integration=not_attempted`。
定向 replay/tamper 回归位于 `memory/tests/test_candidate_pool_evidence.py`；
`memory/docs/` 继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 authority replay projection into P9

修正 `tehm.evaluation.production_readiness` 的证据装配边界：当独立的
`tehm-rule-authority-replay-v1` 报告已经通过六项 rule gate、只读数据库前后摘要、
`ALLOW_AUTHORITY_REVIEW`、`promotion_attempted=false` 以及 content-bound receipt 校验
后，才把该 receipt 的 `verified/receipt_id/receipt_digest` 投影到下游
`evaluate_production_gate()`。此前 readiness 顶层 `authority_replay=PASS`，但 P9
输入没有携带这份已重放 receipt，导致 authority 被错误显示为
`NOT_ESTABLISHED`；本修正不接受裸 `verified=true`，也不改变 authority ledger、
canonical memory 或 production runtime。

以相同的 43-case calibration、35-case routed-policy MIR、anti-forgetting、held-out
Delta-M、authority replay 和 P16 schema inputs 重新 freeze/replay，外部结果位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-p17-v4-authority-projection-20260903/`，
readiness receipt digest 为
`sha256:d95590e2e74ff2cc1fcbdfcfa20979ffdd05773950ed0dbb409154792b18d614`。现在 P9
`authority=PASS`、`rollback/evidence/no_skill_calibration=PASS`，但
`candidate_pool` 与 `efficacy` 仍为 `NOT_ESTABLISHED`，顶层 `mir_upper_ci=FAIL`，
所以整体仍是 `eligible=false` 且 `production_integration=not_attempted`。该阶段只
修复 evidence projection，并没有把 readiness 变成 production authority；
`memory/docs/` 继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 selected-memory candidate-pool assembly

使用显式 descriptor 为同一份 4-case routed-policy cohort 装配了一次真实的 typed
candidate-pool receipt，外部结果位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-candidate-pool-selected-memory-20260903/`。
descriptor 明确列出每个 query、两个 cold-start 候选和一个 routed `tehm_rule` 候选；
builder/replay 重新校验 cohort digest、route/case/arm、candidate source/ID、执行结果和
paired denominator。receipt digest 为
`sha256:e3ea072c77ff642c1e58aea867aeefa8236a43d6c1054346f3d1eefa5815b0d4`，
`paired_cases=4`、`memory_interference_cases=0`、`candidate_diversity=0.666667`。

将该 receipt 投影到同一 4-case MIR readiness 后，P9 的 `authority=PASS`，candidate
pool 证据不再是 `NOT_ESTABLISHED`，但因注册的严格 MIR upper-CI 阈值为 `0.0`，4 个
零 harmful case 的 Wilson upper bound `0.489891` 使 `candidate_pool=FAIL`；`efficacy`
仍为 `NOT_ESTABLISHED`，整体 `eligible=false`、`production_integration=not_attempted`。
这说明 candidate-pool seam 已可复现，但样本量不足以证明 production safety，不能通过
调高阈值或重标 outcome 绕过 gate；该 campaign 仍为 evaluation-only，不写 canonical
memory、authority 或 runtime。`memory/docs/` 继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 multi-cohort candidate-pool aggregation

为使 P6 candidate composition 与 35-case routed MIR 使用同一分母，新增
`tehm.evaluation.candidate_pool_aggregate` 和
`scripts/aggregate_r3_candidate_pool_evidence.py`。每个独立 cohort 先由 typed
descriptor 生成并 replay `candidate_pool-v0.1` receipt，再由 aggregate 逐文件校验
candidate receipt、cohort digest、campaign/case/lineage/source-disjoint 绑定和固定
toolchain/oracle/platform/PDK/budget，最后从所有 typed pool rows 重新计算 metrics；聚合
标量不是输入 authority。

35-case、5-cohort aggregate 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-candidate-pool-p17-v4-20260903/`，receipt digest
为 `sha256:74ff80cd12ec56796c22314f84dce4be7a9479f52a45e55b84515a8ca350f661`，
`paired_cases=35`、`candidate_diversity=1.0`、`memory_interference_cases=0`，aggregate
freeze/replay 均通过。接入完整 MIR/readiness 后，P9 `candidate_pool=FAIL` 而不再是
`NOT_ESTABLISHED`，唯一的 candidate safety 原因是 0/35 harmful 的 Wilson upper-CI
`0.098901 > 0.0`；`authority=PASS`，`efficacy=NOT_ESTABLISHED`，整体仍
`eligible=false`、`production_integration=not_attempted`。这补齐了 candidate-pool
composition evidence，但没有放宽 MIR policy、写 canonical memory 或打开 runtime；
`memory/docs/` 继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 typed efficacy evidence from interference revision

新增 `tehm.evaluation.efficacy_evidence` 与
`scripts/build_r3_efficacy_evidence.py`，把 RQ3 的 harmful-activation decrease 固定为
一个 typed pre/post comparison。builder/replay 要求 before/after 是不同 revision 的
完整 `RtlPairedCohortReceipt`，逐 case 绑定相同 source digest、lineage、toolchain、
oracle、platform、PDK 和 budget，并从 `NO_MEMORY` baseline 与指定 policy arm 的真实
oracle outcome 重算 harmful cases；不接受手写 `gain` 或 rate 布尔值。

真实 interference revision 证据位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-efficacy-interference-revision-20260903/`，
receipt digest 为 `sha256:dff5c5297fe3bfe716e1df15e9b6f2a05728da44500dc7725d93b187c3a7c1b8`，
2 个 paired cases 的 before harmful rate 为 `1.0`、after 为 `0.0`，独立 replay 通过。
接入 35-case MIR readiness 后，P9 `efficacy=PASS`，但 candidate-pool 仍因
Wilson upper-CI `0.098901 > 0.0` 为 `FAIL`，因此 readiness digest
`sha256:052a801700daefbdf0bb9d0b69ef6e40e44362f3fa489c20fe15a6f83289b4bb` 的整体仍为
`eligible=false`、`production_integration=not_attempted`。该 2-case efficacy 结果是
evaluation-only 的 safety evidence，不等于 production promotion；`memory/docs/` 继续
由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 MIR statistical plan refreshed at the 35-case freeze

在 35-case、5-cohort `r3-policy-mir-v2` aggregate 冻结后，重新运行
`scripts/plan_r3_policy_mir_samples.py` 生成并 replay 了最新的 evaluation-only 规划：
`/data1/zhangdy/tehm-campaigns/tehm-r3-policy-mir-sample-plan-p17-v4-20260903/plan.json`，
receipt digest 为
`sha256:fb88b7a32a77cb9546ee44926eef3c99097225d91904907f808dee90437f7209`。
当前 `known=35`、`harmful=0`、Wilson 95% upper-CI=`0.098901`；在明确的
“后续不新增 harmful case”假设下，`0.10` 已达到（还需 0），`0.05/0.02/0.01`
分别还需 `38/154/346` 个 source/lineage-disjoint known cases，目标总数为
`73/189/381`。注册的 `0.0` 仍记录为
`finite_wilson_upper_bound_is_positive`，不存在有限样本解；该计划不会改写阈值、
canonical memory、authority 或 runtime。因而当前 readiness 仍保持
`eligible=false`，下一步只能在治理明确后继续独立样本积累或审查阈值政策，不能用
规划 receipt 代替 production evidence；`memory/docs/` 继续由 `.gitignore` 排除且不提交。

同时，`audit_r3_production_readiness.py` 新增可选的 `--mir-sample-plan`。readiness 在
不新增 gate 的前提下重放该规划，并强制校验它绑定同一 MIR aggregate 的
`known/harmful/upper_ci` 以及当前配置阈值；缺失或漂移会 fail-closed，规划只作为治理
元数据，不会把 `eligible` 改成 `true`。用该绑定重新生成的 readiness 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-p17-v4-mir-plan-20260903/readiness.json`，
receipt digest 为
`sha256:4158f8f8434768ce312a639fa7da4aa0a44ca17bb5aab22f4d7c52b7e1da8901`；重放通过，
`mir_sample_plan.threshold_status=finite_wilson_upper_bound_is_positive`，顶层仍为
`eligible=false`，P9 `candidate_pool=false` 的唯一安全原因仍是
`0.098901 > 0.0`。这一步闭合了“统计规划必须和实际 readiness 分母一致”的审计边界，
但没有放宽 strict MIR policy、写 canonical memory 或打开 production runtime。

### 2026-09-03 Revision3 73-case MIR/P6 challenge expansion

在不改写 `max_mir_upper_ci=0.0` 的前提下，使用 `--cohort-tag` 再执行了三个
source/lineage-disjoint 的 RTL challenge cohort（`mir35c`/`mir35d` 各 14 cases，
`mir35e` 10 cases）。每个 case 都真实执行四个 P12 arms，三批均为
`NO_MEMORY=FAIL`、`CAUSAL_NO_SKILL=PASS`、`UNKNOWN=0`；机械 disjoint 证据仍只支持
`source_lineage_disjoint_only`，不把同一组仓库 fixture 的 tagged 变体宣称为 IID 或真实
用户分布。

diverse producer 现在同步输出显式的
`r3-candidate-pool-descriptor-v1`，因此新增 cohort 的 P6 composition 也能由 typed
execution candidate ID 重放，而不是手工填充 pool。新增 38 条 P6 receipt 与原有 35 条
合并后，MIR aggregate 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-policy-mir-p17-v5-73case-20260903/`，receipt
digest 为 `sha256:bda9b752fd9a8eefb1066da7242d154442b6d8854eb03acea51ddeb0fb47f58f`，
`known=73`、`harmful=0`、`unknown=0`、Wilson upper-CI=`0.049992`；P6 aggregate 位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-candidate-pool-p17-v5-73case-20260903/`，
receipt digest 为 `sha256:13d7579fd7f5b3dab07140d4d47f606c0793aa8b6d86ae8f29617ffb2c6e7c0f`，
`paired_cases=73`、`candidate_diversity=1.0`、`memory_interference_cases=0`；两份
aggregate 均独立 replay 通过。

将 73-case MIR、73-case P6、43-case cross-backend calibration、typed efficacy、P14
held-out、authority 和 schema 一并接入 readiness，结果位于
`/data1/zhangdy/tehm-campaigns/tehm-r3-production-readiness-p17-v5-73case-20260903/readiness.json`，
receipt digest 为 `sha256:6bd248ecbbd64f5892e2c1fbb2bc12e2eb816c8b1a0f5f827a6c440f4f07baa4`；
所有顶层结构/回滚/校准 gate 仍为 `PASS`，但 strict MIR upper-CI 和 P9 candidate-pool
安全检查仍为 `FAIL`（`0.049992 > 0.0`），`eligible=false`、
`production_integration=not_attempted`。这证明了统计分母和 P6 composition 已同步增长，
没有把更多样本当成 production 授权；`memory/docs/` 继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 P6/MIR exact cohort binding

补强 P15 readiness 的证据边界：当 candidate-pool 使用多 cohort aggregate、而 MIR 使用
`r3-policy-mir-v2` aggregate 时，readiness 现在除了重算并比较 paired/harm/rate 标量，
还必须比较每个 `RtlPairedCohortReceipt` 的 content-bound digest、campaign ID 和
case count 集合，并要求 policy arm 一致。这样即使两份报告偶然拥有相同分母，也不能把
不同 cohort 的 P6 composition 伪装成同一份 MIR evidence；旧的单 cohort/v1 输入保持兼容。
现有 73-case readiness replay 通过，digest 未改变（`sha256:6bd248ecbbd64f5892e2c1fbb2bc12e2eb816c8b1a0f5f827a6c440f4f07baa4`），
仍为 `eligible=false`、strict MIR `FAIL`、`production_integration=not_attempted`。
该校验只加强 evaluation evidence provenance，不写 canonical memory、authority 或
runtime；`memory/docs/` 继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 strict-MIR threshold governance review

对同一份 73-case MIR/P6、43-case cross-backend calibration、P14、authority、schema 和
anti-forgetting 输入做了只读阈值敏感性回放。预注册的 strict `max_mir_upper_ci=0.0`
下，所有七项 readiness gate 中只有 `mir_upper_ci=FAIL`，P9 只有 `candidate_pool=FAIL`；
原因是 0/73 harmful 的 Wilson 95% upper-CI=`0.049992`，有限样本不可能得到严格零上界。

仅作为治理情景（没有写回 receipt、没有修改默认值、没有打开 runtime），将阈值设为
`0.05` 或 `0.10` 时，当前回放的所有 readiness/P9 checks 会变成 `PASS`。这不是授权：
当前生产政策仍固定为 `0.0`，而且这些 RTL tagged cohorts 只证明 source/lineage
disjoint，不证明 IID 或真实用户分布。任何未来非零阈值都必须由明确的外部治理决定，并
重新冻结带该阈值的 evidence；不能把这次敏感性结果当成 production promotion。当前仍为
`eligible=false`、`production_integration=not_attempted`，`memory/docs/` 继续被忽略且
不提交。

### 2026-09-03 Revision3 external challenge fail-closed capture

对外部 ORFS `aes`/`gcd` 描述符的独立挑战先进行了真实执行预检。修正官方
`sky130hs` 基线参数后，`gcd` 的 `NO_MEMORY` 为 PASS、99% config candidate 为 FAIL，
但 `aes` 的 `NO_MEMORY` 仍为 FAIL（无法形成 paired interference）；因此该 campaign
不能作为 MIR 或 production evidence，也没有接入任何 aggregate/readiness。诊断 artifact
位于 `/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-independent-challenge-20260903-retry3/`，
其 `cohort.json` digest 为
`sha256:3a03146952e2c4b3b2da7a67567df9fe8df43f07a6698aaa000f4434b505256f`，
`failure.json.status=REASON_DERIVATION_FAILED` 明确记录了 AES 缺失
`MEMORY_INTERFERENCE` 的原因。

同时修正 `run_r3_orfs_interference_challenge.py` 的 evidence lifecycle：输入 manifest/cases
在执行前落盘，完整 paired cohort 在 reason derivation 前落盘；若执行或 reason 推导失败，
写入带状态和 cohort digest 的 `failure.json`，并保持非零退出。该改动只改善 evaluation
审计，不创建 P13 reason、不写 canonical memory、authority 或 runtime；`memory/docs/`
继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 producer boundary consistency

补齐 counterexample、novelty/conflict reason-adapter 与 ORFS P14 attribution producer
的报告边界字段：所有新生成的 evaluation artifact 都显式写入
`memory_docs_submitted=false`，并保持 `canonical_memory_mutation=none`、
`production_runtime_imported=false`（或等价的 `production_runtime` 全 false）。这只
统一可审计 schema，不把历史缺字段报告回填成新 evidence；`memory/docs/` 仍由
`.gitignore` 排除且不会进入 release。

### 2026-09-03 Revision3 non-zero MIR threshold governance receipt

`tehm.evaluation.mir_threshold_governance` 新增 evaluation-only 的外部治理 receipt。
production readiness 仍默认使用 strict `max_mir_upper_ci=0.0`；如果未来显式设置非零阈值，
必须通过 `--mir-threshold-governance` 提供 content-bound receipt，且 receipt 的
`evidence_sha256` 必须精确绑定当前 interference summary。缺失、阈值不一致、证据漂移或
跨越 canonical/production/docs 边界都会 fail closed。该 receipt 只是可重放的外部决策
记录，不是 promotion token，不写 canonical memory，也不打开 production runtime。

### 2026-09-03 Revision3 R3-9 non-P12 reason replay

在当前 HEAD 重新执行两个 source-disjoint RTL challenge：
`tehm-r3-capability-gap-20260903-r3` 与 `tehm-r3-repeated-failure-20260903-r1`。
两批均由真实 Icarus/VVP capture 产生，分别得到 `CAPABILITY_GAP` 与
`REPEATED_FAILURE` 的 typed derivation 和 reason-specific admission（均 `admitted=true`）。
前者只生成 `ADD/ASSET_OR_KNOWLEDGE` shadow proposal，后者只保留 repeated-failure
evidence；两个 source canonical SQLite digest 都保持
`d808d3e66b452577a7402bdc7c4fffd03b3f54123e165e5a1f0e63c88a7f8997`，且
`canonical_memory_mutation=none`、`production_promotion_eligible=false`。
这些结果补强非 P12 reason-specific admission 的真实可重放证据，但不注册新
Knowledge/Asset、不进入 production runtime；`memory/docs/` 继续被 `.gitignore`
排除且不提交。

### 2026-09-03 Revision3 non-P12 frozen replay boundary

修正 R3-9 capability-gap 与 repeated-failure runner 的证据冻结边界：source
canonical snapshot 和 disposable shadow snapshot 现在在报告落盘前执行
`wal_checkpoint(TRUNCATE)`，并用字节复制创建空的 shadow 起点，避免只读 backup
再次产生 `-wal/-shm` sidecar。新增
`scripts/replay_r3_non_p12_challenge.py`，只读重放数据库 digest/count、typed
detector receipt、reason derivation、reason-specific admission 以及 capability-gap
proposal，并 fail closed 检查 canonical/production/docs 边界。

新 artifact
`/data1/zhangdy/tehm-campaigns/tehm-r3-capability-gap-20260903-r6/` 与
`/data1/zhangdy/tehm-campaigns/tehm-r3-repeated-failure-20260903-r4/` 均为无
sidecar 的冻结快照，报告显式写入 `memory_docs_submitted=false`，CLI replay 已通过；
此前带 sidecar 或缺少显式 docs boundary 的 r3/r4/r5（capability-gap）和 r1/r2/r3
（repeated-failure）仅保留为诊断，不能作为冻结 evidence。该 lane 仍是
evaluation-only：不写 canonical memory、不进入 production runtime；`memory/docs/`
继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 direct-toolchain preflight boundary

对当前服务器路径做了只读预检：`/usr/bin/openroad` 可以识别，但配套的
`/usr/bin/yosys` 为 0.9，缺少该 ORFS flow 要求的
`read_liberty -unit_delay`，因此 `preflight_orfs_toolchain()` 正确返回
`blocked`，不能把系统二进制误记为可复现 toolchain。`/data1/zhangdy/Tools/tehm-toolchain`
中的 OpenROAD `26Q2-1846-g49bd051a10` 与 Yosys `0.65` 满足 capability 检查；在
干净的 `/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean` 上生成并 replay 的
manifest digest 为
`9b5f179b01bebde6da87f6443729f2589d8fab218fc63478628c2286e1940b1c`，状态为
`bound_internal`。用户目录下的 `/data1/zhangdy/Tools/OpenROAD-flow-scripts` 当前
存在大量修改/未跟踪文件，故不能作为冻结 evidence；不对其执行清理或覆盖。该结论
只约束 ORFS evidence 的显式环境绑定，不写 canonical memory、authority 或 runtime，
`memory/docs/` 仍由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 reason-adapter/P14 snapshot hardening

novelty/conflict reason-adapter runner 现在与 R3-9 一样，在 source 初始化后先做
`wal_checkpoint(TRUNCATE)`，通过字节复制创建 disposable shadow，并在成功路径
checkpoint derived projection；ORFS P14 attribution 则改用 immutable read-only source，
并 checkpoint 自己的 attribution projection。新
`tehm-r3-reason-adapters-20260903-r3` 与
`tehm-r3-orfs-interference-p14-20260903-r2` 均无 SQLite sidecar，报告显式写入
`memory_docs_submitted=false`，且 novelty/conflict、P14 strategy attribution replay
结果保持 evaluation-only、canonical mutation 为 `none`、production runtime 未导入。
此前带 sidecar 的 reason-adapter artifact 仅作诊断，不作为冻结 evidence；
`memory/docs/` 继续由 `.gitignore` 排除且不提交。

### 2026-09-03 Revision3 full regression and signature-fixture audit

使用工具链自带的 Python 3.11/pytest，并显式加入 `memory/tests` 同目录导入路径，
完整执行 `memory/tests`，结果为 `1063 passed`。期间修正了四个测试夹具：修改
content-addressed cohort 身份或路由后先移除旧 digest 再重签名，并让 efficacy 夹具
使用 cohort-level 的 platform/PDK 字段。生产 loader 继续对旧签名 fail-closed，未
放宽 replay 或 authority 边界；当前 `git ls-files memory/docs` 与 HEAD tree 仍均为
零，`memory/docs/` 不会进入提交或 release。

### 2026-09-04 Revision3 real ORFS interference shadow/P14

使用绑定的内部 toolchain（clean ORFS、OpenROAD/Yosys/PDK manifest digest
`sha256:9b5f179b01bebde6da87f6443729f2589d8fab218fc63478628c2286e1940b1c`）执行
两个 source-disjoint、但行为刻意相同的 GCD 变体；真实 `run_orfs.sh`、KLayout DRC
和 Netgen LVS 均完成，challenge cohort digest 为
`sha256:50a235a583c4282e1e7b5a3451ad4d0d652cac100dbf3ed4402310ea495f871f`。
两 case 均导出 typed `MEMORY_INTERFERENCE`，NO_MEMORY 为 2/2 PASS，强制
ALWAYS_MEMORY、APPLICABILITY_GATED、CAUSAL_NO_SKILL 均为 2/2 FAIL；这不是 IID
统计样本，且 timing 仍为 moderate，不能当作 strict production signoff。

P13 shadow 目录
`/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-gcd-shadow-20260904-r2/`
给出 `memory_delta_eligible=true`、anti-forgetting=true，post 三个安全 arm
均 2/2 PASS；canonical DB digest 前后一致、staging discarded、production
authority/runtime 均未改变。P14 attribution 目录
`/data1/zhangdy/tehm-campaigns/tehm-r3-orfs-interference-gcd-p14-20260904-r1/`
确认 strategy gates C1--C5（含 fallback execution）成立，但 capability C5/C6/C8
仍缺失，因此只证明 L2 safety strategy evolution，不声明 L3 capability gain，
不授予 promotion token。以上三个 artifact 均标记 `memory_docs_submitted=false`；
`memory/docs/` 继续由 `.gitignore` 排除且不提交。
