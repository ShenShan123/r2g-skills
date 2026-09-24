# Revision5：双结构 skid payload 的核心 shadow 接线

状态：**R5-3 接线检查完成；R5-4 TRAIN / M+ 仍 NO-GO。** 本代将已冻结的 DEV v2 source-only locator 接入 RTL action、Asset 绑定与 selector 的显式目标公开参数通道；保留旧 v1 domain/template。它不创建合法 TRAIN、validated Knowledge 或可运行的 M+，也没有构造未见 PILOT_TRANSFER 任务。

## 固定接口与代码锁

- 新 action domain `rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW_V2`，profile `rtl.skid.temp_payload.v2.dev`；`rtl-actions-v0.4`、`rtl-compatibility-v4`、`asset-selector-v0.3`。v2 locator 本身的 SHA256 为 `0408f50bad03e0eaa24b27b457d5dbc176b486fb867cb3a3ad9ea1b27d7a9823`。
- 新 action adapter `memory/tehm/rtl/skid_payload_action_v2.py` SHA256 `d66f02f42f9fe21773f7d1dd17faaa84760ded0d1ed9dfb77939ffd451fa1fb0`；Asset 模板 `memory/tehm/assets/skid_binding_v2.py` SHA256 `eb0ac1c3ab1b999525735151f3c404d11efc5cb7b0c1c535771e34f3406694d6`；RAM 检查 `memory/tehm/evaluation/research_r5_skid_core_v2_checks.py` SHA256 `ac971745a01fcc0f7d9525e976374e829239a4f091aa63beabddfb18de5d67e2`。完整代码代由本次本地 commit 固定；依赖 v1 locator 和 Verilog parser 的 SHA 见 [DEV v2 报告](research_r5_skid_binding_v2_dev_20260924.md)。
- `bind_rtl_asset_to_source` 增加可选 `public_context`；v2 contract 必须显式提供，且 locator 只接受已声明的 register 或 broadcast 参数。selector 的 `rtl_public_context` 只和显式 RTL source/design_id 成对使用；replay 以绑定证据里的 context 和当前源码重新推导，而不是复用训练源码坐标。v2 action payload 字段精确限制为 domain/profile/module/context/source hash/witness digest；执行前重新绑定。
- 核心 source-contract 列表、candidate 缺失证明的拒绝路径均识别新 v2 domain；未修改 Knowledge authority 或 Asset lifecycle。`with_skid_payload_binding_v2` 只是 proposal 装配，调用者必须另外证明 TRAIN 来源与原生 oracle。

## RAM-only 验证

用开发期 `axis_register` 故障源码做 proposal fixture，在空 RAM 数据库注册后状态仅为 `draft`；另取开发期 `axis_broadcast` 故障源码及公开 `DATA_WIDTH=8, M_COUNT=2` 生成 bound copy。19/19 项通过：注册模板内容重放、跨形态 context 重绑定、核心 action 单次实际编辑、两种 DEV 源码修复结果分别与已通过原生测试的 clean staged RTL 逐字节相同；缺/错参数、额外 gold 字段、篡改 context、过期源码、健康源码和缺少 source replay 均拒绝。静态验证只给出 `SHADOW_STATIC_PASS`，`independent_verifier=false`、`oracle_verdict=null`，Knowledge 行数为 0。实际 `route_memory` / `select_knowledge_grounded_assets` 在空 Memory 上均返回 `NO_SKILL`，原因 `no_validated_mechanism_knowledge`；没有伪造 SELECT 或 candidate。

复核：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_skid_core_v2_checks \
  --register-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/clean/stage/rtl/axis_register.v \
  --register-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/fault/stage/rtl/axis_register.v \
  --broadcast-clean /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/clean/stage/rtl/axis_broadcast.v \
  --broadcast-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/fault/stage/rtl/axis_broadcast.v
```

旧 v1 shadow core 19/19、新 v2 locator 对抗检查 37/37 在本次改动后仍通过。此结果只是软件接口与开发样例一致性；没有新的原生候选运行、合法训练回执、跨来源支持或三态归因。

## 下一门槛

按 Revision5 §9、§14、§16–17，先在独立 campaign 冻结 TRAIN role、source lineage、故障与保持义务、可见性及原生 oracle，再从实际 baseline→action→验证链构造 Knowledge/Asset。当前两个 v2 正例均属于已观察的 `alexforencich` DEV，不能作为未见 target，也不能单靠它们满足默认跨来源准入。缺乏合格 TRAIN parent 时保持 draft/NO_SKILL；不得手填 validated/candidate 来演示 SELECT。
