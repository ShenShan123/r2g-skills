# freecores/i2c I²C NACK 目标候选 oracle 合约（执行前冻结）

依照[候选预登记](research_r5_i2c_freecores_target_candidate_20260927.md)，固定 `freecores/i2c` commit `3b067f00ccced753b0502024766a51f58f3e04bc`。本轮仍是 `PILOT_TARGET_CANDIDATE_QUALIFICATION`；故障构造与 oracle 原始结果只留 evaluator 工作区，不给冻结 binder。该仓库六份本次用到的 RTL/TB 文件头均保留原作者的使用/分发条件和免责声明；没有根 `LICENSE`，不能标成某个 SPDX 许可证或直接授予公开重新发布许可结论。

编译范围固定为三个 `rtl/verilog/i2c_master_{bit_ctrl,byte_ctrl,top}.v`、三个 `bench/verilog/{i2c_slave_model,wb_master_model,tst_bench_top}.v`，顶层 `tst_bench_top`，Icarus `-g2012`，包含路径 `rtl/verilog`，无 `WITH_VTU`、无 `WAVES` 宏；每臂隔离构建，compile≤60 秒、vvp≤120 秒。干净臂已执行，编译/仿真退出 0、stderr 空，stdout 有 `Check for nack` 和末尾 `Testbench done`，无 `ERROR`；本事实只是 clean 可运行性，不替代故障判定力。

唯一负控：在 evaluator 私有副本的 `rtl/verilog/i2c_master_top.v` 中，仅将非 reset 的 `rxack <= irxack;` 改为 `rxack <= 1'b0;`；两条 reset 清零赋值、其余 RTL、原生 testbench 和命令全保持原样。公开状态 `SR[7]` 应在缺席地址阶段由 ACK 采样传播；本故障阻断该传播，不是语法/编译故障。精确修改必须由单行 diff 与全文件 hash 验证。

判定规则：clean 需编译/运行到终态、出现 `Check for nack`、`Testbench done`，且 stdout/stderr 无 `ERROR`。fault 敏感性只在编译成功、仍到达这两个 marker、出现原 testbench 的唯一 `ERROR: Expected NACK, received ACK`、无其他 `ERROR`、且此前数据读写保持检查未报告错误时才记 `DETECTED_FOR_REGISTERED_SCOPE`。原 testbench 遇错仅 `$display`，故 fault 进程 exit 0 **不等于 PASS**；超时、未到 NACK、其他错误均 `UNDETERMINED`，指定错误缺失则 `MISSED`。一个 bench 不拆成多个独立样本。

之后只用事前冻结于 `1c01edd` 的 I²C DEV binder 对 buggy RTL 与公开 `wishbone_status_err_bit_7` 需求作一次 source-only probe；当前接口若不支持该公开 profile，原样记 `UNSUPPORTED`，不得在这代补一个 profile 追求正结果。此资格结果不进入 TRAIN/heldout final 或 M−/M+/Mremove；若以后以此源扩展 binder，先重分类为 DEV 并另起软件代次。
