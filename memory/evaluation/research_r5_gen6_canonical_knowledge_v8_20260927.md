# R5 gen6 v8 canonical／Knowledge TRAIN 准入

## 结论与边界

`research_r5_rtl_scoped_v8_checks.py` 在一次性 RAM 数据库中，从两份封存 v8 原始 TRAIN 包重建 AXIS、ZipCPU、mux 三个已登记 DEV 任务的 control/treatment 共六条 canonical `ExecutionRecord`。这些任务是 researcher-assisted reused-DEV TRAIN，不是新样本、未见迁移或模型自主修复。ZipCPU 的 payload 判定仍是 research-augmented，不能称 native oracle 等价。mux 的 target/preservation 是封存执行 scope 标签，不是虚构的私有测试 ID。

每条 learner 访问经 `scoped_learning_replay` 重审封存 raw、来源审计、完整执行与显式 training membership；原始 source、动作输出和候选 SHA 也逐项匹配。三个 control/treatment pair 获 L2；核心 replication 在三个固定仓库来源组上给出 L3。来源历史审计只证明有界组划分，不证明统计独立、作者独立或生成器无共享。

同一 RAM 数据库生成并登记 Knowledge，严格 authority receipt 可冷核验；第二 RAM 连接在重新提供冻结 acquisition 上下文后独立核验。没有该上下文时，冷核验拒绝。Knowledge 初始是 candidate；缺失或伪造 receipt 不能直接设为 validated。只有显式消耗严格 receipt 才在一次性 RAM 中进入 validated，已消耗的旧状态版本 receipt 不再可用。这个状态不写入持久 Memory，也不是 production promotion。

31 项检查通过，包括六条记录同一 oracle instance、AXIS 实际九个 cocotb ID、exact source/action/candidate、L2/L3、严格 Knowledge、跨连接冷回放，以及篡改 witness/contract/acquisition、错配 acquisition、heldout membership、无 replay 上下文和布尔式 authority 的拒绝。`research_r5_train_asset_authority_v8_checks.py` 的 30 项严格 Asset RAM 检查仍是独立前置；二者不能互相替代。

## 软件与证据

- 适配器：`memory/tehm/adapters/research_r5_rtl_scoped_v8.py`。
- 核心 learner replay 版本接线：`memory/tehm/verified_execution.py`。
- RAM 检查／冷验证入口：`python3 -m tehm.evaluation.research_r5_rtl_scoped_v8_checks --output /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/skid-v8-knowledge-ram-r1.json`（`PYTHONPATH=memory`）。冻结输出要求干净软件树，回执含 Git HEAD、上述三份源码 SHA、原始 TRAIN digest 和包 seal。复验将 `--output` 改为 `--verify`，重跑并比较完整 JSON。

本阶段没有新 simulator 执行、模型调用、M+ 构建、heldout target 执行或 ΔMemory attribution。新 Memory generation 必须先固定干净软件 epoch，再从这批合格 TRAIN 记录构建并冷验 M−／M+／Mremove；不能复用 gen5 的采集身份、知识或资产回执。
