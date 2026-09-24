# Revision5：v3 skid Asset 核心准入的 DEV 只读预检

状态：**`asset_eligible=false`；没有记录 authority、没有 TRAIN、没有 M+。** 这份预检只检查现行核心 gate 的实际行为，避免把 [v3 shadow core](research_r5_skid_core_v3_shadow_20260924.md) 的静态执行误认为 Asset 生命周期通过。

历史定位：本报告的代码/结果固定在本地 commit `8671474`；当前 HEAD 的 lifecycle 已另建 [v3 fail-closed 修正](research_r5_asset_authority_v3_fail_closed_20260924.md)，旧脚本在新语义下预期返回非零，不得拿新代码覆盖旧审计结论。

## 方法和原始输入

`research_r5_asset_authority_preflight.py` SHA256 `9ff25f54bd2bb9a1a4ad91d9fd1a859388e2ab07b42c25e6ee7ffeecb8aeaa89`；受检 `assets/lifecycle.py` SHA256 `b6343f286168d4febad37966bec3ddd88069b5d7ca3b7aa00be996cbe3b71543`，严格 ledger `assets/authority.py` SHA256 `c8fe958c4bd9fe39e46d2f5235b2ec25bc6a725046b27a544bc84a23be925248`。脚本在内存 SQLite 注册一个 researcher-assisted **DEV draft** Asset，用既有 register、broadcast、ZipCPU staged fault source 建立三个 source-bound copy，只调用纯 `evaluate_asset_authority`；不调用 `record_asset_authority`，不向任何持久数据库写入资格或权威回执。

输入 SHA256：register `000b18ba283dd3ad7ff6d9b5a2165df152441b662a43b4a34fd1ef8f88264e25`、broadcast `00b6aff964ab877e6f02cda66fcc0e353104c75ac5e9c9d109ed70cf332084c4`、ZipCPU `ca72b46e9dd7744f90d302811603140fc688418dbdde5d811dee5c502ea33fa3`。三者都是已观察的 DEV 故障，不是 TRAIN 任务。运行命令：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_asset_authority_preflight \
  --register-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-skid-payload-20260924/fault/stage/rtl/axis_register.v \
  --broadcast-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-broadcast-payload-20260924-r3/fault/stage/rtl/axis_broadcast.v \
  --zipcpu-fault /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-augmented-20260924-r1/fault/backpressure/stage/rtl/skidbuffer.v
```

## 现行 gate 观察

| Gate | 结果 | 能支持的判断 |
|---|---|---|
| `schema_valid`、`static_valid` | true / true | 三个 DEV 副本可构造、可静态执行；功能正确性未因此授予 |
| `independent_verifier`、`regression_zero`、`rollback_verified` | false / false / false | 无合法独立训练验证、保持义务和回滚回执 |
| `compatibility_verified` | false | 现行 `_binding_is_compatible` 仅列 alpha/guard，不识别 skid v3 source replay；v3 不能从 shadow 静态通过跳到 lifecycle 通过 |
| `cross_lineage_verified` | **true，但不能采用** | 当前纯 helper 从 `bound_design` 名称计数；只传同 owner 的 register+broadcast 两个设计时此项仍 true。它没有核验 [来源预检](research_r5_skid_lineage_precheck_20260924.md) 的实际分组，更不是 L3 双独立 lineage 证据 |

RAM Asset 保持 `draft`，总体 `asset_eligible=false`，脚本诊断 `valid=true` 只表示准确复现当前 gate 缺口。`cross_lineage_verified=true` 是**已复核的代理指标误计**，绝不记为研究资格成功。严格 authority ledger 对不带 wrapper 的 binding evidence 默认 `split=training`；因此不得把这些 DEV payload 直接交给记录 API 来“试一试”，否则角色元数据会失真。本预检没有调用该 API。

## 进入 R5-4 前需修复

1. 为 v3 source-bound Asset 建立可重放的 lifecycle 兼容性验证，不能仅把 contract 名加入允许列表；必须核对注册内容摘要、绑定模板、目标源码重绑和 profile。
2. 将 Asset 的跨 lineage gate 从 `bound_design` 数量改为经过独立来源审计、与 source/evidence row 绑定的 lineage ID；同 owner 的 register/broadcast 必须只计一个，未知关系不能算通过。严格 ledger 的记录与冷验证都要使用同一语义，不能只改纯 helper。
3. 显式校验 TRAIN 角色与 split，拒绝 QUALIFICATION/DEV 直接成为训练支持；随后才收集真实 baseline→action→oracle/preservation、rollback 及非目标回归。当前 ZipCPU 原生 formal payload 仍 `MISSED`，其 research-augmented DEV oracle 不自动升级为 TRAIN。
4. 任何 gate 语义修复都创建新的软件 epoch，并复跑旧 alpha/guard、v1/v2/v3 shadow 与新负控；冻结后才进入正式三态，不以当前 `cross_lineage_verified` 输出构建 M+。
