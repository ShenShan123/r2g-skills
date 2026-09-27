# R5 gen6：外部来源候选 C1 元数据锁定（2026-09-27）

本记录按 `research_r5_gen6_source_discovery_plan_20260927.md`，在读取候选 RTL／TB 内容、运行 native test 或 TEHM binder 之前锁定。它只是来源初筛，不是合格 repair task。

## 发现与选择

- 预登记查询：`ready valid skid buffer self-checking testbench`、`stream skid buffer cocotb backpressure`、`elastic buffer RTL testbench payload`；本次仅选第一个元数据满足条件的来源，不因后续失败换样本。
- C1：`https://github.com/Louis-DR/OmniCores-BuildingBlocks`，候选单元 `valid_ready_skid_buffer`。GitHub README 将其列为带 valid-ready 流控的双入口数据缓冲，进度标记依次对应 design／verification／documentation／constraints，其中前三项为绿色；仓库页面列出 MIT license。精确 commit、测试入口、覆盖强度及来源独立性均待 clone 后核验。
- 当前选择理由仅是独立仓库身份、声明的机制范围和验证进度；不预设 native test 真能检测背压后 payload 错误，也不预设 v8 binder 可匹配。
- 其他已见结果：`iammituraj/skid_buffer` 的顶层元数据未见 testbench；`chiplukes/veriforge` 的 skid 例子属于验证工具示例，不作为独立真实 RTL 设计来源；ZipCPU、YChud、LibSV 等已用或已审查来源按预登记排除。本轮不为凑齐两个候选扩大查询或补位。

## 后续固定审查顺序

1. 取得 C1 精确 commit、license、dirty/submodule/LFS 状态、RTL/test 文件清单及可复现依赖，不改上游源码。
2. 运行仓库原生 test，核对实际 elaborated DUT、合法输入及原始 functional verdict；只有 clean baseline PASS 才进行受控 fault sensitivity。
3. 先审查 target 与 preservation 的真实判定力，再看是否存在与 TRAIN/DEV/F1 相同的机制叶。未合格时如实记录 `UNQUALIFIED`／`UNKNOWN`／`SHARED_LINEAGE`，不运行 TEHM 三视图，也不补位。
4. 只有全部门槛成立，才另行冻结 target task，按共享软件和预算执行 M−／M+／Mremove。此记录不授权模型调用，也不把本候选记为论文统计样本。
