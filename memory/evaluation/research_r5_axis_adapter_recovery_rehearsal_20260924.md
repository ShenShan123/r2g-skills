# Revision5 §19：`axis_adapter` 资格 case 异路径恢复演练

状态：**本次实际资格 case 的旧证据可在新路径冷审计，恢复出的源／测试在当前环境可重新执行；长期独立备份与完整依赖打包仍未证明。** 本回执不替代[原资格回执](research_r5_axis_adapter_native_qualification_20260924.md)，也不产生新的独立设计、缺陷或训练经验。

仅选择 `/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-q-axis-adapter-20260924-n8kAEk/` 这一实际使用的 case（约 1.3 MiB），未打包完整 corpus、其他 `_qualification`、上游 `.git` 或其它私有任务。原目录无 symlink；tar 清单均在单一 case 前缀内。archive 位于 `/tmp/tehm-r5-axis-adapter-recovery-20260924-bLKhEQ/evidence.tar`，SHA256 `84e67a03d69b59fe15e4c396274cdd59c486a5fbe0ba013a24dfa69aaecdf38d`。本机 `/data1/zhangdy` 为 `/dev/sdc1`，`/tmp` 为 `/dev/sda2`；这提供不同设备上的临时第二副本，但 `/tmp` 可被清理，不是长期备份服务。

解包到新的 `/tmp/.../restore/r5-q-axis-adapter-20260924-n8kAEk/` 后，逐文件比较原 case 无差异。[恢复专用审计器](../tehm/evaluation/research_r5_axis_adapter_recovery.py)不会沿原回执中的绝对工件路径读取原目录，而是将 14 个工件逐一映射到恢复根，核对 SHA256、大小、目录归属、历史 VVP/日志的原编译源身份、9 个 JUnit test ID、seed 和 verdict。输出 `valid=true`、旧 receipt digest `sha256:0de756e34ce05ebdd6abe3b3c8a4e0901a2201e9e366e69d3e392b4366dcd35c`、clean `PASS`、fault `FAIL`、sensitivity `DETECTED`、`new_execution=false`。[对抗检查](../tehm/evaluation/research_r5_axis_adapter_recovery_checks.py) 5/5 PASS：禁止读取原证据路径时仍通过，内存篡改 tar、恢复 RTL 或 JUnit 均拒绝。

随后**另外**从恢复出的预登记、两臂 RTL 和原生测试复制到空 `reexecution/`，在新 build root、同一参数与 seed、当前驻留依赖环境重新运行。它产生新的 receipt digest `sha256:c549009551d92f386886a92e7d9966e255f509457f8c90851ffa61589e0ed15e`，独立冷重放 `valid=true`；语义终态仍为 clean 9/9 PASS、fault 7/9 PASS/2 FAIL、`DETECTED`。新 receipt 不是原实验时间／执行身份的恢复，两个 digest 不合并成两次独立故障样本。原 case 与上游 checkout 均未修改。

旧证据恢复命令：

```sh
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_axis_adapter_recovery \
  --restored-case /tmp/tehm-r5-axis-adapter-recovery-20260924-bLKhEQ/restore/r5-q-axis-adapter-20260924-n8kAEk \
  --corpus /data1/zhangdy/RTL/RTL_testbench \
  --archive /tmp/tehm-r5-axis-adapter-recovery-20260924-bLKhEQ/evidence.tar \
  --archive-sha256 84e67a03d69b59fe15e4c396274cdd59c486a5fbe0ba013a24dfa69aaecdf38d
```

剩余 §19 缺口：没有持久化、可维护的第二故障域副本；archive 不含 cocotb/Python 传递依赖及 Icarus 二进制包，新的构建依赖当前机器。因而本演练只证明**这一个资格 case 的异路径旧证据复核和当前环境重跑**，不证明完整 R5 campaign 的可移植归档、更不证明 TRAIN/M+ 或论文结果已满足发布条件。
