# Revision5 R5-4：axis_register 第二候选来源 TRAIN 重跑

状态：**`axis_register[8-2]` 的 researcher-assisted TRAIN 重跑完成；
仍非独立 lineage 认证、核心验证 transition 或合法 M+。** 用户授权的
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot` 是 evaluator-only 工作根；
与原 `_qualification` 和上游 checkout 隔离。此故障及修复在 DEV 已观察，
所以明确登记 `TRAIN_REUSED_DEV_RESEARCHER_ASSISTED`、`unseen_transfer=false`。

固定 `alexforencich/verilog-axis` HEAD
`48ff7a7e2ef782cf778d47910cf85835c64b1bce`，原生
`tb/axis_register/test_axis_register.py::test_axis_register[8-2]`，
公开参数 `DATA_WIDTH=8, REG_TYPE=2`，固定 seed `20260924`。
在新执行前把九个 cocotb 用例分为两个目标检查
`run_test_002`、`run_stress_test_002`，以及其余七个保持检查。
这种选择来自先前 DEV 观察，合法用于 TRAIN，但不能作为未见目标指标。

`training/axis-register-skid-train-r1/preregistration.json` SHA-256
`240fc574525afe56dde0f81959cfaac15411fe32a649f62b4c448154bee6830e`，
于三臂原生仿真之前封存；固定源码、native test、Python 依赖树、
工具二进制及 binder/判定器代码哈希。source-only v3 binder 只接收
buggy staged RTL、公开参数和 draft 模板；clean 源、注入坐标及测试由
evaluator 私有保存。agent-inputs 仅含一份 fault `rtl/axis_register.v`。

| Arm | 原生 JUnit | 两个目标检查 | 七个保持检查 |
|---|---|---|---|
| clean | 9/9 PASS | 2 PASS | 7 PASS |
| fault | 7/9 PASS、2/9 FAIL | 2 FAIL | 7 PASS |
| source-only candidate | 9/9 PASS | 2 PASS | 7 PASS |

各 arm 独立 stage/sim_build，记录 pytest 命令、INFO 中的实际 Icarus
编译命令、JUnit、日志、退出码和 VVP 的 staged RTL 路径。
独立进程冷复核 `valid=true, errors=[]`，回执 digest
`sha256:731625b13c1aba1a8fdd8450e9ce0c24e4274815b88d7bd5c39507576618d711`；
原始 `receipt.json` 文件 SHA-256
`88623df82c88990219789620245eefe6a2f6784979847a1cdc161cd7a28b40f1`。
脚本 [`research_r5_train_axis.py`](../tehm/evaluation/research_r5_train_axis.py)
SHA-256 `f2c8e38ac853f246c0ac74f2d869afeaa01d42b974fb273ef69934967aaa9864`。
上游 checkout 运行后仍 clean。复核命令：

```sh
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_train_axis verify \
  --work /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/axis-register-skid-train-r1
```

与 [ZipCPU TRAIN 重跑](research_r5_train_zipcpu_campaign_20260924.md) 合计
**两个已见 DEV 来源候选的独立执行回执**，不是两个已认证独立 lineage。
研究增强 ZipCPU oracle 不是原生测试；两方测试类别、故障和参数不同，
不能合并成同一无条件成功率。尚缺审计签收的跨项目源码关系、实际核心
verified transition、Knowledge L3、Asset 生命周期的独立验证/回滚与
合法只读 M+/Mremove。`PILOT_TRANSFER` 未开始，未运行模型 API 或远端推送。
