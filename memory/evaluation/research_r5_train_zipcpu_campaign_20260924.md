# Revision5 R5-4：获授权 campaign 与首个 TRAIN 重跑

状态：**ZipCPU 研究者辅助 TRAIN 重跑完成；尚未形成核心准入的 M+。**
用户授权的新 evaluator-only 工作根为
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot`，与上游 checkout 和已有
`_qualification` 分开。角色、可见性和剩余 gate 见该根的 `README.md`。

## 预登记与新执行

将已观察的 `ZipCPU/wb2axip/rtl/skidbuffer.v` DEV payload 故障**另行登记**为
`TRAIN_REUSED_DEV_RESEARCHER_ASSISTED`，明确 `unseen_transfer=false`。
固定源 HEAD `2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b`，上游 RTL
SHA-256 `ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389`，
研究增强私有 testbench SHA-256
`ed5e2162902b128114e37bc29e8c540218809601b0ab6b150de1b69af6f7f745`。
固定 `DW=8, OPT_OUTREG=1, OPT_LOWPOWER=0, OPT_PASSTHROUGH=0, OPT_INITIAL=1`，
五拍确定性事务；直通为保持义务、背压为目标义务。原生 `skidbuffer.sby`
对此 payload fault 仍为 `MISSED`，不能称此增强 oracle 为原生结果。

`prepare` 在仿真前写入不可覆盖的
`training/zipcpu-skid-payload-train-r1/preregistration.json`，SHA-256
`4c8a33b7d0fba081db1cf97e10328e1e7f0bea9e533303d236e9797c603e3f60`。
随后 source-only v3 binder 只以 staged buggy RTL、公开参数和 draft 模板
产生唯一候选；私有 clean 源和故障坐标只在 evaluator 使用。
`agent-inputs/` 仅有 `skidbuffer.v`；每个 arm 单独 stage/build，记录实际
Icarus/VVP 命令、源码、私有 TB、日志、退出码和编译镜像。

| Arm | 直通保持 | 背压目标 |
|---|---|---|
| clean | PASS | PASS |
| fault | PASS | FAIL |
| source-only candidate | PASS | PASS |

独立进程冷复核 `valid=true, errors=[]`，回执 digest
`sha256:bc312de489ad101d93d630ea890a16069667369c68248019f4626e6e645564d6`；
原始 `receipt.json` 文件 SHA-256
`9c9eddc054aef7ab470f1868675f3241e2879af174ed894acd9ad39f41781658`。
本轮 evaluator 脚本为
[`research_r5_train_zipcpu.py`](../tehm/evaluation/research_r5_train_zipcpu.py)，
SHA-256 `42646ecebececa23424310cfd6ed64acfaa056e8439ba2319dd1d3272b202032`。
既有 v3 binder 48/48 对抗检查、augmented verdict 11/11 反例检查重过；
上游 ZipCPU checkout 运行后仍 clean。

复核命令：

```sh
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_train_zipcpu verify \
  --work /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/zipcpu-skid-payload-train-r1
```

## 严格边界与下一 gate

这是一个已见 DEV 故障的**新执行**，不是独立样本、未见目标、TEHM router
实际 SELECT、在线自主演化或 Memory 净收益。候选与 clean 的逐字节相等只在
evaluator 审计，不能进入未来 target binder。当前仍缺另一候选来源的正式
TRAIN baseline→action→oracle/保持回执、足以签收的独立 lineage 关系、
核心 verified transition/Knowledge L3 证据与 Asset 生命周期验证/回滚。
全传递动态依赖及跨设备封存也未完备。`M+` 和 `Mremove` 均未构造，
`PILOT_TRANSFER` 未启动，且不能通过手填 validated/promoted 或降低 gate
绕过这些缺口。
