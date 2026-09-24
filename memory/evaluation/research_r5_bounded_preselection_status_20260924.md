# Revision5 §7.4：现有十库的有界同机制预选

状态：**仅 evaluator-private 预选，两个候选；没有 TRAIN 或 PILOT_TRANSFER 任务。** 为避免按 TEHM 成败挑目标，本次在任何新候选的 clean/fault 运行、binder 扩域或 Memory 选择之前，按“现有十库、未用于 skid binder DEV、有显式 payload 暂存结构、最多两个范围”作只读初筛。未因同为 FIFO/AXI 或仅有 `.v` 文件而纳入其它项目。

后续状态（不回填原始预选清单）：其中同来源候选已完成一次独立[原生资格测量](research_r5_axis_adapter_native_qualification_20260924.md)，对固定 8→16 skid payload 故障得到 `DETECTED`；另一候选仍未测。下文的 `UNDETERMINED` 是**预选时**快照，不代表当前两个范围均未审查。


完整路径、固定 checkout、RTL/native 入口哈希和差异化风险保存在 `/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-bounded-preselection-20260924-r1.json`，SHA256 `6af2435e95d6a5b4918ee5a57d821a4b2b06246894adc31a15b93106244eb732`。此文件属于 evaluator-private，不进入 agent staging、Memory 经验或 Git。它引用第一波 QF-1-r4 索引 SHA256 `529794ce8a8b328ec3abcc70ea27da80099071400304a94413001ff88e9f027c`；冷复核为 `valid=true`、两个候选、四个文件哈希匹配 QF 锁和 clean checkout。没有修改任何上游 checkout。

两个候选的资格差异不能合并：一个同开发来源组，有原生测试源码显示宽度矩阵、payload 比对与背压分支，但**未运行**，且等宽默认配置旁路了目标暂存结构；另一个来自不同 owner、有显式 fetch payload 暂存，但没有找到模块级直接 native test，顶层集成测试能否判错尚不清楚。两者目前都是 `oracle=UNDETERMINED`、`binding=UNDETERMINED`、`role=UNASSIGNED_CANDIDATE`，也未获得独立 lineage 认证。测试文件中的比较断言不是某个预登记故障的检出回执。

这份清单不替代 R5-4 的合法 TRAIN provenance，也不提前冻结 R5-5 的最终目标。正式 campaign 获准后，先固定角色和可见性；对每一精确范围分别完成 clean/fault 的目标义务与保持义务核验，才讨论是否入 TRAIN 或作为未见目标。若第二候选没有合格 oracle，输出“跨来源迁移条件未建立”，不把它计入分母或任意换目标。当前 M+、Mremove、跨来源迁移和论文统计仍 NO-GO。
