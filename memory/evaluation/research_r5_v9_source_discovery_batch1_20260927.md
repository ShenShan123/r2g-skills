# R5 v9 独立来源发现批次 1：执行前登记

角色：`SOURCE_DISCOVERY`，不是 PILOT_TRANSFER／FINAL_TEST。前批 gen6 C1 结构不合格和批次 2 零候选均保留；v9 APEX 是已观察 DEV，不能重新命名为未见目标。当前 v9 代码及其 APEX 结果已提交，但 v9 尚未形成 TRAIN Memory 或完整软件 epoch。本批不运行方法三视图。

## 固定缺口与边界

寻找已公开、非上述已观察来源的真实 RTL skid／elastic-buffer 设计，最好自带可运行的 self-checking test，能实际判定阻塞后已接受 payload 的值、顺序和非目标保持义务。元数据初筛只读仓库说明、文件名、测试命令、license 和 Git 身份；**在锁定候选之前不读其 RTL／TB 内容、不运行 binder、不注入故障，也不按潜在 TEHM 成功换样本**。原生测试不覆盖目标义务时如实标 `UNQUALIFIED`；开发增强 oracle 要升代，不能混同 native。

固定查询，按顺序各一次（公开网页/GitHub 搜索均可）：

1. `skid buffer ready valid self checking testbench verilog github`
2. `elastic buffer SystemVerilog backpressure scoreboard test github`
3. `stream skid buffer formal payload order open source rtl`

最多锁定前两个同时具备独立仓库身份、可定位 RTL、可定位测试入口、许可文件或明确许可说明的候选；不因后续结构或测试负结果补位。排除：AXIS、ZipCPU、drewbabel（已用 TRAIN）；APEX（v9 DEV）；LibSV、SkillSurf、namangoyal、GAXI、OmniCores、dozecat、LibFPGA、YChud、PULP、以及前批已审查或共享代码的仓库。不同 owner 仅是初筛，fork、复制模块、共同 generator 或依赖需要后续核查。

先写候选 URL、精确 commit、文件名、license、测试入口和选择顺序，再看代码。之后逐项审查 clean native、实际 skid 激励和 fault sensitivity；即使 clean PASS，也需 target/preservation 判定力及 source-only 结构资格。任何不合格均保留在本批结果，不计方法修复分母。本批不授权模型调用、不修改冻结 gen6、不发布远端。
