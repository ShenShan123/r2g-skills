# Revision5：ZipCPU skidbuffer 独立来源 DEV 资格探针

状态：**原生形式化 payload 故障 `MISSED`；单独 live-control `DETECTED`；不是 TRAIN、Memory 或独立迁移结果。** 本次针对第一波候选中同类结构缺少可用原生 oracle 的具体缺口，从用户第二档名单有界新增 `ZipCPU/wb2axip`，放在 `/data1/zhangdy/RTL/RTL_testbench/ZipCPU/wb2axip`。上游 checkout SHA `2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b`，origin `https://github.com/ZipCPU/wb2axip.git`，运行前后 clean，未写入实验产物。初步来源组标为 `ZipCPU_pending_relation_audit`，不同 owner 不自动证明独立。`rtl/skidbuffer.v` 源文件头标 Apache-2.0；此浅克隆未发现单独 license 文件，不扩大为仓库级法律结论。

## 预登记的两代 DEV 输入

- Native formal 入口是上游 `bench/formal/skidbuffer.sby`，SHA256 `067e36aa8fcd26a8743216f5f36cb87c532624912dc6812dbbf7f9c76d3d6a58`；RTL SHA256 `ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389`。工具为本机 OSS CAD Suite 的 SBY `v0.51-6-gff98e51`、Yosys `0.51+101`，可执行文件的绝对路径、SHA256 和大小锁在原始 preregistration 中。实际 smtbmc 日志显示使用 yices；完整动态依赖锁尚未建立。
- Payload 探针：`prfo`（`OPT_OUTREG=1, OPT_LOWPOWER=0, DW=8`）为目标，`prfc` 为组合输出保持对照。DEV evaluator 只在隔离副本把第 213 行 `o_data <= r_data;` 改成 `o_data <= i_data;`；上游原文件未变。`preregistration.json` digest `sha256:e75e4c0d636f81a8742cff19072d22ef5424ee452aa12c50e4d7d2126f02f578`。原始四臂在 `/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-skid-formal-20260924-r1`，每臂独立 stage/proof，编译器复制源码与预登记 clean/fault 字节一致。
- Live-control 是**另一类故障**：只在隔离副本把注册输出 `assign o_valid = ro_valid;` 改成 `assign o_valid = 1'b0;`，运行 `prfo`。它只检验原生断言是否活跃，不能替 payload 探针补判定力。其 preregistration digest `sha256:4c75cda153bf0ad6539f9286a1abfbcad6c309c67418806a2fde87031aa54ce4`；原始两臂在 `/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-skid-live-control-20260924-r1`。

## 原始结果与冷审计

| 范围 | clean | fault/control | 可得结论 |
|---|---|---|---|
| Payload `prfc` | PASS, rc 0 | PASS, rc 0 | 保持对照通过 |
| Payload `prfo` | PASS, rc 0 | PASS, rc 0 | 对指定 payload 来源故障 `MISSED` |
| Valid-signal live-control `prfo` | PASS, rc 0 | FAIL, rc 2；第 371 行断言、VCD 反例 | 同一原生形式化入口对另一类活性/有效性故障能报 FAIL |

第一代 producer 只接受单词 `PASS`/`FAIL` 和 rc 1，因此虽然保留了全部原始工件，自己的初始解析是 `UNDETERMINED`，没有将其伪装成检出。后续 v2 只读审计正确识别 `PASS 0 0`，把 payload 原始四臂定为 `MISSED`；live-control 暴露了 SBY 的反例退出码为 2。最终 v3 只读审计同时要求状态、退出码、断言日志、实际编译源码及失败 VCD 相互一致，7/7 反例解析检查通过，结果 `valid=true, qualification=MISSED, live_control_detected=true`，完整摘要 digest `sha256:99580e101715cf6d548cc29333fdbff6344ee97c5a43d4a757aa6260445fc624`。不同版本的 producer/auditor 和两代原始目录均保留，不覆盖旧工件。关键代码 SHA256：原始 payload producer `efe414c6093cec69fd2a0e3ed2ffe4736f2cca0871855807a8587d01898ee888`，live-control producer `f8f4c751e485a293fdef840d898b9a54fafc1a0b3f6641738507bb7d0c347bf2`，最终审计 `8b0e51f9fb277995c77af46af5963ce7f52c5f6cfdfeb9efda8a8b926937a4d5`。

复核入口：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_zipcpu_sby_audit_v3 \
  --payload-work /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-skid-formal-20260924-r1 \
  --control-work /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-skid-live-control-20260924-r1
```

当前冻结 v2 skid binder 对该 fault 的真实公开参数 `{DW:8, OPT_OUTREG:1, OPT_LOWPOWER:0}` 返回 `UNSUPPORTED: unsupported_or_extra_public_parameters`；这与 oracle 漏检是**两道独立 NO-GO 门槛**。不能把 ZipCPU 放入此 payload 机制的合格 TRAIN 或 PILOT_TRANSFER，也不能把 live-control 的 FAIL 挪作 payload FAIL。若后续需要此来源，先另建 DEV generation 设计独立的 transaction-level payload oracle，再验证 clean、fault 和保持义务；同时若拓展 binder，也必须新版本与新的答案防火墙测试。首批 `ultraembedded/riscv` 的两个 skid 线索仅有整核 SystemC/Verilator 原生入口，本机目前未发现 SystemC，尚无这两个文件的限定 oracle 资格。
