# Revision5 R5-4：TRAIN / Memory 准入预检

状态：**NO-GO for M+ construction**。这是进入 R5-4 前的只读接口与证据审查，不是 TRAIN receipt、Memory snapshot、目标迁移或 ΔMemory 结果。阶段条件以 [Revision5](../docs/TEHM_R2G_Revision5_RTL测试判定力_受限绑定与独立迁移Pilot方案_2026-09-24.md) §9、§14、§16–17 为准。

## 已确认的输入边界

- 第一批 10 个公开仓库在 `/data1/zhangdy/RTL/RTL_testbench/<owner>/<repo>`；QF-1-r4 的十库锁、三个 qualified scope 和旧 binder 0/249 扫描复核 `valid=true`。该索引只含 axis_register、UART、AES 三个具体 scope；新增 broadcast DEV scope 另有回执，不能把十个仓库都标作 oracle-ready。
- `axis_register` 的 skid payload 故障和修复是同一个已观察的 DEV case。R5-3 的 candidate 原生测试 9/9 PASS 仅证明该开发故障上的动作执行，不提供未见目标，也不自动转为 TRAIN。
- 第一批不同 owner/source group 的 RTL 中虽可搜到其他 skid 字样，当前 v2 受限合约也只识别 register/broadcast 两种 `alexforencich` 形态和固定公开参数；没有对其他来源文件的合格测试、可绑定性或修复成功的证据。
- 后续另建的 `axis_broadcast[2-8]` DEV 探针取得 clean 4/4 PASS、故障 3/4 PASS/1 FAIL，但它不在 QF-1-r4 原三项范围内；冻结 v1 binder 对该故障明确 NO_MATCH。新 DEV v2 binder 在该故障及 register 故障上均能唯一绑定，37/37 对抗检查通过，动作源码逐字节等于各自 clean staged RTL；v2 shadow core 接线另有 19/19 RAM-only 检查，仍无 Knowledge-authorized SELECT，也不改变本报告的 NO-GO。该同 owner 设计不是自动 TRAIN 或跨来源 target。
- 针对独立来源缺口，从用户第二档定向新增 `ZipCPU/wb2axip` 作为 DEV 资格候选；原生 `skidbuffer.sby` 的 `prfo` 对预登记的暂存 payload 来源故障给出 `MISSED`（clean/fault 均 PASS），另一个 valid-signal live-control 被断言检出。当前 v2 binder 对其公开参数也 `UNSUPPORTED`。两道独立 NO-GO 都不能由 live-control 的 FAIL 越过；详情见 [ZipCPU DEV 探针](research_r5_zipcpu_skid_dev_20260924.md)。
- 仅为选定的 ZipCPU DEV payload 范围，另建研究者编写、evaluator-private 的事务级 augmented oracle：clean 的 direct/backpressure 均 PASS，fault 的 direct PASS、backpressure 因第二拍 payload 不符 FAIL；11/11 结果适配器反例通过。它不改变上游 native `MISSED`，也不改变 v2 binder 的 `UNSUPPORTED`、TRAIN provenance 缺失或本报告的 M+ NO-GO。详情见 [augmented DEV 回执](research_r5_zipcpu_augmented_payload_dev_20260924.md)。
- 新建独立的 DEV v3 source-only binder 草案，在 register、broadcast 与 ZipCPU registered 三种已观察 DEV 形态上 48/48 对抗检查通过，三例候选逐字节等于各自 clean staged RTL；v1/v2 不改，v2 对 ZipCPU 仍 `UNSUPPORTED`。v3 尚未接入核心或取得 TRAIN/Memory authority，不能把新增软件适用域归因成 Memory transfer；详情见 [v3 绑定回执](research_r5_skid_binding_v3_dev_20260924.md)。

## 核心接线与准入缺口

| 项目 | 当前观察 | 进入 M+ 前必须完成 |
|---|---|---|
| 参数化 RTL 解析 | 预检时 `parse_verilog` 对冻结的 `axis_register.v` 返回 `[]`；后续 `verilog-parse-v0.3` 已有界解析参数化 header，10 项正反检查通过 | 固定此 parser 版本并继续验证与 action/Asset 实际接口；这项修复本身不构成 Memory 准入 |
| 可执行 Asset 路径 | v1/v2 两代 shadow core 均接入 domain/profile/source replay；各自 DEV fixture 在 RAM 中仅为 draft，分别 19/19 检查通过。v2 实际 router/selector 在空 Memory 上返回 NO_SKILL，尚无合法 SELECT | 从合法 TRAIN evidence 建立 Knowledge/Asset authority 后执行真实 selection 与原生 oracle；直接函数调用和 draft fixture 不计 TEHM Repair@B |
| TRAIN provenance | 唯一已审计的 skid repair 来自 DEV；没有另行登记且封存的 TRAIN baseline→action→native oracle→保持义务链 | 先选 TRAIN 任务、冻结角色和可见性，独立运行并保存原始回执；不得读取未来目标答案 |
| Knowledge/Asset authority | 尚无上述 TRAIN causal path、知识权威回执和 Asset 验证/绑定/回滚回执 | 按现有 `knowledge.authority` 的 L3、至少两个支持 lineage 默认门槛及 `assets.lifecycle` 的真实 gate 判定；不手填 `validated` 或 `promoted` |

因此当前可审计的 Memory 状态是 **M− only / M+ not constructed**，而不是“非空 M+ 但 NO_MATCH”。这一区别会保留到后续归因表，避免以软件 primitive 的单独作用冒充 Memory 增量。

下一步在独立 campaign 中预登记 TRAIN 任务并取得真实 target/preservation oracle；再基于合法证据运行 Knowledge/Asset authority 与真实 source-bound selection。若训练证据只来自单一 source lineage，则保持 shadow/BLOCKED，按实际 gate 报告，不能降低阈值。新目标选择与三态比较须等待合法 snapshot/delta 及有界新目标先验登记。上游 checkout 与现有 `_qualification` 原始证据不修改、不清理。
