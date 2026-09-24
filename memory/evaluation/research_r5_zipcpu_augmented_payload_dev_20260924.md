# Revision5：ZipCPU skidbuffer payload 的 DEV 补充 oracle

状态：**research-augmented DEV oracle 在预登记范围内 `DETECTED`；上游 native formal 仍为 `MISSED`。非 TRAIN / M+ / PILOT_TRANSFER。** 前一代 [ZipCPU 原生探针](research_r5_zipcpu_skid_dev_20260924.md) 已证明 `skidbuffer.sby` 的注册输出形式化任务没有检出“缓冲数据改读当前输入”的故障，但独立 valid-signal live-control 可以触发断言。因 R5 需要跨来源 payload 判定力，本代单独增加事务级 scoreboard；它是本研究编写的 evaluator-private 测试，不冒充原仓库 native test，也不回写旧结论。

## 固定 scope 和隔离

- 上游 `ZipCPU/wb2axip` SHA `2e8d3bc2d26ddc33d1881022a2a2b9d3f0c16b9b`，`rtl/skidbuffer.v` SHA256 `ed1fda9192563fd7fd7033b564e8cf47ad6cb63ba4f578ed2c302037d47a5389`；checkout 运行前后 clean。DEV fault 仍是第 213 行 `o_data <= r_data;` → `o_data <= i_data;`，没有扩增突变矩阵。
- 私有 testbench：`/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-zipcpu-augmented-oracle-input-v1/tb_skidbuffer_payload.sv`，SHA256 `ed5e2162902b128114e37bc29e8c540218809601b0ab6b150de1b69af6f7f745`。它不在上游 checkout，也不属于 agent-visible 输入。固定 `DW=8, OPT_OUTREG=1, OPT_LOWPOWER=0, OPT_PASSTHROUGH=0`；五个不同 payload，无随机种子。`direct` 全程 ready 是保持对照；`backpressure` 在第二拍进入缓冲后暂停输出，再释放并比较接受顺序与值。只检验这个 schedule 的数据保持/顺序，不证明所有 AXI 时序、复位、低功耗或参数组合。
- Producer `memory/tehm/evaluation/research_r5_zipcpu_augmented_probe.py` SHA256 `baf969896296cb1543bb9ff35b2751d9c3d3532873961fa13ecc93bb6ef21824`；适配器对抗检查 SHA256 `06dacf5fb0d75ff1e073404b1ea08292d0742f9ba8a194edc2e671d282bd279f`。编译使用独立 `/usr/bin/iverilog -g2012`，运行 `/usr/bin/vvp`；工具二进制 hash/版本输出 hash、producer 代码 hash、实际命令、顶层、参数、每臂 timeout 与目标/保持义务已在 preregistration 冻结。完整传递动态依赖尚未锁定。
- 原始 DEV 工作根为 `/data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-augmented-20260924-r1`，preregistration digest `sha256:e929a9b583a813cb277a942483477f352f4821cec62a925cfb52d33d9edbca79`。四臂各有独立 stage、编译产物、原始编译/运行日志与退出码；冷审计核对 staged source/TB、命令、上游与工具锁，以及 `.vvp` 中实际 staged RTL 路径。

## 原始结果

| Arm | 编译 | 仿真/scoreboard | 判定 |
|---|---:|---|---|
| clean/direct | rc 0 | 五拍有序比较，rc 0 | PASS |
| clean/backpressure | rc 0 | 五拍有序比较，rc 0 | PASS |
| fault/direct | rc 0 | 五拍有序比较，rc 0 | PASS；保持对照 |
| fault/backpressure | rc 0 | 第 4 个调度周期在第 2 个输出 beat 比较得到 `c3`，期望 `b2`；`$fatal`，rc 1 | FAIL；目标检出 |

冷审计 `valid=true, qualification=DETECTED`，完整摘要 digest `sha256:1824a6909091b9b2454ec28761c81e30319873a31ed0157f3b5fda7d28827cdd`。11/11 项适配器反例检查通过：零测试、缺终态、功能 FAIL/rc 0、PASS/rc 1、矛盾汇总、错场景、编译错误、超时及缺 `$fatal` 均不会被误记为 PASS。旧 native formal 的 `MISSED` 和 live-control 的不同义务 FAIL 原样保留；本轮不是“原生测试由漏检变成检出”。

复核入口：

```bash
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_zipcpu_augmented_probe verify \
  --work /data1/zhangdy/RTL/RTL_testbench/_qualification/r5-dev-zipcpu-augmented-20260924-r1
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_zipcpu_augmented_checks
```

## 准入结论

在此**新 DEV oracle generation** 中，ZipCPU 为该具体 payload 来源故障提供了一个受限的 research-augmented 判定范围；尚无 native payload 检出、完整 test-closure/动态依赖锁、非目标全回归或 TRAIN 经验。冻结 v2 binder 对 ZipCPU 公开参数仍返回 `UNSUPPORTED`；不能因 oracle 改进就自动宣称可绑定或 Memory transfer。若后续纳入 TRAIN，必须另行登记角色、独立执行 baseline→action→augmented target 与保持义务、明确 researcher-assisted 来源，再通过核心 Knowledge/Asset gate；它不能再用作未见最终目标。私有 testbench 与原始工件是当前唯一 DEV evidence 副本，发布或清理前仍需满足 Revision5 §19.4 的独立备份与隔离恢复要求。
