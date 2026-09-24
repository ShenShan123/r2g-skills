# Revision4 Pilot P1–P4：冻结开发样本的诊断结果

本报告按 `docs/TEHM_R2G_Revision4_接口收口与真实设计Pilot方案_2026-09-17.md` §12–14 记录。范围是 Research Runtime RC1 的 **Pilot development**，不是最终 held-out、LLM Agent、strict signoff 或 production 结果。以下 S0 原始 flow、S1 构造挑战、S2 三策略对照具有不同任务合约，不能跨表合并成功率。原始产物位于仓库同级的 `../tehm-campaigns/r4-real-pilot/`；报告中的 digest 均可与该目录的冻结产物核对。

## P1：设计筛查与接入

只读 RTL inventory 的 [完整 552 行设计账本](../../../tehm-campaigns/r4-real-pilot/inventory/rtl-readonly-20260918-rc2/inventory.json) 和 [候选 CSV](../../../tehm-campaigns/r4-real-pilot/inventory/rtl-readonly-20260918-rc2/candidates.csv) 是 P1 全筛查分母，不仅列成功案例。552 个候选中 537 个为 `NEEDS_ADAPTER`、15 个为 `UNSUPPORTED_CURRENT_PROFILE`；[排除账本](../../../tehm-campaigns/r4-real-pilot/inventory/rtl-readonly-20260918-rc2/exclusions.json) 逐项记录 15 个当前 profile 不支持项。shortlist 规则筛出 210 个 eligible，按声明来源各取一个形成 [8 项 shortlist](../../../tehm-campaigns/r4-real-pilot/inventory/rtl-readonly-20260918-rc2/preflight-shortlist.json)，digest `sha256:afe3b1e2c7830af6ef242ba454bac87e60e9e269a3798b2d8ebfde2040ad3544`。inventory 的 `NEEDS_ADAPTER` 与 shortlist 的 eligible 都不是 frontend、flow 或 oracle 已就绪认证。只读 corpus 前后 digest 一致。另有两个 ORFS 官方设计进入 S0；它们不属于 RTL shortlist 分母。

| Design / lineage | 来源及当前接入状态 | Profile / frontend-synth / oracle readiness | 纳入或未纳入原因 |
|---|---|---|---|
| `gcd` | ORFS 官方；S0 已执行 | sky130hs；固定 flow 可审计，route 终态 FAIL | 自然 `routing_congestion`；保留为 S0 无候选样本 |
| `uart` | ORFS 官方；S0 已执行 | sky130hs；固定 flow PASS | 无目标失败，不触发记忆路由 |
| `verilog_axis_ll_axis_bridge` | RTL shortlist，声明 `verilog-axis`；S0 已执行 | sky130hs；固定 flow 在 floorplan FAIL | 自然 `pdn_geometry_infeasible`；保留为 S0 无候选样本 |
| `usbcorev_endpoint` | RTL shortlist，声明 `usbcorev`；S0、S1、S2 已执行 | sky130hs；原始 S0 PASS；构造 S1 challenge 有独立 oracle | S1/S2 为 development 复用样本，不是 final held-out |
| `verilog_axi_axil_reg_if_rd` | RTL shortlist，声明 `verilog-axi`；S0、S1、S2 已执行 | 同上 | 同上 |
| `verilog_wishbone_wb_reg` | RTL shortlist，声明 `verilog-wishbone`；S0 已执行 | sky130hs；固定 flow PASS | 无目标失败 |
| `spi_master_core` | RTL shortlist，声明 `spi-master`；S0 已执行 | sky130hs；route timeout，oracle UNKNOWN | 基础设施/时间限制不能改写为硬件 FAIL |
| `vlsi_axi_apb_bridge_top` | RTL shortlist，声明 `VLSI-Project-AXI-to-APB-Bridge`；S0 已执行 | sky130hs；固定 flow PASS | 无目标失败 |
| `fpu_floating_multiplication` | RTL shortlist，声明 `FPU-IEEE-754`；未执行 | `PROPOSED_NOT_RUN`，来源元数据未核验；尚需 bounded frontend/synth preflight | 不列入 S0/S1/S2 终态，也不预称 final held-out |
| `simple_i2c_slave` | RTL shortlist，声明 `Simple_I2C`；未执行 | 同上 | 同上 |

六个外部 S0 设计的上游声明不能代替独立来源认证；S1 两组已核验为 `github-owner:alexforencich` 与 `github-owner:avakar`，均不同于 M0 seed 的 ultraembedded、freecores。S0 [完整八案 flow/route 账本](../../../tehm-campaigns/r4-real-pilot/campaigns/r4-s1-memory-route-coverage-post-m0-20260923/audit/fd51674-r1/coverage.json) digest 为 `sha256:624ae1a11274905e908da6cc2f60fb4118ddb5c7a628e0f27e362a4b412e3f3c`：5 PASS、2 自然 FAIL、1 UNKNOWN；route selected 0/8、实际 memory action 0/8。

## P2：逐案真实使用链

S0 的两条自然失败分别在 route 返回 `NO_SKILL / NO_MATCH`，不存在后续 selector、binder 或 memory candidate；五条 PASS 无目标 query，timeout 项不路由。下表只列产生目标 route 或执行候选的记录，其余终态与原始 flow audit 在上述完整账本中。

| Case / policy | 初始目标状态 | Route / asset 与实际动作 | 终态及原始证据 |
|---|---|---|---|
| S0 `gcd` | route `FAIL / routing_congestion` | `NO_SKILL / NO_MATCH`；无选择、无动作 | 自然 FAIL；flow audit `sha256:f3db14f9fa0b0fb4006644945ca50a2935c040ef190611c3adab9f4929d32d90` |
| S0 `verilog_axis_ll_axis_bridge` | floorplan `FAIL / pdn_geometry_infeasible` | `NO_SKILL / NO_MATCH`；无选择、无动作 | 自然 FAIL；flow audit `sha256:59030690b63cd83f62ff878dbc2f3733b59fcc88aa96b83c836a6b88105e9365` |
| S1 AXI control → TEHM-selected treatment | 构造 u95，place `FAIL / placement_density_infeasible` | 冻结 M0 `CONSIDER → SELECT → bind`，`CORE_UTILIZATION 95→40`；实际执行一次 | `PASS / flow_completed`；control `sha256:7938a5d3a567de87c918f86fecb6fc3c17c10548d3f958981a5b1c9a68d0f96c`，treatment `sha256:70546ee97ab52c5b6e7fe6ed8507e113c9feb9bd1aa39c85d81d97dc20049126` |
| S1 USB control → TEHM-selected treatment | 同样的构造 u95 place FAIL | 同一已验证机制，独立来源、实际执行一次 | `PASS / flow_completed`；control `sha256:084d27098b05ab6aaac470bd74be26fe8c4ad3d5a7f943e732c5852ff287f79f`，treatment `sha256:ce4fdc43afcd82ae5f7ad96c51d97af00b3806eb231e8fec179269303bce143d` |

S2 [冻结 proposal](../../../tehm-campaigns/r4-real-pilot/campaigns/r4-s2-deterministic-development-20260924/proposals/v1/proposals.json) `sha256:206fcaf44925ff1eee4866e0ddb87f56fc551755f8a0019d089b8d6972384b7a` → [隔离 stage plan](../../../tehm-campaigns/r4-real-pilot/campaigns/r4-s2-deterministic-development-20260924/stage-plan/v1/stage-plan.json) `sha256:22d556527b75e62e53c99c120674ae4615271cd6d1cabacc89d60aa4357e257b` → [逐尝试事件链](../../../tehm-campaigns/r4-real-pilot/campaigns/r4-s2-deterministic-development-20260924/s2-runs/v1-once/attempt-events.jsonl) tail `sha256:3d0be1c2786fffeac9055af597acb2f026d43717430d9fd391d80d8edf8f5c22` → [完整 summary](../../../tehm-campaigns/r4-real-pilot/campaigns/r4-s2-deterministic-development-20260924/s2-runs/v1-once/run-summary.json)。三组均为相同 R2G `deterministic_skill` controller、每任务 B3/至多 6 EDA stage/0 model、无在线学习。每组均先冻结真实候选，再执行首个候选且 PASS 停止；未执行的 fallback 不算 memory-use。S1 control 只是三组共享反馈，不是 No Memory Agent 结果。

| S2 case / policy | 首个候选来源与实际配置动作 | 是否实际使用记忆 | Oracle 终态；独立 raw flow audit digest |
|---|---|---|---|
| AXI / No Persistent Memory | cold start：`ABC_AREA=1, UTIL=25, ADDON=0.2` | 否 | PASS；`sha256:debae8699b9396e53087757b61d43cca61d8d7220f942865512e1e19c82ad3eb` |
| AXI / Legacy | 历史建议与 cold 相同，被去重；实际 cold start | 否 | PASS；`sha256:7c1e297aa3dd16732e81425196e550186bf4acdc75dcf88427997ee567cc9c64` |
| AXI / TEHM | M0 scoped 规则：`UTIL=40`（保留项目其他配置） | 是 | PASS；`sha256:8819aec582d5aafbec6af2a9d163aa57efd0e6ff8bab32724aa5949224271a32` |
| USB / No Persistent Memory | 同一 cold-start 配置 | 否 | PASS；`sha256:cee70e0bdec880da376f87793df314027c70295d20f051d3f2c0b9c282f4f5dc` |
| USB / Legacy | 真实历史 `usb/sky130hs` 建议：`ABC_AREA=1, UTIL=18, ADDON=0.2` | 是 | PASS；`sha256:aad9c2d853bb280e179259086a9a745e3ac925dd1ea1001b3a6059cfe075004f` |
| USB / TEHM | M0 scoped 规则：`UTIL=40` | 是 | PASS；`sha256:205a2f6a5b23e0796d10a16504b807ab2f9e39adc9f7975aae3b21b28b0acb2b` |

Legacy 数据库使用隔离副本，原始 DB SHA256 `e5b46fe49c289b609c39df077be4cfcf444366767f54edc75686cc50f1d449f0`；两设计在 Legacy 历史库里各已有 5 次 exact-top run，因此该对照**不是 Legacy 未见设计泛化**。TEHM 只读 M0 SQLite SHA256 `a9d4e83a402432e2e62e39d4dd1a51d4657bf46496ace79656353176f96d5dfd`。六个 raw flow audit 与事件链经过独立 verifier 第二次复核；source RTL、SDC、平台和工具链在候选间保持一致。

## P3：S1 与 S2 的小样本效果、成本

S1 是 **构造任务 controlled-action**，不是 Agent 三策略：两条 control 都在 place 确定 FAIL，两条候选 treatment 都全 flow PASS，构造 flow-feasibility 收益 `2/2`、UNKNOWN `0/2`；每条 treatment 一次 driver、六次 EDA stage、一次独立 audit，stage wallclock 分别 54 秒和 61 秒。其 control 不能填入下表的 No Persistent Memory 行。

S2 任务成功定义为预算内全 flow `PASS / flow_completed`；两个独立开发来源组各有一条任务。`Benefit/Harm/Neutral/Unknown` 在此仅按 **任务成功** 相对 No Persistent Memory 成对比较，未预注册 QoR utility 阈值，故不能由两个 PASS 推断 QoR 中性或无害。

| S2 policy | TaskSuccess@B / 全部任务 | 可判定成功配对；Unknown | Benefit / Harm / Neutral / Unknown（成功） | Memory-use coverage | 实际 flow driver / EDA stage / model | EDA stage wallclock；策略终态累计耗时 |
|---|---:|---:|---|---:|---|---|
| No Persistent Memory | 2/2 | 参考臂；0 | 不适用 | 0/2 | 2 / 12 / 0 | 102 秒；约 112.75 秒 |
| Legacy | 2/2 | 2/2；0 | 0 / 0 / 2 / 0 | 1/2 | 2 / 12 / 0 | 102 秒；约 107.36 秒 |
| TEHM M0 | 2/2 | 2/2；0 | 0 / 0 / 2 / 0 | 2/2 | 2 / 12 / 0 | 115 秒；约 122.00 秒 |

策略累计耗时是独立验证后从每条 `POLICY_TERMINAL.elapsed_seconds` 相加的开发运行观测，并非并发吞吐、纯 query 开销或统计均值；stage wallclock 来自审计汇总，不包括离线 M0 构建、proposal/staging 与独立审计的完整总成本。六次候选全部首试 PASS，无 retry/fallback；额外审计调用 6 次。离线 M0 建库/验证单列在 seed 证据中，尚无与 Legacy 建库的统一成本归一化；在线 query/routing/binding 的独立耗时也未测量。S2 成功率差为 0/2，不支持 TEHM 的成功率增益；样本太小，且 QoR utility/容差未冻结，不能宣称 QoR benefit/harm 或风险为零。零个观测到的成功型干扰只可写作 `0/2`。

## P4：失败层分类与收尾顺序

| 层 | 完整分母中的观测 | 原始依据与应修复事项 | 对当前结论的影响 |
|---|---|---|---|
| Onboarding / dependency | RTL inventory 552；其中 15 当前 profile 不支持，2 shortlist 项未跑 | `inventory.json`、`exclusions.json` 与 shortlist；先完成 filelist/top/dependency、前端与来源核验 | 影响后续准入；不可把 inventory eligible 写成 oracle ready |
| Query / state | S0 自然失败两条 query 均形成；没有已证明的 state-shift | 完整 route coverage；新的异常需冻结 state receipt 再判定 | 暂不能归因 state shift |
| Retrieval / support | S0 自然失败 2/2 `NO_SKILL / NO_MATCH` | `gcd` 的 route congestion、axis 的 PDN geometry 是两个不同机制；需要各自多来源训练证据及独立验证，不手改知识状态 | 证明当前 M0 的自然机制覆盖缺口，不证明记忆动作无效 |
| Binding / action | S1 构造 2/2 bind 并实际执行；S2 TEHM 2/2 实际执行 | S1 treatment、S2 proposal/stage/event/raw audits；继续关注 plan/actual 差异 | 已支持限定 scope 的可执行链，不外推自然失败 |
| Hardware outcome | S1 构造 benefit 2/2；S2 三策略 success 均 2/2 | 独立 flow oracle；为 QoR/strict claims 冻结可比性、容差和目标后再测 | S2 成功收益 0/2；QoR 未定，不声称无害 |
| Infrastructure | S0 `spi_master_core` 1/8 route timeout、UNKNOWN | 原始 flow audit `sha256:0e10a875cbac9a5d1389162cbce58e5306e5cd25776009e1f75fd2c63c0fee37`；查资源/预算并在新 epoch 重跑 | 保留全分母，不变成硬件 counterexample |

优先次序：先闭合未跑项/timeout 的接入与终态；再建立各自然失败机制的独立训练来源；随后预注册 QoR/严格 oracle、成本和新 final held-out。当前两条不同机制的自然失败、两条构造 S1 benefit 与两条 S2 全 PASS 均不构成 R4-7 的合格 mutation signal；Shadow Evolution 的准入另行记录，不能从本表反填 reason。
