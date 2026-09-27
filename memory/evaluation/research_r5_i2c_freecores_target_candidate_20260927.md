# R5 I²C 独立目标候选登记：freecores/i2c

登记发生在读取该仓库 RTL／testbench 正文、调用新 binder 或运行任何候选 oracle 之前。当前 I²C DEV binder/action 固定于主树 commit `1c01edd`，模块 SHA256 `bb8f459b38c4797de17b62655a9e13b26e16fe32417d36ab5140a6d6624963ff`；本登记后不依据本候选的绑定/测试结果修改这代软件。候选角色是 `PILOT_TARGET_CANDIDATE`，**不是** FINAL_TEST、TRAIN 或已准入的 T/A 样本。

目标取用户现有 corpus 中 `/data1/zhangdy/RTL/RTL_testbench/freecores/i2c`，原始 upstream `https://github.com/freecores/i2c.git`，固定 commit `3b067f00ccced753b0502024766a51f58f3e04bc`，checkout clean。先验只使用跟踪文件清单、`i2c.core`、CI 配置和旧仿真启动文件：该库声称 Wishbone I²C controller；CI 的 `sim-icarus` 目标引用 FuseSoC 的 `vlog_tb_utils` 与 `wiredelay`，另有 Cadence `ncverilog` 旧脚本。根目录未找到 `LICENSE`/`COPYING`；尚未审阅文件头许可、testbench 判定语义或 RTL 结构。不同 owner 不自动证明独立 source lineage。

下一步按固定顺序进行：核对许可/来源 → 用只读隔离副本执行原生 clean 终态 → 预登记一个公开 NACK 状态故障和保持义务 → clean/fault 两臂实际验证 oracle 判定力 → 在最终方法输入限制下仅让已冻结 binder 读取 buggy RTL 与公开 context，记录 BOUND/NO_MATCH/AMBIGUOUS/UNSUPPORTED 原样结果。若无合格许可、clean 原生无法运行、负控不可检出或结构不适用，分别记录其真实门禁结果；不更换目标以掩盖失败、不为此候选改 binder，也不纳入 T/A 分母。若日后为它调整软件，先将其改归 DEV 并另起 generation，永不在该代称其未见目标。

当前 I²C profile 仍缺合法 TRAIN Memory；即使此候选资格通过，也必须先完成 TRAIN 准入和 M−/M+/Mremove 冷构建，才可真正执行 T/A。此登记不授权新模型调用、生产晋升或 GitHub push。
