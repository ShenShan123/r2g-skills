# Revision5：双结构 skid payload 绑定器 DEV v2

状态：**仅 DEV source-only 绑定与动作原型，非 TRAIN / M+ / 独立迁移 / ΔMemory。** 本代新增 `rtl_skid_temp_payload_binding_dev_v2`，不修改已冻结的 v1 绑定器、R5-3 回执或两个原生 DEV oracle 回执。v2 由既有 v1 的 register 形态和新增 broadcast 形态组成；二者指向同一类“暂存 beat 输出时错误读取当前输入 payload”的有界语法差异。两设计均来自 `alexforencich/verilog-axis`，不能视为两个独立 source lineage。

## 版本和可见输入

- 绑定器：`memory/tehm/evaluation/research_r5_skid_binding_v2.py`，SHA256 `0408f50bad03e0eaa24b27b457d5dbc176b486fb867cb3a3ad9ea1b27d7a9823`；对抗检查：`memory/tehm/evaluation/research_r5_skid_binding_v2_checks.py`，SHA256 `3e8737d3b3db33816688e336d5fd94b78bb28fe32d8ebfaf0b3caee4cf875937`。
- v2 还调用冻结 v1 helper（`research_r5_skid_binding.py`，SHA256 `9018a444649ed05e50781795c3c15a641ef6098206a0df373a9ffb7a95273106`）和 `_balanced_end` 所在的 `verilog_parse.py`（SHA256 `556f286b4224bfb3f3b89b9935198c8729c4dd24962373be05d97822a6529c8f`）；这些依赖同属本代代码锁，不修改旧 generation。
- 唯一允许运行时输入：选中的 draft Asset 的精确 `binding_template`、一份有界的 buggy RTL 文本、公开参数。register 只接受 `DATA_WIDTH=8, REG_TYPE=2`；broadcast 只接受 `DATA_WIDTH=8, M_COUNT=2`。不同、附加或答案路径参数均拒绝。
- v2 不读文件。调用方不能传入 gold、testbench、mutation recipe、原生 oracle 结果或 `.git`。本轮 evaluator 的对抗测试读取 clean/fault 文件用于比较；这些不是绑定器的运行时输入。无生产 Asset 注册或 Knowledge authority。
- 结构 witness 要求一个 module、可支持词法、唯一相关输出/寄存器宽度/时序块/三条数据路径，direct 与 capture 取输入，而 temp→output 的 keep/last 取 temp。只有当该分支的 payload 唯一错误取 `s_axis_tdata` 时才 `BOUND`；已正确取 `temp_m_axis_tdata_reg` 返回 `NO_MATCH`。多分支/多赋值 `AMBIGUOUS`，不支持的参数、词法或结构 `UNSUPPORTED`。改写前重算精确 witness/source hash，改写后检查健康形态；只替换一处 RHS，不断言功能正确。

## 可重放 DEV 结果

两份原生 DEV 回执冷复核均 `valid=true`：register `sha256:4c61d376aa04236623964b7c25c8a8d82252745c5924f5f17207a5bcbd381aa5`（clean 9/9 PASS、fault 2/9 FAIL），broadcast `sha256:654af62b1faf429a80f6b6fc0ea93bcc6aca07eedf310348a4bfe65ec186340d`（clean 4/4 PASS、fault 1/4 FAIL）。v2 对两份故障源码均 `BOUND`，且实际动作结果与各自已通过原生测试的 clean staged RTL **逐字节相同**；这不是新增独立 candidate 原生运行。37/37 对抗检查通过，涵盖健康/无关拒绝、错参数、混合形态、额外答案字段、错误 Asset、双 module、词法拒绝、重复分支/赋值、注释隔离、单次编辑、幂等、过期/篡改 witness 和无文件访问。`axis_fifo.v` 因不支持词法返回 `UNSUPPORTED`，属于拒绝绑定；不能说是语法 `NO_MATCH`。

复核入口（只读冻结数据）：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_skid_binding_v2_checks \
  --register-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/clean/stage/rtl/axis_register.v \
  --register-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/fault/stage/rtl/axis_register.v \
  --broadcast-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/clean/stage/rtl/axis_broadcast.v \
  --broadcast-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/fault/stage/rtl/axis_broadcast.v \
  --unrelated /data1/zhangdy/RTL/RTL_testbench/alexforencich/verilog-axis/rtl/axis_fifo.v
```

## 下一门槛

v2 尚未接入真实 router/select、Asset lifecycle 或原生 candidate runner；旧 shadow core 仍只使用 v1。`axis_broadcast` 已被 DEV 开发观察，不能作为未见目标。进入 R5-4 前仍需合法 TRAIN 角色和独立证据，不能把 DEV mutation 或逐字节等价直接升级成 TRAIN、`validated` 或 M+。若仅此同一 source group 的两设计，仍不满足跨来源 Memory authority / 论文外推。独立 corpus 中的目标必须先封存 scope、oracle 和可见性，再运行同一冻结合约，失败与拒绝同样计入分母。
