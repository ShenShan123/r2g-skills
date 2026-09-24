# Revision5：skid payload 两来源关系预检

状态：**可分为两个候选源码家族，尚不能授予 `independent_lineages_verified`。** 本预检只服务于后续 TRAIN/目标划分，不是 oracle、绑定成功或 Memory 准入回执。不同 owner、作者或许可证都是来源线索，不是统计独立性的充分证明。

| 候选家族 | 固定 checkout / relevant RTL | 本地来源线索 | 当前关系判定 |
|---|---|---|---|
| `alexforencich/verilog-axis` | `48ff7a7e2ef782cf778d47910cf85835c64b1bce`；`axis_register.v` SHA256 `599fde2d6c2d806643bbffb7c444297e69a71871f962d4b741ec1914342e0d39`、`axis_broadcast.v` SHA256 `a644b7542adf7552cb4713ab22856e1b76c58780da970d122351e45bc0bc51bf` | 两文件头分别标 Alex Forencich 2014–2018 / 2019；仓库 `COPYING` SHA256 `8ea57f95365e9b16a5b516f422b71269183a1ae53bab5878ae6d69039e58fe77`，MIT 文本。两模块同仓库，应先算一个源码家族。 | `alexforencich` 单一候选组；register/broadcast 不当两个独立支持 lineage。 |
| `ZipCPU/wb2axip` | `2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b`；`skidbuffer.v` SHA256 `ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389` | 文件头标 Dan Gisselquist / Gisselquist Technology 2019–2025、WB2AXIP、Apache-2.0。固定 README SHA256 `e21ffe5300b43bf2c38286ee8f2e201606d0a93f974b67f4c7235bd605904dd3` 称项目 Apache 2；README 的“not a fork”仅指另一个 Wishbone→AXI bridge 项目，不用于证明与 verilog-axis 的关系。 | 单独的候选组，跨组独立性待更深审计。 |

两 checkout 的 origin 分别为 `https://github.com/alexforencich/verilog-axis.git` 与 `https://github.com/ZipCPU/wb2axip.git`，审查时都 clean。官方 [verilog-axis](https://github.com/alexforencich/verilog-axis) 与 [wb2axip](https://github.com/ZipCPU/wb2axip) 页面可核对公开项目背景；**实验版本与内容仅以上述本地 checkout/文件 hash 为准**，不用网页当前 `master` 代替锁定提交。目标源码内未见对方项目/作者的显式引用；AXIS 使用 `m_axis_tdata_reg`/`temp_m_axis_tdata_reg`，ZipCPU 使用 `r_data`/`o_data`，接口及分支形态不同。这些观察足以避免把三个 DEV 模块误称为三个来源，却不足以证明代码从未复制或共同派生。

两个 checkout 均为 shallow clone，当前本地历史不能完整核查模块最初提交、跨仓库 cherry-pick、生成器来源或长期共享代码。正式 L3 双 lineage 支持前，仍需得到可信的模块级历史/相似性与依赖审计，并保留可复核的证据；不能因为 v3 DEV binder 对两组都成功就手填 Knowledge 的独立 lineage。现阶段 `source_group_status=provisional_distinct_pending_history_audit`，TRAIN/M+ 仍 NO-GO。即使以后独立关系确认，现有故障仍是已观察 DEV，须另建合法 TRAIN 任务和未见 PILOT_TRANSFER 目标。
