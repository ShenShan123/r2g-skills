# Revision5 RTL Pilot：QF-1 封存与 DEV 机制选择（2026-09-24）

状态：完成 R5-0 的本机 QF-1 索引、R5-1 的两种结果适配器初版，以及 R5-2 的一个固定 seed DEV 故障判定力探针；新版 binder 尚未实现。没有 TRAIN memory、真实 transfer、ΔMemory 或论文 final 结果。执行依据为 [`Revision5`](../docs/TEHM_R2G_Revision5_RTL测试判定力_受限绑定与独立迁移Pilot方案_2026-09-24.md)。

## QF-1（本机，不是方法结果）

当前索引：`/data1/zhangdy/RTL/RTL_testbench/_qualification/QF-1-r3/index.json`，digest `sha256:5b65bd958c64738e070436f86fad1e65bcd345261a05e1d0f6c7ba4acd32f30e`。早期 `QF-1/` 参数不完整，`QF-1-r2/` 把按键排序的 AES 摘要字典误当有序编译清单；二者保留历史身份但**不用于后续协议**。r3 索引从现有原始文件读取 10 个 URL/完整 SHA、源码与测试字节摘要、三项预登记负控、native 结果、实际 JUnit test ID 和 seed；其检验函数核对克隆 clean、remote、文件摘要与代码摘要。AES 有序 filelist 来自实际 compile argv。旧 `rtl_acceptance_completion_guard_binding_v1` 的 source-only 重放保留 249 条逐文件状态及原因，0 匹配；未将其解释为 binding recall。

| Scope | 已存 clean / 负控 | 限定判定力 | 历史缺口 |
|---|---|---|---|
| `axis_register`，DATA_WIDTH=8、REG_TYPE=2，及原 wrapper 其余参数 | 9/9 PASS → 9/9 FAIL | 输出 payload 取反：DETECTED | wrapper rc 未独立保存；clean/probe seed 分别为 1790252446/1790252701；其他故障未测 |
| AES 原生 20-case scope | 20/20 PASS → 20/20 native ERROR | result read word 取反：DETECTED | probe 的 vvp rc 未独立保存；参数、个别 test ID/软件依赖锁需补足 |
| UART RX，DATA_WIDTH=8 | 2/2 PASS → 2/2 PASS | 输出 payload 取反：MISSED，拒绝该故障类别的 oracle 准入 | wrapper rc 未独立保存；clean/probe seed 分别为 1790251628/1790252184 |

适配器 `r5-cocotb-junit-verdict-v1` 绑定预声明 test ID、JUnit 与 wrapper 终态；`r5-secworks-native-summary-verdict-v1` 同时解析原生成功/失败汇总、结束标记、错误计数和 stderr。正式新方法运行默认要求记录进程退出码；历史 QF 回放用显式的 `*_exit_recorded=false` 限定模式，不补造退出码。`research_pilot.py check-r5-verdicts` 的 15 个内存反例全部通过；缺报、零测试、终态缺失、日志/JUnit 矛盾不得 PASS，AES 式 native FAIL 即使 vvp rc=0 也输出 FAIL。原始数据未重新仿真，也未写入上游 clone。

复核入口：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 memory/scripts/research_pilot.py check-r5-verdicts
PYTHONDONTWRITEBYTECODE=1 python3 memory/scripts/research_pilot.py verify-r5-qf1 --index /data1/zhangdy/RTL/RTL_testbench/_qualification/QF-1-r3
```

QF-1 是索引而非可携带归档。Python 依赖的完整传递锁、所有 cocotb 实际编译命令/进程 rc、独立备份与异路径完整恢复仍未证明；若这些字段是正式 task 的必需项，必须在新 generation 中实测/冻结，不能从这份历史索引补写。资格负控的预登记、mutant 和详细私有日志不允许挂进 agent/binder 工作区。

## DEV 机制卡（已做限定故障探针，binder 未冻结）

- 公开义务：在 AXI-Stream skid buffer 的 `REG_TYPE=2` 路径中，遇到 backpressure 时先暂存的 beat，之后送出时 `tdata` 应保持该暂存 beat 的 payload；这是 payload 来源选择，不等于一般 ready/valid 时序正确性。
- 开发起点：锁定的 `alexforencich/verilog-axis` `axis_register.v`。其 `store_axis_input_to_temp` 路径把输入写入 `temp_m_axis_tdata_reg`，`store_axis_temp_to_output` 路径把暂存值送入 `m_axis_tdata_reg`；现有 native test 显式比较收发 `tdata`。
- 开发期实测：在现有 `_qualification/r5-dev-skid-payload-20260924/` 预登记把第 173 行的暂存 payload RHS 错换为当前输入。clean 与 fault 各用全新隔离 build root、相同 `RANDOM_SEED=20260924`、同一 `test_axis_register[8-2]`；clean wrapper rc=0、JUnit 9/9 PASS，fault rc=1、JUnit 7/9 PASS、2/9 FAIL，失败 ID 为 `run_test_002` 与 `run_stress_test_002`，日志中的收发 payload assertion 明确失败。VVP 包含 staged 源路径，上游 clone 仍 clean。增强后回执 `receipt-r2.json` digest `sha256:4fefc3a274d55cc0009da75ceada529956bc1b3d95ae514ac2a458c1cb93c9e3`，冷复核有效。这表明该 seed/故障/参数点的 native suite 能检出错误；未单独记录 RTL 分支覆盖波形，也不推出时序故障判定力或 TEHM 修复。
- 候选受限动作：仅当源码中找到唯一的 temp-register capture、唯一的 temp-to-output 条件分支、唯一的输出 payload 赋值且 RHS 不再取暂存值时，建议修复 RHS；对正确源、多个位置、缺失结构、参数不支持均拒绝。模板、定位条件和 action 必须从 DEV 版本化，冻结后 binder 只读目标 buggy RTL 和公开参数，不能读测试/参考源码/负控坐标。
- 相似代码出现在同一 `verilog-axis` owner 的多个模块；这只支持寻找有界的同源 DEV/候选目标，不证明它们独立，亦不与 AES 强行合并。跨来源合法目标、TRAIN 证据与健康 non-target 仍待建立。

R5 后续顺序：先在 DEV 中实现并测试受限 source-only binder/action，同时确认独立健康非目标与有界目标池；之后单独登记 TRAIN 与 PILOT_TRANSFER，按同软件条件运行 M−/M+/Mremove。未通过任何 gate 时保留 NO_MATCH/UNKNOWN，不从最终目标反向调协议。
