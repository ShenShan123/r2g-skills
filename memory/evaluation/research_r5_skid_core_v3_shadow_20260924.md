# Revision5：skid v3 source-only Asset 的 shadow core 接线

状态：**DEV shadow-core conformance 59/59；Asset 为 `draft`，Knowledge 行数为 0，实际 router/selector 在空 Memory 上为 `NO_SKILL`。不是 TRAIN、合法 M+、真实目标迁移或 ΔMemory。** 本次仅把已审查的 [DEV v3 binder](research_r5_skid_binding_v3_dev_20260924.md) 接到现有核心路径；v1/v2 locator 和既有原始 oracle 工件未改。

## 版本与接线

- 新 shadow action domain `rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V3` / profile `rtl.skid.temp_payload.v3.dev`。`RTL_ACTION_VERSION` 从 `rtl-actions-v0.4` 变为 `rtl-actions-v0.5`，`COMPATIBILITY_VERSION` 从 `rtl-compatibility-v4` 变为 `rtl-compatibility-v5`；故任何未来三态对比都须统一使用这个新软件 epoch，不能把旧软件 M− 与新软件 M+ 对比。旧 v1/v2 domain 仍可执行，回归各 19/19。
- action adapter `memory/tehm/rtl/skid_payload_action_v3.py` SHA256 `1f68262470e5389fde0091ae8ccf0a92c7d39ebeff3de91eeccb1399f0c9b644`。它从 buggy RTL 和精确公开配置重建唯一绑定，payload 字段必须完全匹配，再执行唯一 RHS 改动；目标 clean、附加 gold 字段、陈旧源码均拒绝。
- Asset 模板 `memory/tehm/assets/skid_binding_v3.py` SHA256 `674a35946cdebd61c99dfec16e8eca723f3c62c6dd88fbd4c45ac91561413561`，冻结 locator/proof scope/spec digest。注册的训练侧 payload 仍只是 researcher-assisted DEV fixture；目标侧 payload 从目标 buggy source 独立重新推导。core 的 `bind_rtl_asset_to_source`、`verify_source_copy`、source-runtime binding、候选 replay 及 selector 校验均纳入 v3 contract/domain，不允许没有 source proof 的 action 走旧路径。此接线不创建 Knowledge authority，也不自动 promotion。
- RAM-only conformance `memory/tehm/evaluation/research_r5_skid_core_v3_checks.py` SHA256 `e8783eebe3e55c23e6332230dbc11b18ca0dcc617e50e4a3913046373b96124f`。输入是已有 DEV staged register、broadcast、ZipCPU fault/clean；三个源码的核心 action 都逐字节生成相应 clean。`validate_rtl_rewrite_asset` 只给 `SHADOW_STATIC_PASS`，无独立 verifier/功能 verdict。结果含模板缺失/篡改、配置错误、私有答案字段、source/registry digest 篡改、陈旧源码、健康源码拒绝与无 Knowledge 的不可选检查，共 59/59。另保留 v3 locator 48/48、v1/v2 core 各 19/19。

复核命令：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_skid_core_v3_checks \
  --register-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/fault/stage/rtl/axis_register.v \
  --register-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/clean/stage/rtl/axis_register.v \
  --broadcast-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/fault/stage/rtl/axis_broadcast.v \
  --broadcast-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/clean/stage/rtl/axis_broadcast.v \
  --zipcpu-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-augmented-20260924-r1/fault/backpressure/stage/rtl/skidbuffer.v \
  --zipcpu-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-augmented-20260924-r1/clean/backpressure/stage/rtl/skidbuffer.v
```

## 尚未跨越的门槛

所有三个输入故障及其 clean 均已在 DEV 中观察，不能当作未见 target；ZipCPU 仍只是 [research-augmented DEV oracle](research_r5_zipcpu_augmented_payload_dev_20260924.md) 的限定检出，上游 native formal `MISSED`。本次测试没有正式 TRAIN baseline→action→oracle/preservation 回执、独立来源关系审计、L3 双 lineage Knowledge、Asset lifecycle 验证/回滚、实际 Memory selection 或目标任务预登记。因此保持 R5-4 的 **M+ NO-GO**；不得从静态源码相等或空库 NO_SKILL 推导 Memory 收益。
