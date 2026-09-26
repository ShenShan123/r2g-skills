# R5 dozecat Q1：原生测试漏检实际交付的 SKID 写数据损坏

> 后续限制：[augmented G1](research_r5_dozecat_augmented_oracle_g1_20260926.md) 发现原生输入驱动在背压后非法撤掉 valid，最早发生在 tick 15，早于本文的损坏传输。本文旧规则下的 MISSED 与 raw artifacts 保留，但“0/1”不能用于合法协议输入的敏感性或合格 repair-probe 分母；当前增强归责为 UNKNOWN／UNDETERMINED，scope 仍 NO-GO。

接续 [native gen2](research_r5_dozecat_native_gen2_20260925.md)，执行 Revision5 §6.5–6.6、§13.1、§19 的一个预登记负控。**clean 与 fault 都报告 1000 cycles、6 PASS、0 FAIL；波形证明 fault 中 3 次实际接受的写传输有一位 payload 损坏，106 个非目标信号逐时间点保持一致。该 native scope 对这一负控为 MISSED（检出 0/1），不能准入修复／FINAL_TEST。** 这是测试判定力结果，不是 TEHM 修复失败或迁移结果。

## 执行前固定的 scope

工作根 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/paper/dozecat-native-sensitivity-q1/`。`preregister.md` 在 mutant 创建与两臂运行前固定一个 constructed probe；`activation-clarification.md` 在执行前明确必须观察到 strobe bit 0 有效的接受传输，并保持外部 master 的输入写数据不变。

沿用 dozecat 原始 commit `f15227300d72e79bc537817c89340a084d3b9072`、BFM commit `330c66b8d229916dba5b68101494d8663c3c9432`、Verilator 5.038 和原 native top/Makefile/激励。clean 是原 73 文件；fault 私有副本仅改 `rtl/skid_buffer.v` 的一处组合输出：只在 SKID 且 WIDTH=36 时翻转输出 bit 4，其余不动。本 top 的通道打包为 `{wdata[31:0],wstrb[3:0]}`，故该位属于 wdata，不改变 strobe；地址、响应、读数据缓冲宽度不同。该探针作用于此配置的所有 36-bit 缓冲实例，不声称适用于任意参数。

这不是上游自然 bug，也没有修改 upstream checkout。不替换探针、不改 seed/测试/warning、没有调用 binder 选择更容易的目标。`plan.json` 在执行前锁定注册、runner/auditor、精确单行 diff、两份完整源码清单、工具和宿主依赖摘要。clean/fault 分别独立 cold build，各 `-j2`、300 秒上限、零重试；实际约 8.64/8.69 秒，均 rc=0。原始 corpus、主仓库、网络、Memory 对模拟进程不可见。

## 目标生效与保持义务

目标生效不能仅由 native PASS/FAIL 推断。冻结的 evaluator 从原始 VCD 检查：SKID 状态输出相对 skid_data 恰好只差 bit 4，至少一次在 valid/ready 接受边沿出现，且实际外部 slave 接受的有效字节发生相应变化。上升沿使用此前下降沿的稳定值，与原 C++ TB 先采样/求值、后更新 BFM 输出的调用顺序一致；复位边界不充作传输。

两臂每个波形均有 2000 个 dump 时间点，即原测试的 1000 cycles，不是 2000 个周期。fault 有 1816 个 SKID 损坏状态观测，其中 3 个形成实际接受的输出；它们仍只属于一个预登记 probe。

| 接受时间点 / slave port | clean 写数据 | fault 写数据 | 两臂 strobe |
|---|---|---|---|
| tick 17 / mst0 | `0xA5A5A5A5` | `0xA5A5A5A4` | `0xF` |
| tick 77 / mst0 | `0x12345678` | `0x12345679` | `0xF` |
| tick 143 / mst0 | `0x11110000` | `0x11110001` | `0xF` |

两臂均有 9 个外部接受写记录，时间点与 port 的键完全相同。106 个固定非目标信号在全部时间点一致，包括 clk/rst、地址/prot、strobe、响应、全部 valid/ready 及 master 输入写数据；paired preservation 为 PASS。这是声明范围内的配对参考比较，不是完整 AXI 正确性、独立 oracle、全输入形式证明或新 native checker。

原 native adapter 与明细交叉检查确认两臂均实际完成 6 个 read checks，全部 PASS。源码中的 read checker 判断返回值是否出现在任一 slave memory；BFM 捕获数据的方法也不严格检查 ready/strobe。实际日志可见损坏写值，而后续 read checks 仍全部通过；本实验不把某个静态弱点单独宣称为全部漏检的唯一原因。外部接受传输的损坏已由独立波形比较证实，不以 BFM 的自报 WR success 代替握手证据。

因此：clean PASS、probe 已生效并交付、fault native PASS，按冻结规则输出 **MISSED**。原生测试不是因为没有跑到或没有输出而 UNKNOWN。本 scope 没有发生工具失败、超时、编译无效或未激活；也没有据结果增加探针或更改判据。

## 原始审计与完整恢复

审计核对两臂 raw receipt、pre-execution lock、全部原始文件、实际命令、14 个编译 RTL 输入、保留的 warning 选项和 native check 明细。15 项 synthetic verdict/sensitivity 检查通过；现有论文计量 67 项 conformance 也通过。这些测试不增加方法样本。

主封存 `_r5_pilot/archive/dozecat-native-sensitivity-q1/`；第二副本 `/tmp/tehm-r5-dozecat-q1-wdc3kpfg/`。原包 365 文件、71 目录，包含实际使用的完整工具安装、两个 stage、原始输出和入口；明确保留空 build mount 目录。恢复补包外层 94 文件、7 目录。两包均核对两份 SHA256、全部文件和目录清单。

隔离恢复先复算原始 audit，随后实际重新 cold 编译并运行 clean/fault；两臂结果、完整波形和全部 paired diagnostics 都完全复现，仍为 MISSED。工具从第二副本挂载，原始 corpus/主仓库不挂载；宿主系统依赖仍是显式外部依赖。共 2 次原始 native 执行 + 2 次恢复执行，仍是 **1 个 probe、0 个方法任务**。同机设备 2081/2050 与 `/tmp` 不构成长久托管或异地备份；没有清理原始证据。

| 工件 | SHA256 |
|---|---|
| preregistration | `3a8593576840e87d7a29db0bae2909b39b9ac75a088543ba7a1a8698ee3159d3` |
| frozen plan | `d97eeb6af51b836d404a2e61de6a2ff5a3bd59657d793e05cb5ba6102bb9109d` |
| clean receipt | `0ed6ac92dbb6cf0b58e5de5294f5b14c9e4792ceb97e871f6b4fb39c88d9486a` |
| fault receipt | `e89b70ce6a1b53af3d0b315a7e6fe6303b27bf60a0d8b7264e3ba0f2a575286f` |
| original audit | `1174b95bfc242ea85e6b9c93c27506696275558258c9cb85c51e7e84bef2e6c7` |
| original archive | `daa30f2bd044697e55891d471a0acaca38e16298de5cf99e186581720f0e7f19` |
| recovery archive | `16f5328e6e7c9e8617278b8622cb5f1201cbfae6d833f59b5348af2d3080d9dd` |
| actual recovery | `0f0dd411eef1a8e1f8227e37ad4b4320b903000542b7cfcb1423bb8fbeaedec1` |
| final seal | `fffe732f8429dc732b919ae27bb2f333ecd99932ca9dce71a8d35f59cd526bf1` |

## 对主链的影响

dozecat 当前 native SKID 写 payload scope 固定为 NO-GO；它不同于 LibFPGA 的未覆盖、也不同于旧 5.035 的基础设施 UNKNOWN。三者不能合并成硬件失败或修复失败分母。当前没有新增合格 repair/final task、来源独立性认证或 ΔMemory 收益；F1 结果不变。

若继续使用此具体来源，应先另登记 `research-augmented` 开发代次，按公开行为义务检查 clean 与同一负控，保留 native 漏检并分开报告；不能在这个 scope 内补 assertion 后覆盖旧结果。之后才是来源历史/暴露审查和独立最终准入。论文范围抽样与样本依据仍开放，不因 native PASS 或这个已观察负控自动完成。上游与 frozen gen5 工作树 clean，三份 Memory 摘要与原 freeze 相同。无模型调用、Memory 更新、production、清理或 push；R5 总目标仍开放。
