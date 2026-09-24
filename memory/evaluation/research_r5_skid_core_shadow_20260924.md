# Revision5：skid action 的核心 shadow 接线自检

状态：**core-interface conformance only；R5-4 TRAIN / M+ 仍 NO-GO**。本轮把 R5-3 冻结的 source-only DEV locator 接入新的 `rtl.SKID_TEMP_PAYLOAD_RESTORE_SHADOW` action、Asset binding template、`source_selection` 重放及 structured-candidate 的缺失证明拒绝路径。新路径不授予 Knowledge、Asset promotion 或 production 权限，仍依赖冻结的 DEV 识别器。

固定接口：`rtl-actions-v0.3`、`rtl-compatibility-v3`、`verilog-parse-v0.3`，Asset binding contract `rtl_skid_payload_source_binding_shadow_v1`。Action payload 仅含 domain、profile、module、公开 `DATA_WIDTH=8/REG_TYPE=2`、目标 buggy source SHA256 和 source-only witness digest；执行前从当前源码重新推导并逐字段核对，不接受注入行号、gold path 或多余字段。Asset 绑定从注册模板与目标 buggy RTL 推导 bound copy，`verify_source_copy` 重新绑定并比较全部内容；缺少 source replay 的该 domain 不进入 structured-candidate 执行。

在内存 SQLite 中，用**DEV 故障作为接口 fixture** 注册一个 draft Asset；没有注册 Knowledge，没有状态提升。19 项自检全部通过：真正单点编辑、参数化源码前后可解析、Asset 内容重放、健康/无关源码拒绝、篡改 payload/template/bound copy 拒绝，以及无 Knowledge 时 runtime binding 拒绝。`validate_rtl_rewrite_asset` 只给 `SHADOW_STATIC_PASS`，`independent_verifier=false`、`oracle_verdict=null`。运行入口：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_skid_core_checks \
  --fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/fault/stage/rtl/axis_register.v \
  --clean /data1/zhangdy/RTL/RTL_testbench/alexforencich/verilog-axis/rtl/axis_register.v \
  --unrelated /data1/zhangdy/RTL/RTL_testbench/alexforencich/verilog-uart/rtl/uart_rx.v
```

本轮未调用新的 native oracle；该 action 生成的 DEV 候选与开发 clean 源字节相同，既有 R5-3 原生回执只证明旧 DEV 候选执行，不可重新命名为当前 Asset selection、TRAIN 或独立 transfer。没有实际 P5/P7 route/select、合法 causal path、M+ snapshot、Mremove 或新目标测试，不能据此填写 Repair@B / ΔMemory 表。

独立来源只读审查显示 `ultraembedded/riscv` 的两个 skid 字样分别对应打包 request buffer 与 fetch response mux；并非当前 temp→output 分支合约，且尚无这两个子模块的 qualified native oracle。它们不应因为名字相似被算作跨来源支持；这次开发者已读其 clean 源，后续若使用必须如实标注资格/DEV 可见性，不能充当真正未见 FINAL_TEST。第一批 corpus 的跨来源迁移仍未建立。

下一道门槛：在隔离的新 campaign root 冻结角色、visibility、runtime 与 TRAIN baseline→action→target/preservation oracle；按核心 L3/lineage/Asset authority gate 构建可审计 snapshot/delta，不够支持就保持 shadow/BLOCKED。随后独立预登记新目标，才允许运行 M−/M+/Mremove。不得重命名 DEV fixture 或手填 validated 状态。
