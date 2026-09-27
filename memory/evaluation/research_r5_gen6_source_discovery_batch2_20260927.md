# R5 gen6：同机制新来源发现批次 2 预登记（2026-09-27）

本文件先于批次 2 的任何网络搜索或新仓库 RTL／TB 查看。前批 C1 `OmniCores-BuildingBlocks::valid_ready_skid_buffer` 已按 `246bc74` 固定为 `UNQUALIFIED`：其双入口 selector 数组队列虽有 clean native PASS，却不是冻结 v8 的单暂存 payload 恢复机制；本批不会改写或补位该结论。

软件仍为 gen6 v8 `f51f8fc6d269a405e6044e7efa05b3665a58025c`，M0 report digest `sha256:af461d667815d501723fbf9b95781bc2d21d325aa8cadaa99085b63bc0d6840d`。本批只寻找能被同一机制定义审查的外部 RTL 来源，不修改或探测 binder 以筛选结果。若来源结构不同，记录 `UNQUALIFIED`；不把新版 binder 软件改进混入此代 ΔMemory 比较。

固定的公开仓库元数据查询主题为：

1. `skid buffer valid bit buffered payload mux testbench`
2. `ready valid register skid buffer captured data cocotb`
3. `skid buffer formal backpressure payload order simulation`

仅凭公开仓库描述、README、文件名、license、native/形式化测试运行说明和 commit 身份选最多两个新仓库；不得先查看目标 RTL 细节、跑 v8 binder、注入故障或依据预期修复结果挑样本。按符合元数据门槛的发现顺序锁定，负结果不补位。TRAIN 的 AXIS/ZipCPU/drewbabel、已观察的 LibSV/SkillSurf/namangoyal/GAXI/OmniCores，以及前代排除项不得换 owner 或换参数冒充新来源；`chiplukes/veriforge` 工具示例不作独立设计。

锁定后依次检查精确来源与复制/fork/generator 关系、实际 elaborated DUT、原生测试、clean baseline、合法输入及 target/preservation 判定力。只有冻结 v8 机制作用条件、oracle 和 lineage 全部成立，才单独登记 repair task 并执行相同软件和预算的 M−/M+/Mremove。若本批仍无合格目标，正式结论是“gen6 v8 跨来源迁移条件未建立”；下一步转为显式的新 DEV 软件 generation，而非偷偷放宽本批的结构筛选。无新增模型调用授权或 FINAL 统计声明。
