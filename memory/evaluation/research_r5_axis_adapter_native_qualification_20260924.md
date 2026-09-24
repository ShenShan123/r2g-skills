# Revision5：`axis_adapter` 8→16 skid payload 原生资格回执

状态：**`QUALIFIED_FOR_DECLARED_SCOPE`（仅该预登记故障的 native 检出）；非 TRAIN、非未见迁移、非修复结果。** 本次由[有界预选](research_r5_bounded_preselection_status_20260924.md)中的同来源候选进入 evaluator-only 资格测量。另一不同 owner 候选未运行、未注入，仍为 `UNDETERMINED`。

固定输入：`alexforencich/verilog-axis` clean checkout `48ff7a7e2ef782cf778d47910cf85835c64b1bce`，`rtl/axis_adapter.v` SHA256 `42e8a1289d4aa2dfd90785c983d3f5790c9c4a2cb957cfd42ea1d39435032c67`；原生 pytest node `tb/axis_adapter/test_axis_adapter.py::test_axis_register[8-16]`，`S_DATA_WIDTH=8`、`M_DATA_WIDTH=16`、其它公开参数由测试源码固定，`RANDOM_SEED=20260924`。此参数进入 upsize 分支；等宽默认配置会旁路暂存，不能代替本范围。测试使用原生 cocotb 的 payload 比对与背压组合，未修改测试源码。

故障在任何仿真前私有预登记：仅把上游第 211 行的 `s_axis_tdata_reg <= s_axis_tdata;` 在 fault stage 改为 `<= ~s_axis_tdata;`，影响 upsize 背压时的 payload 暂存。clean/fault stage 除这一行外源码相同；分别编译并使用各自的 `sim_build`、JUnit 和 VVP。上游 checkout 未修改，stage 不含 `.git` 或 symlink。原始预登记、日志、退出码、JUnit、编译产物与回执保存在 `/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-q-axis-adapter-20260924-n8kAEk/`，不进入 learner staging 或 Git。回执 `receipt-r1.json` digest `sha256:0de756e34ce05ebdd6abe3b3c8a4e0901a2201e9e366e69d3e392b4366dcd35c`。

| Arm | pytest rc | Native cocotb 终态 | 已观察失败 |
|---|---:|---|---|
| clean | 0 | 9/9 PASS | 无 |
| fault | 1 | 7/9 PASS、2/9 FAIL | `run_test_002` 与 `run_stress_test_002` 均在 `rx_frame.tdata == test_frame.tdata` 的 payload 断言失败。 |

[运行与冷审计器](../tehm/evaluation/research_r5_axis_adapter_q_probe.py)使用现行 `r5-cocotb-junit-verdict-v2` 交叉检查原生 JUnit、日志、pytest 退出码、9 个预声明 cocotb ID 和 seed；VVP 中实际编译的 RTL 路径绑定对应 stage。独立进程冷重放 `valid=true`、`sensitivity=DETECTED`；[对抗检查](../tehm/evaluation/research_r5_axis_adapter_q_checks.py) 5/5 PASS，内存篡改 RTL、JUnit、VVP 或敏感性结论均遭拒绝。工具现场为 `/usr/bin/python3` 3.10.13、pytest 7.2.1、cocotb 1.7.2、Icarus/VVP 11.0；完整传递 Python 依赖与二进制包哈希、外部备份及异路径恢复仍未封存，不能声称携带式重放完成。

复核命令：

```sh
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_axis_adapter_q_probe verify \
  --receipt /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-q-axis-adapter-20260924-n8kAEk/receipt-r1.json \
  --corpus /data1/zhangdy/RTL/RTL_testbench
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 memory/tehm/evaluation/research_r5_axis_adapter_q_checks.py \
  --receipt /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-q-axis-adapter-20260924-n8kAEk/receipt-r1.json \
  --corpus /data1/zhangdy/RTL/RTL_testbench
```

这是一处故障、一个配置范围、9 个同一模块内部用例；两项 FAIL 不是两个独立缺陷。已证明的是条件性 payload 来源错误可被此 native suite 检出；ready/valid 时序、丢拍、其它宽度以及修复后的目标／非目标保持义务均未被本故障单独证明。该案例现已被观察为资格/DEV 材料，若以后用作 TRAIN，必须重新登记角色及来源；不能当作最终未见目标，更不能用于手填 Knowledge lineage 或 M+ 增益。
