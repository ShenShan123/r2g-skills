# Sky130HD Synth-Memory Recovery Screen

## 结论

本轮没有新增可 promotion 的 Recipe。`synth_memory_relax`
(`SYNTH_MEMORY_MAX_BITS=65536`) 可以解除默认 4096-bit memory cap 的综合
abort，但在冻结的 Sky130HD、100 MHz、fixed-footprint、strict-signoff 主轨中，
尚未证明它能产生可发布的物理结果。因此不能把“越过综合”写成 win，也不能
将该方向加入正向 learner evidence。

## 来源与前提

所有项目都由公开 URL、精确 commit 和 source-byte digest 绑定。候选只在当前
综合日志同时满足 `SYNTH_MEMORY_CAPACITY` 与最大单一 memory 在
`4096 < bits <= 16384` 时才允许进入筛选；时钟、SDC、CORE_UTILIZATION=20、
die/core footprint、source closure 和完整 check set 均保持不变。

| 来源 | 基线最大 memory | 基线结果 | 处理 |
| --- | ---: | --- | --- |
| emaczero `eth_mac` | 180,224 bits | default cap abort | 超过安全阈值，需 RAM macro，拒绝。 |
| kemetcore `racore_lite` | 32,768 bits | default cap abort | 超过安全阈值，拒绝。 |
| v8cpu `v8cpu_mem_sim` | 8,192 bits | default cap abort | 合法候选，完成全流程筛选。 |
| smit RV32I `data_mem` | 8,192 bits | default cap abort | 合法第二来源；首个完整 screen 已失败后停止，未计为 A/B 证据。 |

## 完整候选结果

`v8cpu` 的基线只在综合阶段因 4096-bit cap 中止。候选臂唯一有效配置差异为
`SYNTH_MEMORY_MAX_BITS=65536`：它清除了 synth abort，完成 ORFS，并通过 LVS、
antenna 和 RCX；但耗时 6200.767 秒完成 ORFS、849.563 秒完成 signoff，最终仍有
12 个 route violations 和 16 个 `m3.2` DRC violations。setup/hold WNS 分别为
+6.50698 / +0.968668 ns。故 strict gate 为 dirty，属于全局回归，不能进入 A/B，
不能生成 win、promotion 或正向学习证据。

第二个独立候选已越过综合、floorplan、place 和 CTS，但在 detailed route 超出
预注册端到端预算；在第一个完整候选已被严格拒绝后停止。它只保留为开发期
资源边界观测，不计为失败投票或正向证据。

## 同时修复的资格化缺陷

`run_repair_family_probe.py` 原先会把 `--dependency-file` 与编译单元一同按 HDL
后缀检查，导致合法的 `$readmemh` `.dat` 依赖无法被冻结。现在编译单元只接受
`.v/.sv`，而 dependency closure 可安全保存 `.dat/.mem/.hex/.vh/.svh` 等数据或
include 文件，且不会把它们传给 `VERILOG_FILES`。对应回归测试通过：20 passed。

## 下一步

不要继续扩大 memory cap 或以降低 utilization 补救本轮问题：那会改变 fixed-
footprint 任务。下一轮应回到当前自然缺口中可在既有物理规模下闭合的机制，优先
筛选独立的 `m3.2` 几何/引脚分布或中等 setup-timing family，并在单对 sandbox
strict-clean 后再投入完整 A/B。
