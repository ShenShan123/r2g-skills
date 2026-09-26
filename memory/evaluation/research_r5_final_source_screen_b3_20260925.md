# R5 B3：两个定向来源、原生激励空覆盖与工具版本阻断

控制设计为 Revision5 §7.4、§14、§16/R5-8、§19。继 [F1 描述性最终实验](research_r5_f1_gaxi_final_20260925.md) 后，本批仅定向预登记两个新来源候选，不修改 frozen gen5 方法或 Memory，不根据 binder 结果选入。**新增合格 repair task、FINAL_TEST task 和认证独立来源均为 0**；F1 四视图各 0/1 的读数不变。

## 两候选真实状态

| 固定来源 / scope | 本地结果 | 当前门禁 |
|---|---|---|
| [dozecat/axi_lib](https://github.com/dozecat/axi_lib) `f15227300d72e79bc537817c89340a084d3b9072`；`rtl/skid_buffer.v`，native `axil_interconnect` 集成测试 | 预登记的一次构建在仿真前终止：Verilator 5.035 不识别上游 `-Wno-PROCASSINIT`，make rc=2，无模拟二进制或有效波形 | UNKNOWN / 工具兼容性待解决，不是硬件功能 FAIL。未删警告选项、换测试或重试 |
| [libfpga/libfpga](https://github.com/libfpga/libfpga) `a4ef4a3fa4ac1de6aa485baf3efc56f14a6df704`；`lfpga_skid_buffer`，native `tb_skid_buffer` | 原版单配置 TB PASS；波形显示 1507 入、1506 出，输出阻塞/skid capture/skid drain 均为 0 | 本 scope 对所需背压后 payload 义务 UNQUALIFIED；没有进行故障注入，不能称为已测 MISSED 或修复失败 |

两仓库分别位于 `/data1/zhangdy/RTL/RTL_testbench/dozecat/axi_lib` 与 `/data1/zhangdy/RTL/RTL_testbench/libfpga/libfpga`，前后工作树均 clean。两者是公开 RTL 库组件/集成候选，不由 README 宣传认证成工业生产设计或独立作者样本。

本批有两个 baseline attempts，其中 dozecat 未启动仿真，LibFPGA 实际运行一个原生测试；后续仅 LibFPGA 又执行一次恢复验证。恢复不增加来源、配置、任务或方法样本。无 route/select/binder/primitive/模型调用，无 Memory 写入。

## 预登记与来源证据

当前已获取范围中的 APEX 旧记录显示 standalone skid TB 缺失，YChud 原目标判定门禁未通过；这些旧结果不重新包装为未见任务，也不表示已经穷尽整个 corpus。按 §7.4 的具体缺口，查阅公开文档后、读取这两项 RTL/TB 之前固定 B3 的两候选列表，不追加替补。定向发现依据为 dozecat 的公开 native 仿真说明及 [LibFPGA 库页面](https://libfpga.com/lib) 的 skid/self-checking test 描述；它们只用于发现，不当作本机 PASS。

工作根：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/paper/final-source-screen-b3/`。`preregister.md`、`acquisition-r1/pre_execution_lock.json` 固定候选和首次获取身份。GitHub public metadata、完整 HEAD、origin、tree、clone stdout/stderr、dirty 状态均保存。浅克隆历史限制明确；不声称完成历史作者/复制关系审查。

`snapshot.py` 对固定 Git 对象逐字节核对并封存 dozecat 42 文件、LibFPGA 77 文件；RTL、原生测试、runner、README、license 一并保存。每个 DUT 对照 10 个明确历史叶节点（B2 原八参考，加已观察 F1 GAXI 与 APEX），20 对比较均无精确字节或仅去除注释/空白后的相同；两候选彼此也不同。这只是有界重复诊断，不是语义去重、穷尽暴露检查、完整 elaboration 或独立 lineage 认证。

冻结方法仍为 `2ce921a599c406ab63331561a182d6f4f1bef8cb`，工作树再次确认 clean；未改 F1、TRAIN、原生源码或任何既有 gate 以便本批通过。

## LibFPGA：native PASS 不等于实际测到 skid

执行前固定 `libfpga-native-preregister.md` 和 `native-plan.json`：WIDTH=8、10 ns、原 TB LFSR 初值 `0x51CD`、3000 次循环，Icarus 11.0 及 37 个工具/后端文件 pin，每条 compile/run 命令 60 秒，零重试。只运行原 `scripts/verify.sh` 中该项对应的两文件编译/模拟操作，显式指定 native top；DUT/TB 没有编辑，也没有运行全库测试。

隔离执行中原始 corpus、主仓库和网络不可见，源码只读。原始 stdout 为单一 `TB PASS: skid_buffer`；独立波形 replay 核对 native 计数器、输入接受、输出顺序、时钟、复位与内部 skid 活动：

- 1507 次输入握手、1506 次输出握手；输出序列与已接受输入前缀一致。
- native `errors=0`、循环次数 3000；结束时间 30,026 ns。
- 0 个 `m_valid && !m_ready` 周期，0 个 skid capture，0 个 skid drain；`skid_valid` 自复位后从未置位。
- 末尾还有一笔 `0xE2` 未排空，TB 本就没有最终 drain；不能把 PASS 写成全部输入已交付或全功能证明。

源码层面的解释与观测一致：LFSR 更新后当前 bit 1 等于上一次 bit 0，而 native TB 用相邻两位决定 ready 和新 valid。3000 次离线递推核对该相关性；不据此外推其他 seed/刺激。零 stall 下的稳定性检查只是空真，`stall_stability_nonvacuous=false`，不能列为背压覆盖通过。

原 `audit.py` 在要求正 skid activity 的断言处拒绝，保存为 `native-audit-r1-failure.json`。追加 `audit_v2.py` 仅将“clean 运行证据有效”与“skid 资格不成立”分开输出，保留原 auditor/失败摘要，未修改原生运行或准入门槛：`valid=true, verdict=PASS, scope_status=UNQUALIFIED_FOR_BACKPRESSURE_PAYLOAD, fault_qualified=false, final_test_admitted=false`。12 项离线检查包括矛盾/缺失/重复 PASS、rc=0 功能 FAIL、超时、缺波形、伪 plan、内部零 skid 状态与资格空覆盖，全部通过；不是新增实验样本。

## dozecat：原测试保持不变，记录实际工具阻断

指定 native top 的源码显式启用 `S_SKID_EN=2'b01`、`M_SKID_EN=4'b1010`，S_NUM=2、M_NUM=4。原测试 C++ 为 2000 ticks / 1000 cycles 的固定 directed stimulus；未添加新 seed。事先注意到其 read scoreboard 检查“返回值出现在某个 slave memory”，而非精确地址/事务匹配，因此即使将来 clean PASS，也不自动证明目标故障判定力。

按其 Makefile 和 include 闭包，仅获取实际需要的公开 BFM `dozecat/vaxivip@330c66b8d229916dba5b68101494d8663c3c9432`，放在实验目录 `dependencies/`，逐 Git blob 核对。未运行会更新共享 cache 的上游 downloader，未获取当前测试不需要的 corosim；BFM 不计作第三个 DUT 或独立来源。

`dozecat-native-plan.json` 固定 73 个 stage 文件、原始 Makefile/RTL/TB/BFM、Verilator 5.035 及工具 pin，300 秒外层上限、`-j2`、零重试。原始 source/TB/BFM 挂载只读，仅 obj_dir、waveform.vcd、.stamp.verilate 可写。实际输出明确为 `%Error: Unknown warning specified: -Wno-PROCASSINIT`，make rc=2，obj_dir 为空、波形 0 字节，没有 `SIMULATING` 或功能汇总。

独立 `dozecat_audit.py` 重核全部工件、命令与 mounts，确认 UNKNOWN 来源于构建前工具版本不兼容，不能补成功/失败硬件标签。9 项离线 summary 判定检查通过，包含零检查、矛盾、缺波形、超时、重复 summary 和 wrapper rc 不掩盖功能 FAIL。没有为绿灯删除 warning 选项或更改本批工具。

## 原始证据、第二份与实际恢复

主封存根 `_r5_pilot/archive/final-source-screen-b3/`，第二份为 `/tmp/tehm-r5-b3-0vhn110r/`。原始 `evidence.tar.gz` 内 301 个文件、恢复包外层 23 个文件；每份 tar SHA256 与另一份一致，解压全清单一致，并已另行只读复核。

恢复在只挂已封存 B3 根、无原始 corpus/主仓库/网络的进程中，重算两项原始审计，再实际 cold 编译/运行一次原 LibFPGA native case。恢复 exit=0，所有 trace 计数完全复现；VCD 只移除 wallclock `$date` 后逐字节一致，仍然 PASS 但 skid 资格 NO-GO。未重试 dozecat，未新增样本；锁定系统工具依赖仍为外部依赖。同机设备 2081/2050 和 `/tmp` 不代表异地长期持久保存。

| 工件 | SHA256 |
|---|---|
| B3 preregistration | `f84e4433e58661d54a47d1e5620d71801cfa16271558c618be2c1bda120cc0fc` |
| acquisition receipt | `3786ea7329cc4ca18abaa87eea6dc9b9107e3cc8e04de4b65d3570c5dd5b3aeb` |
| LibFPGA original receipt | `991b5a89f08caf612ca8d0268bb73cf1a9781edbb4760fad25fc6597bbd8fa00` |
| LibFPGA split-status audit | `35159bb9a2b092ddfc82fc4d6a5a8463581fefd0a206c02de7ab2599bbb00ff7` |
| dozecat original receipt | `f243a9911a681e8ce0ceb089d06ecb8f79cbd8e1d813357f420ce0d710d33eeb` |
| dozecat raw audit | `73de1063979a041d3aeb742e4787f571385f3d9113952539f9ab7244ba8b68f7` |
| original evidence archive | `f585bf94a314b7a912c05bcf383a86fdf7cbe9c272012d0bbbc57b81cd401214` |
| recovery evidence archive | `cbbdbe4c1ec7c5aed6763e2384444767ed8c2e3e51dd00c61d841647274062c6` |
| actual recovery receipt | `e5845f1adfafacf3cdeb3e4bf37101ff39c1698e4ddc0891c558284be694aaee` |
| final seal | `d29f7671f4bf4a1f792b11609e9b221f52d052834238640301b9e2cacae7622b` |

## 下一步不变的边界

不据 B3 结果调 gen5 binder，不用其他 seed 或删 warning 改写这批冻结结果，也不将 clone 数量充作 final N。下一项具体工作是核对 dozecat 所需 Verilator 版本及可用工具；若解决兼容性，必须另立资格运行代次，保留本次 UNKNOWN，再审查实际 native 义务与来源历史。LibFPGA 当前 native scope 保持未合格；另建 augmented oracle 需单独范围与版本，不能与当前 native 口径混合。

论文范围的来源抽样/样本依据仍未完成，未认证来源独立性，也没有新迁移或 ΔMemory 收益。无 upstream 修改、模型调用、Memory 更新、production、清理或 push；仅保存本地阶段检查点，R5 总目标仍开放。
