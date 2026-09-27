# R5 v9 独立来源发现批次 1：元数据结果

按已提交的 `research_r5_v9_source_discovery_batch1_20260927.md` 三条固定查询，各进行一次 GitHub 定向搜索；没有读取新候选 RTL／TB、克隆仓库、运行 binder／oracle、施加 fault 或模型调用。结果为 `NO_METADATA_QUALIFIED_CANDIDATE`，0/2 名额；不把 0/0 写成修复失败率。

| 发现项 | 元数据结论 |
| --- | --- |
| [YChud credit FIFO](https://github.com/YChud/Parameterizable-Credit-Based-FIFO-ready-valid-Backpressure)、[ZipCPU wb2axip](https://github.com/ZipCPU/wb2axip)、[OmniCores](https://github.com/Louis-DR/OmniCores-BuildingBlocks)、[dozecat axi_lib](https://github.com/dozecat/axi_lib) | 已审查或已用来源，按执行前排除表不重入。 |
| [iammituraj skid_buffer](https://github.com/iammituraj/skid_buffer) | 仓库顶层列出 RTL 和波形图片，没有可定位的自检查测试入口；此前批次已审查。 |
| [jeras synthesis-primitives](https://github.com/jeras/synthesis-primitives/blob/main/doc/handshake.adoc) | 有 SystemVerilog RTL/TB 和许可文本，但其公开文档明确把实际实现范围列为 datapath 与 backpressure register slice；`skid buffer` 是相关术语／参考，而非已定位的本批单暂存 drain DUT。公开运行说明依赖 Questa，并注明当时 Verilator 路径不能正确跑该 TB。未据关键词硬判同一机制。 |
| [Asresh Design-Verification-Projects-Everyday](https://github.com/Asresh/Design-Verification-Projects-Everyday) | README 列出 day10 AXI-Stream skid DUT、UVM agents、顺序 scoreboard 和回归说明，机制元数据有潜力；所见顶层文件列表及 README 未列许可文件或明确许可。按预登记准入条件，当前标为 `LICENSE_UNCONFIRMED`，不锁定、不读取 day10 RTL/TB。若后来核实许可，须新批次事前登记，不能事后补位本批。 |
| [chiplukes veriforge](https://github.com/chiplukes/veriforge/blob/main/notes/getting_started.md) | 验证工具的 skid 示例，前批已排除为独立真实设计来源。 |
| 其他返回的 FIFO/UVM/scoreboard 或 GPU 集成项 | 页面元数据未同时定位独立的本机制 skid RTL、可执行义务测试与许可；不凭搜索摘要计入样本。 |

因此当前已观察的 v9 APEX 正例仍为 `DEV_ONLY`。本批未建立新的 PILOT_TRANSFER task，未建立 v9 TRAIN Memory、三视图或 ΔMemory 归因；也未证明所有公开仓库均不合格。下一动作应先解决 **方法侧范围**：现有 v9 完整模块 grammar 只证明 APEX 风格一个进程，不能通过换来源名制造跨设计迁移。若扩展到不同实现，需要公开新 DEV generation、相应负拒绝与 oracle，随后重新冻结软件和 TRAIN；新目标需独立预登记且不得使用 DEV 结果调参。
