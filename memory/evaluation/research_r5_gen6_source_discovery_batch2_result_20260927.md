# R5 gen6：同机制来源发现批次 2 结果（2026-09-27）

依预登记 `894e6b2`，在公开网页上执行 `skid buffer valid bit buffered payload mux testbench`、`ready valid register skid buffer captured data cocotb`、`skid buffer formal backpressure payload order simulation` 三条固定主题查询，并各执行一次 GitHub 限定查询与不限定站点查询；未运行 binder、未读取新候选 RTL／TB、未克隆本批新仓库。结果为 `NO_METADATA_QUALIFIED_CANDIDATE`（0/2 上限），不是 TEHM 修复失败率 0/0 的样本。

| 发现项 | 元数据判定 |
|---|---|
| `ZipCPU/eth10g`、`ZipCPU/wb2axip` | ZipCPU 已在 gen6 TRAIN；不以另一仓库路径自动授予新来源资格。 |
| `bensampson5/libsv`、`YChud/Parameterizable-Credit-Based-FIFO-ready-valid-Backpressure` | 前者已作 DEV/TRAIN；后者在先前审查中 oracle 不合格。本批不重入。 |
| `iammituraj/skid_buffer` | README 声称 skid/pipeline skid，当前公开顶层文件清单无可运行 testbench 或测试入口。 |
| `cepdnaclk/e19-co227-Developing-image-capturing-and-analysing-system-using-FPGA` | README 有系统级 AXIS/skid 描述与 ModelSim 验证叙述，但列出的 `tb/` 无独立 skid TB，仓库元数据未显示 license 或可复现的本地 skid native test 命令；不足以凭元数据锁定本批的独立目标。其项目模板生成关系还需另审，不能据 owner 名称认定独立。 |
| `alexforencich/cocotbext-axi`、`corundum/corundum` | 前者是已用 AXIS 来源的验证库，后者 README 声明使用同体系 `verilog-ethernet` 模块；不自动认定新的同机制来源。 |
| `RasmusGOlsen/cocotbext-interface`、教程／练习站点 | 验证工具或教学材料，不是同时具备独立 RTL 仓库、固定 native 测试入口与来源锁的候选。 |

公开 URL 索引：[ZipCPU eth10g](https://github.com/ZipCPU/eth10g)、[ZipCPU wb2axip](https://github.com/ZipCPU/wb2axip)、[libsv](https://github.com/bensampson5/libsv)、[YChud](https://github.com/YChud/Parameterizable-Credit-Based-FIFO-ready-valid-Backpressure)、[iammituraj](https://github.com/iammituraj/skid_buffer)、[cepdnaclk](https://github.com/cepdnaclk/e19-co227-Developing-image-capturing-and-analysing-system-using-FPGA)、[cocotbext-axi](https://github.com/alexforencich/cocotbext-axi)、[Corundum](https://github.com/corundum/corundum)、[cocotbext-interface](https://github.com/RasmusGOlsen/cocotbext-interface)。

因此 gen6 v8 在目前这两批有界发现下的跨来源 PILOT_TRANSFER 条件未建立。C1 的 clean PASS 保留为资格证据，C1 的结构不匹配与本批零候选均不得隐去。没有 target fault、三视图 oracle 运行、正修复或 ΔMemory attribution；不得宣称统计实验已开始。

下一工程决策按 R5 §10.1：把冻结 v8 的精确语法覆盖不足视作 DEV 方法缺口，另立软件 generation；先明确可泛化的受限机制语义、保留 answer firewall 和负拒绝测试，再从有独立原生 oracle 的来源构建 TRAIN，并重新锁定未见目标。不能把新 binder 的能力增加计入 gen6 的 Memory 增益。
