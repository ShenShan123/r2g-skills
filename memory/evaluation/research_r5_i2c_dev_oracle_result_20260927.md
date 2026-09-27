# R5 I²C DEV 原生 oracle 可运行性与指定故障判定力

先后冻结了[可运行性范围](research_r5_i2c_dev_feasibility_20260927.md)和[负控算子/判定规则](research_r5_i2c_dev_fault_probe_20260927.md)，再对 `alexforencich/verilog-i2c` 提交 `a65be4045e898a52e791c6ee71f8f79a7cd2e129` 的隔离副本运行 native MyHDL/Icarus 测试。上游 checkout 与 pinned MyHDL checkout 均保持干净。MyHDL 固定 tag `0.11` / `c3a74de25f2d60284f909da1c6793793faa62b17`；系统缺少 `ensurepip`，因此使用无 pip 的隔离 venv 内复制同一提交的纯 Python 包，并在独立目录用本机 Icarus 11.0 编译 `myhdl.vpi`（SHA256 `07994ff93321a9e7973e7f329c170d186646f8e06a063436bede41f893eee96c`）。未改全局 Python 环境。

隔离路径：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/i2c-master-r1/`。clean `run-r1` 的五个编号阶段全部出现、原生 Python 退出 0、五份源/测试文件不变；回执 SHA256 `de3fca2dad07213bd73cb27efaa529fba03c67f919a8e666889b1e674f6cd005`，stdout SHA256 `f6a1917e3b726e57d1d930a4c035bd6ab450cf26e2da8a68b795fbb802c03983`。原始日志、编译 `.vvp`、波形和哈希在 `run-r1/`，不是仅凭汇总 PASS。

fault `fault-r1` 只将非 reset 的 `missed_ack_reg <= missed_ack_next;` 改为 `missed_ack_reg <= 1'b0;`；其余四个 native test 文件与 clean 逐字节相同，源 diff 只有该行。RTL SHA256 `b4951f32e3387cf2a9984999defb66e49c7f2fa3832ea9a39bd8555c25d7accf`。原生运行前四阶段完成，第五阶段对不存在设备 `0x52` 的 `assert got_missed_ack` 失败，Python 退出 1；raw log SHA256 `ab218a668688e5b88ec413c24aeee89d2562567dc769cb03ffc570312cb3606a`，fault 回执 SHA256 `e690ddb391cf822e41b9a4a99e08350d13dc187a0b50dada83e7c85254ebcf26`。这符合事先登记的 `DETECTED_FOR_REGISTERED_SCOPE`，不是编译失败或超时。五个测试阶段不能算五个独立故障/样本。

本结果仅将 `missed_ack` 锁存错误的 native oracle 资格提升到 DEV 指定范围；尚无 answer-free binder、动作模板、合法跨来源 TRAIN Memory、独立未见 target、M−/M+/Mremove 执行或 ΔMemory 归因。`freecores/i2c` 的 RTL/TB 本轮未读，许可与 lineage 也未认证，仍不得用作未见测试结论。无模型调用、无 GitHub 推送、无生产晋升。
