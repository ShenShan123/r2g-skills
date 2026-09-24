# Revision5 R5-3：skid-buffer payload 受限绑定 DEV 原型

状态：**development-only operator/binder smoke 已执行并审计；没有注册 TEHM Asset、没有真实 route/select、没有 TRAIN memory、target transfer 或 ΔMemory。** 本报告遵循 [Revision5](../docs/TEHM_R2G_Revision5_RTL测试判定力_受限绑定与独立迁移Pilot方案_2026-09-24.md) §10–11、§16 的阶段边界。旧 `rtl_acceptance_completion_guard_binding_v1` 未修改，其 0/249 结果仍见 QF-1。

## 固定输入与开发任务

- 来源：`alexforencich/verilog-axis` SHA `48ff7a7e2ef782cf778d47910cf85835c64b1bce`；唯一 DEV 参数范围 `DATA_WIDTH=8, REG_TYPE=2`。其他参数点与模块并未被授予 oracle 资格。
- 当前 QF 索引：`/data1/zhangdy/RTL/RTL_testbench/_qualification/QF-1-r4/index.json`，digest `sha256:529794ce8a8b328ec3abcc70ea27da80099071400304a94413001ff88e9f027c`，消费 cocotb adapter v2。原始三 scope 结果及 249 条旧 binder 跳过原因均在该索引。
- 新 DEV probe：固定 seed 20260924，预登记把 `store_axis_temp_to_output` 分支的 payload 来源错换为当前输入；clean 9/9 PASS，故障 7/9 PASS、2/9 FAIL。回执 `.../_qualification/r5-dev-skid-payload-20260924/receipt-r3.json`，digest `sha256:4c61d376aa04236623964b7c25c8a8d82252745c5924f5f17207a5bcbd381aa5`。这只是一个人为构造的功能错误，不是 TEHM 找到的真实 bug。

## 新受限合约与原生动作

`research_r5_skid_binding.py` 定义 `rtl_skid_temp_payload_binding_dev_v1`，只接收内存中的 asset 模板、buggy RTL 字符串和公开参数；**不接受路径、testbench、gold diff、mutation manifest 或 oracle 输出**。该 DEV 模板不是核心注册 Asset。它要求单一模块、`REG_TYPE>1` 的唯一 generate 分支、唯一的 temp 与 output payload 寄存器及相同位宽、唯一正沿时序块、明确的 input→output/input→temp/temp→output 分支、temp keep/last 伴随写入和唯一错误 RHS。当前只支持 `DATA_WIDTH=8, REG_TYPE=2`、错误 RHS 为 `s_axis_tdata`；正确 `temp_m_axis_tdata_reg` 返回 `NO_MATCH`，复杂表达式与缺少结构拒绝。

对上述 DEV 故障源，source-only locator 返回 `BOUND`、结构见证 digest `sha256:675615f2de79d2b2562c40be56bdc39bbeb7bb3608f198c56a0a22eb83f71586`；动作只把 temp→output 分支的一个 RHS 改为暂存寄存器，并重新绑定检查候选进入 healthy 结构。结果源码 SHA256 `599fde2d6c2d806643bbffb7c444297e69a71871f962d4b741ec1914342e0d39`。DEV 研究者事后可核对它与 clean 源字节相同；**这一同一性不能作为 binder 的输入，也不构成未见迁移。**

开发边界检查由 `research_pilot.py check-r5-skid-binding` 执行，19/19 通过：唯一故障、健康无匹配、相似健康 `axis_broadcast` 无匹配、无关 UART 无匹配、参数/资产/多模块/词法拒绝、分支与赋值歧义、注释中答案路径不起作用、重复执行与篡改见证拒绝、原始源不变，以及对纯 binder 函数的文件读取拦截。后者只证明函数不调用本地文件读取 API，不等于整个未来 Agent 进程已被文件系统沙箱隔离。

由 binder 返回值生成的 candidate 在全新 evaluator 目录运行原生 `test_axis_register[8-2]`，同 seed 20260924：wrapper rc=0，内部 JUnit 9/9 PASS。INFO 日志保留实际 `iverilog` argv，VVP 内源路径是该 staged candidate；agent-inputs 目录仅含一份 buggy `rtl/axis_register.v`，未放 clean、TB、`.git` 或 mutation manifest。审计回执 `/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/binder-candidate-r2/audit.json`，digest `sha256:d3a05a7fbec215d9f35ea98d55cbbb5b71d4cefc7d31ecac21b70ac55543ed86`，冷重放 `valid=true`。前一次 `binder-candidate-r1` 未保存 INFO compile argv，保留但不作为当前完整命令证据。

复核入口：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 memory/scripts/research_pilot.py check-r5-verdicts
PYTHONDONTWRITEBYTECODE=1 python3 memory/scripts/research_pilot.py verify-r5-qf1 --index /data1/zhangdy/RTL/RTL_testbench/_qualification/QF-1-r4
PYTHONDONTWRITEBYTECODE=1 python3 memory/scripts/research_pilot.py verify-r5-dev-probe --receipt /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/receipt-r3.json
PYTHONDONTWRITEBYTECODE=1 python3 memory/scripts/research_pilot.py verify-r5-skid-candidate --receipt /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/binder-candidate-r2/audit.json
```

## 不能跨越的下一道闸门

目前资产只是 `researcher_assisted_dev_not_registered_TEHM_asset`。新 operator 没进入核心 action catalog / source binding authority，且这次任务的注入、测试和 clean 都已被开发者看到。故 R5-3 仅完成受限结构与实际动作的 DEV 验证；不能把 9/9 候选 PASS 写成 TEHM Repair@B，也不能用它创建已验证 Memory parent。后续需要先冻结 core integration 的新版本与 visibility policy，明确 TRAIN 来源及 admission 是否真正满足；支持不足就保持 shadow/BLOCKED。再预先登记新目标和独立来源关系，才进入 T/A。独立副本及异路径完整恢复仍未完成，发布或清理前不得删除唯一原始目录。
