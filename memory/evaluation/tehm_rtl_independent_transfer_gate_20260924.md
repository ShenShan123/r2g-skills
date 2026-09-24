# TEHM 独立 RTL source-lineage 迁移门禁（2026-09-24）

状态：**只读库存与源代码预筛完成；原五案机制协议尚未迁移，外部 transfer/ΔMemory 实验 NO-GO。** 本记录不是实验结果或论文 final protocol。执行代码 HEAD：`47fefec6c12a66000ecd63c5806152918d061dcc`。

## 固定的迁移边界

目标是把既有 answer-free source binding、真实 policy load、NO_MEMORY/loaded/removed-ΔMemory 执行、target+regression oracle、non-target/rollback、独立 cold audit、lineage 隔离和 reason-aware attribution 作为同一协议搬到独立 RTL 来源；不能放宽 binder、补人工答案、把 compile/flow PASS 记为功能修复，或把同来源变体算作多个独立样本。旧五案不再用于结果打磨。

## 本次只读证据

- 通过当前 `research_pilot.py inventory` 清点 `/data1/zhangdy/RTL`，输出在本机隔离路径 `/data1/zhangdy/.cache/tmp/tehm-rtl-independent-inventory.kR0KdC/inventory/`。独立 `verify-inventory` 通过；原目录 before/after snapshot 相同。inventory digest：`sha256:7c7b6e844ac2cbe3eb6c873f16b210391f25a4d6e1553d995c6097ca29ed4319`；artifact manifest digest：`sha256:b5d2069c960ebf7cd83e4d068c53573906db050cc33207817c71a1844faa4a5d`。该缓存路径不是长期归档或公开发布产物。
- 552 个库存候选：537 `NEEDS_ADAPTER`，15 `UNSUPPORTED_CURRENT_PROFILE`；没有 `READY_RTL_REPAIR`。495 个有显式 `src_manifest.txt` filelist；57 个仅词法序推断。536 个 top 来自显式配置，14 个未解析，2 个启发式。41 个不同的**声明** lineage group 不能直接当作 41 个已认证独立来源；217 个来源为未经核验的元数据声明，335 个未解析。
- inventory 标记 549 个功能修复 oracle `unavailable`、3 个 `unverified`。这 3 个是 Faraday DSP/BTB 下文件名含 `tb` 的普通 RTL，并非经确认的 target/regression testbench。全目录按常见 testbench/formal 文件名没有发现可绑定的功能 oracle；这不排除上游仓库另有尚未引入的测试。
- 既有 source-only `rtl_acceptance_completion_guard_binding_v1` locator 对 1,642 个普通 `.v` 文件做只读有界检查：39 个大于 2 MB 未扫描，其余 1,603 个**零匹配**。主要拒绝是词法不支持 1,208、FSM/时序结构不符合 243、多模块 144；另 6 个时钟条件、2 个状态寄存器条件不符合。该结果只说明当前 frozen locator 的静态覆盖，不是运行成功率；未检查的文件保持 UNKNOWN。
- inventory 自动 shortlist 从 210 个 parser-clean 建议中取 8 个不同**声明** lineage 的 `PROPOSED_NOT_RUN`；它们只可用于有界 frontend/来源审计，不因入选而获得 oracle、绑定或独立来源资格。

## 阻断与下一闸门

1. 用户已删除仓库外 `tehm-campaigns/`。README 中五案 r20 的冻结 policy/load、原始 warm/cold oracle、四项 gate 与 attribution digest 仍是历史叙述，但本机原始 campaign 不在；只凭摘要无法按“原封不动”重新执行其精确协议。仓库内旧入口可由 Git 历史找回，但这不恢复外部原始证据或 policy 快照。
2. 当前本地 RTL 库尚无已认证的独立来源 + 冻结 target/regression oracle + 当前 source-only binder 命中三者交集。因此没有启动修复执行、ΔMemory 消融或论文统计；分母、零匹配和 UNKNOWN 均保留。
3. 下一步应先获取旧五案原始冻结包/执行协议（若仍有备份），并为至少两个外部来源提供同版本上游 commit、授权/许可、准确 filelist、可独立运行的目标与非目标 oracle 及目标失败；然后在结果可见前冻结 development/final split、预算和 acceptance contract。用原 binder 做 source-only screening；若仍零覆盖，应把“扩 binder”作为**新协议版本/开发期工作**，不能称为同协议迁移。只有通过这道闸门后，才运行 matched NO_MEMORY/loaded/removed-ΔMemory 与独立 cold replay，并按 source lineage 聚类统计。

本次没有修改 `/data1/zhangdy/RTL`、canonical/production memory 或冻结旧证据；没有 EDA/model 调用和 repair outcome。
