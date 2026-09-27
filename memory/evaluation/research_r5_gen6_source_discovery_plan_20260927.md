# R5 gen6：新来源定向发现预登记（2026-09-27）

本登记先于本轮新仓库搜索、获取或查看其 RTL/TB 内容。目标是寻找与 gen6 v8 TRAIN 三个固定来源组不同、具有可核查背压后 payload 义务的真实 RTL scope；不是按 TEHM binder 命中或正结果筛选。方法软件 epoch 为 `f51f8fc6d269a405e6044e7efa05b3665a58025c`，TRAIN Memory report digest 为 `sha256:af461d667815d501723fbf9b95781bc2d21d325aa8cadaa99085b63bc0d6840d`。后续文档提交不改变该方法源码；任何方法改动都另立 generation。

当前已知排除：AXIS、ZipCPU、drewbabel 为 gen6 TRAIN；LibSV、SkillSurf、namangoyal 等已用于前代 DEV/TRAIN；GAXI `gaxi_skid_buffer` 为已观察 F1；verilog-axis `axis_pipeline_register` 和 PULP `axi_cut` 共享已用机制叶；dozecat 原 RTL 在合法增强驱动下 FAIL、LibFPGA 原 native 激励零 skid、APEX 旧 standalone TB 缺失、YChud 旧目标 oracle 不合格。它们不会因换名、换 top 或换代次自动成为独立未见目标。

本轮仅做一批最多两个新仓库的元数据发现。固定查询主题为公开的 `ready valid skid buffer self-checking testbench`、`stream skid buffer cocotb backpressure`、`elastic buffer RTL testbench payload`；只用公开仓库描述、文件名、测试运行说明、license 和来源身份初筛，不运行 TEHM binder 或查看潜在答案。优先顺序：非上述已用来源；有单独可运行的 native/形式化测试入口；测试声称覆盖 backpressure 与 payload/order；精确 commit 可取得；license 与依赖可保存。按发现顺序登记最多两个符合元数据条件的候选，不因后续不匹配或负结果补位。发现记录须保存查询、时间、URL、候选和排除理由。

获取后再逐项审查精确 Git commit、dirty/submodule/LFS/filelist/testbench/toolchain、真实 elaborated DUT 依赖和与 TRAIN/DEV/F1 的机制叶关系。只有 clean baseline、合法输入、预登记 target FAIL／preservation PASS 的有效 fault probe、非空且一致的 raw oracle 和来源暴露审计全部成立，才单独冻结 constructed repair task。未建立者报告 `UNKNOWN`／`UNQUALIFIED`／`SHARED_LINEAGE`，不进入修复分母；不改测试或方法来救回同一冻结代次。

后续正式三视图目标实验必须在同一任务、同一 frozen runtime/controller/oracle/binder/budget 下各自重新加载 M−／M+／Mremove，实际 route、select、source-only bind、构建候选并运行 fresh oracle。所有 no-match、拒绝、歧义、无动作和失败保留。当前登记不授权新模型调用，也不声称统计独立、未见修复或论文规模样本已具备。
