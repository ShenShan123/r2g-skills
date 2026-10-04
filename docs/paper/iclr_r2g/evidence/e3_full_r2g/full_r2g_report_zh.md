# 实验三补充：保留完整 Recipe 库的 Full R2G

## 写论文的核心结果

Full R2G 在原实验三的13个B组challenge上修复8题（61.54%）：DRC为7/7，setup为1/6。
共执行11次修复ORFS，8次成功、3次未成功；另2题没有合法匹配动作，未执行修复ORFS，
但仍计入13题分母。所有成功均在首次尝试获得。评估阶段不调用LLM、不在线学习。

这是保留完整本体Recipe库的开发后系统补充评估。库中包含利用B题证据开发的策略，
因此不把本结果解释为独立未见测试的泛化成绩，也不替换原M0/M1/M2/M3或Pure LLM成绩。

## 实验设计与执行

- 使用原13题：7个DRC、6个setup；保留原RTL源码、100 MHz（10 ns）、固定Die/Core和检查集合。
- 保留运行时完整原生知识库的隔离快照，使用原生排序、生命周期门及固定任务动作策略。
- 实际执行器为实验三的受预算约束配置动作适配器，不是无限制工程修复循环。
- 208服务器，1个worker，CPU 128-131，共4核；每题最多3次尝试，每次上限7200秒。
- 每次从baseline重新开始，成功立即停止，排除已尝试策略和重复动作效果。
- 按原run_trial严格评分；基础设施证据不完整不能当作确定性修复失败。
- 本次补充臂未另跑4个clean sentinel；不能借用原M1-M3的0/4回归率声称本臂也已验证。

设计与输入清单见[manifest](design_and_execution/manifest.json)，动作白名单见
[action_policy](design_and_execution/action_policy.json)，快照信息见
[snapshot](design_and_execution/snapshot.json)。完整原执行说明见
[执行说明](design_and_execution/README.md)。

## 逐题结果

| 设计（task ID去掉exp1前缀） | 类型 | 修复调用 | 结果 |
| --- | --- | ---: | --- |
| 15998ea30bc1_aes_192 | DRC | 1 | clean |
| 17c9a0461ada_des_top | DRC | 1 | clean |
| 18940142f78e_seqcordic | DRC | 1 | clean |
| 1993adb0da90_timer_top | DRC | 1 | clean |
| 6a2b257650ce_reg_file | DRC | 1 | clean |
| 72c9fc843efb_wbuart | DRC | 1 | clean |
| d50060d8400c_picorv32 | DRC | 1 | clean |
| 61c173a60608_iir_biquad_axis | Setup | 1 | clean |
| 729416e93fdc_blake2s_core | Setup | 1 | 仍有setup违例，并新增DRC |
| 9e3f10826919_fft8_stream | Setup | 1 | 仍有setup违例 |
| c3cb6ed47cb8_pipelined_iir | Setup | 1 | 仍有setup违例 |
| 250c441e570f_blake2s | Setup | 0 | 无合法匹配动作 |
| 95f132db328d_tanh | Setup | 0 | 无合法匹配动作 |

7题DRC使用`pin_side_rebalance`；4次setup尝试使用`hierarchical_place_timing_repair`。
后者动作是`SYNTH_HIERARCHICAL=1`与`ENABLE_PLACE_REPAIR_TIMING=1`，不改RTL。
成功的IIR Biquad：setup WNS由-2.69817 ns改善至+0.106265 ns，改善2.804435 ns；
hold WNS为+0.4358 ns，DRC/LVS/route/antenna违例均为0。

## 与原消融结果的关系

原M1、M2均为8/13，分别使用25、24次challenge ORFS；原M3为7/13、7次。
Full R2G为8/13、11次，但拥有不同的历史知识来源和额外开发投入，不能据此把
调用次数差直接归因于一种孤立机制，或声称其总开发成本更低。
调用次数不包括baseline、策略开发验证、后续源码改写及其他探索实验。
summary.json中的累计attempt秒数不是端到端墙钟时间，也不是整个项目总消耗。

## 后续探索不改变本结果

后来Pipelined IIR源码改写的clean结果不符合本轮保护源码的动作范围；均值滤波器
属于A-propose而非这13题。两者不能把成绩改成9/13或10/13。0.30 ns优化余量也不在
原动作策略仅登记0.2 ns的范围内。相关探索已停止，历史证据保留。

适用范围离线回放发现，收紧WNS门槛会漏掉原本成功的IIR，故未修改生产范围。
11次仍为实测调用数，不能把事后理想筛选的8次写作实测效率。

## 材料入口

- [机器可读摘要](results/summary.json)
- [完整13题结果及11次trial指标](results/complete.json)
- `evidence/`：13题运行前诊断、逐题结果、11次动作选择及trial证据。
- [停止扩展与规则复核](analysis/original_scope_stop_review_20260916.md)
- [适用范围回放](analysis/scope_replay/README.md)
- [材料来源与SHA256清单](material_manifest.json)

本目录是写论文使用的精选证据副本，不是自包含EDA复现环境。大体积RTL、工具链、
知识库和物理产物仍在权威原目录`/home/yangao/r2g_full_development_20260910/full_r2g_supplement_208`。
未移动、删除或修改原始证据。
