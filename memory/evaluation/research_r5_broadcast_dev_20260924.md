# Revision5：axis_broadcast 第二设计的 DEV oracle 判定力

状态：**DEV 资格探针已完成；不是 TRAIN、PILOT_TRANSFER 或 TEHM 修复。** 这个设计在 R5-3 已作为健康相似结构被开发者观察，不能再视为未见目标。本轮仅检验同类“暂存 beat 送出时 payload 来源错误”能否被其原生测试判定，并记录当前冻结 binder 的支持边界。

源锁：`alexforencich/verilog-axis` SHA `48ff7a7e2ef782cf778d47910cf85835c64b1bce`，目标 `rtl/axis_broadcast.v`，原生 `tb/axis_broadcast/test_axis_broadcast.py::test_axis_broadcast[2-8]`。固定 `DATA_WIDTH=8`、wrapper `M_COUNT=2`、`RANDOM_SEED=20260924`，四项内部 cocotb ID 预声明为 `run_test_001` 至 `run_test_004`。故障在 DEV evaluator 的暂存→输出分支，把 `m_axis_tdata_reg <= temp_m_axis_tdata_reg;` 改成 `<= s_axis_tdata;`。同一命令的 clean 与 fault 各使用独立 build root；上游 checkout 未修改。此 probe 只覆盖该参数点和 payload 来源错误，不推出 ready/valid 时序的普遍判定力。

最终 DEV generation：`/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/receipt-r1.json`，digest `sha256:654af62b1faf429a80f6b6fc0ea93bcc6aca07eedf310348a4bfe65ec186340d`。冷审计 `valid=true`，核对 preregistration、上游 SHA/clean 状态、全部 staged 源/测试/生成 wrapper、独立 wrapper 重建、工具版本、原始日志/JUnit/进程退出码、编译参数、VVP 中的实际 staged 源路径和 artifact arm 包含性。当前 stage 每臂只有一份 RTL、wrapper 源/生成物、测试、VVP、JUnit；没有 `.git` 或 symlink。工具为 Python 3.10.13、pytest 7.2.1、cocotb 1.7.2、Icarus 11.0，wrapper 由单独的 Python 3.11/Jinja2 3.1.6 环境预生成；完整传递依赖包/二进制 hash 尚未锁定。

| Arm | pytest rc | native JUnit | verdict | 具体观测 |
|---|---:|---:|---|---|
| clean | 0 | 4 PASS / 0 FAIL | PASS | 两路 sink 的 payload 比较均通过 |
| fault | 1 | 3 PASS / 1 FAIL | FAIL | `run_test_002` 在背压组合中断言 `rx_frame.tdata == test_frame.tdata` 失败，接收 `01 00`、期望 `00 01` |

保留的 r1 是第一次尝试：clean 有效，但 pytest 在失败输出中重复打印同一 cocotb 汇总，严格 verdict adapter 因重复终态返回 UNKNOWN；没有伪造 FAIL 回执。r2 使用 `--show-capture=no`，取得 PASS/FAIL 并独立冷重放有效；r3 再增加 artifact containment、wrapper 重建与编译参数检查，作为当前最强回执。三代目录均不覆盖。

当前冻结 `rtl_skid_temp_payload_binding_dev_v1` 对 r3 故障实际返回 `NO_MATCH: no_unique_reg_type_skid_branch`。`axis_broadcast` 的正常数据路径虽有相同的 temp→output 赋值，但它没有 `REG_TYPE>1` generate 分支且输出为 `M_COUNT` 广播，不能把现有模板硬套上去。若继续同机制支持，需在新的 DEV generation 明确第二结构 profile、歧义/拒绝条件和答案防火墙，再另行登记 TRAIN 角色与保持义务；不可把此 qualification fault 自动送入 Memory。即便日后 `axis_register` 与 `axis_broadcast` 形成两个设计见证，它们仍属于同一个 `alexforencich` source group，不支持跨来源结论。

复核入口：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_broadcast_dev_probe verify \
  --receipt /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/receipt-r1.json
```
