# R5 dozecat augmented oracle G1：输入协议前提失败，不能归责 DUT

接续 [Q1 原生负控](research_r5_dozecat_native_sensitivity_q1_20260926.md)，在独立 `DEV_RESEARCH_AUGMENTED` 代次中先验证环境前提。**两臂原生测试仍各 6 PASS，但输入源在背压后撤掉 valid，违反本 scope 的输入合约；增强 DUT 判定均为 UNKNOWN，敏感性 UNDETERMINED，clean reference 不准入。** 不能把 native PASS 当作健康参考，也不能把前提失败之后的队列异常直接称作 RTL bug。

这也收紧 Q1 的结论：旧规则下的 native MISSED 与原始日志保留，证明的是既有激励下的值差未被原生检查报告。Q1 没有验证输入协议合法性，故原“检出 0/1”不能用作合法协议输入上的检出率或合格 repair probe 分母。当前该 scope 仍 NO-GO，而非新增功能修复失败。

## 新代次与冻结边界

工作根 `_r5_pilot/dev/dozecat-augmented-oracle-g1/`。`preregister.md` 在新 monitor 读取真实波形、任何新运行之前固定行为义务和归责规则；该精确 scope 明确登记为 oracle DEV，不是 TRAIN 或未见 FINAL_TEST。gen5 runtime/binder/Memory 没有改动，也没有模型调用。

监测范围仅为 `TOP.axil_interconnect_tb.dut.S_if[0].buffer_on.u_w` 的公开端口：clk/rst、data_i/valid_i/ready_o、data_o/valid_o/ready_i。接口为 36-bit `{wdata,wstrb}`；monitor 不读取内部 state/skid_data、clean counterpart、mutation metadata 或 gold edit。语义依据是 [Arm AMBA AXI/ACE IHI0022H §A3.2](https://developer.arm.com/-/media/Arm%20Developer%20Community/PDF/IHI0022H_amba_axi_protocol_spec.pdf) 的 valid/ready 握手及阻塞期间稳定性要求，不声称验证整个 AXI interconnect。

冻结检查分开记录：输入阻塞后 valid/完整数据保持；接受输入队列与输出 payload 的顺序一致；strobe 保持；输出阻塞后的 valid/完整数据稳定。允许同周期输入/输出，不按具体 RTL 写死内部延迟。复位清除队列，未排空输入只记 pending，不据有限 trace 宣称 liveness。零传输、缺失或未知波形不能 PASS。

DUT 违例只有发生在首次环境违约**严格之前**，才能作为合法前缀上的反例；环境违约同周期及其后的异常不被补成硬件 FAIL。完整 clean 准入另要求环境和各项义务均满足且有非空覆盖。

16 项 synthetic 检查先通过，包括 payload/strobe 错误、无来源输出、同周期直通、输入撤销/变化、环境错误后的归责阻断、环境错误前的合法反例、输出阻塞、复位与无传输。随后锁定 plan，使用原 Q1 的完全相同 clean/fault 源码、工具、Makefile、BFM、stimulus 和 warnings 做两次新 cold native 运行，各 -j2、300 秒、零重试；没有修原 BFM、换 probe 或调测试直到 PASS。

## 实际结果与独立端口核对

| 结果 | clean | fault |
|---|---|---|
| Native summary | 1000 cycles，6 PASS，0 FAIL | 1000 cycles，6 PASS，0 FAIL |
| 输入假设 | FAIL，4 次 valid 撤销 | FAIL，同样 4 次 |
| 撤销采样 tick | 15 / 75 / 135 / 205 | 相同 |
| 合法前缀 DUT 反例 | 未建立 | 未建立 |
| Augmented DUT verdict | UNKNOWN | UNKNOWN |
| Clean reference admission | false | 不作为 clean |

首次见证：tick 13 所采样的前一下降沿，`slv0_wvalid=1, slv0_wready=0, wdata=0xA5A5A5A5, wstrb=0xF`；tick 15 所采样的前一下降沿，valid 已清零，数据及 strobe 也清零。此前受阻的 offer 并没有完成下一次上升沿握手，不能因为 ready 在周期内变高就撤掉 valid。

除内部端口 monitor 外，`top_phase_audit.py` 用单独的 token 解析路径只读取 TOP 的 slv0 write channel，独立确认相同四个撤销位置。原 C++ TB 的采样/eval/output 更新顺序与记录中的下降沿采样一致。BFM 含延迟 clear 逻辑，但这里不凭静态代码扩大成全部行为的唯一原因，也未尝试修改它。

在整个已含非法输入的 trace 上，该单个缓冲接口观测为 4 个接受输入、6 个接受输出、3 个可配对输出、1 个 pending 输入；额外输出诊断在 tick 23/83/151，均晚于环境失败。fault 的 3 个 payload 差异仍在 tick 17/77/143，最早也晚于环境失败；输出稳定性差异最早与环境失败同在 tick 15。这些观测完整保留，但均不被提升为合法激励下的 DUT 反例。

## 两份证据与实际恢复

主封存 `_r5_pilot/archive/dozecat-augmented-oracle-g1/`，第二份 `/tmp/tehm-r5-dozecat-aug-g1-6kun3exd/`。原包 460 文件、77 目录，包含新 oracle、Q1 固定源码/工具/辅助验证器和原始运行；恢复补包外层 95 文件。两份 tar、全文件及目录清单一致。

隔离恢复先重算原始新 audit，再从第二份源码/工具实际 cold 构建运行两臂。原生结果、增强结果、环境违例和两份完整波形均逐项／逐字节一致；UNKNOWN 没有被恢复改成 PASS。宿主系统依赖仍为外部固定依赖，不声称全系统重建。新增 2 次原始执行 + 2 次恢复执行，复用原 Q1 probe，新增 probe 和方法任务均为 0。16 项监测检查和现有 67 项计量 conformance 通过，不等于经验性收益。

| 工件 | SHA256 |
|---|---|
| preregistration | `96b1e0407cf2716680f81e9b955f5d49263af1f7a66de01a29fe9fe590960b26` |
| frozen augmented plan | `0f17dcbfdb22502967d2dca65c7a75286f9ab7ce5ef1bc1d114b45c882f3aa4a` |
| original augmented audit | `99bce7549c46c327c44657d6c52f94b152bfcd3118546240e41ac3999d75c91d` |
| independent top-port diagnostic | `fcd929bf09cbeb11a7613fd7765ec4d9ecee9615c487bc113c42f299e570ca08` |
| original archive | `8b1a90766681912f22ebd7b133d78ca7fe94726c9f9f0bf06a90fbb88d61f769` |
| recovery archive | `1be26f0313324506fb73f9f8f3e54a36f6fb44bad2af42391928b0cb6197a118` |
| actual recovery | `7ee726442f9966731316cfda5092d8b8dfcc1b2de414d2527861294c6a5e1b25` |
| final seal | `93576b0a259b81cbda4c60c17ef44c773d7c242a69bb11cd690140b3f7e96ca4` |

备份仍为同机设备 2081/2050 的本地副本，`/tmp` 不代表长久托管或异地保存。没有删除旧证据、修改 upstream 或 frozen gen5；三份 Memory 摘要与 F1 freeze 一致。

下一项实质工作是在新开发代次中使用满足输入稳定性约束的有界驱动，先区分测试环境缺陷与 DUT 行为，再决定能否建立 augmented 资格；旧 native BFMs 和原记录保持不变。不凭当前 UNKNOWN 制造 repair task，也不将本 DEV scope 再称为未见 FINAL_TEST。论文来源抽样/样本依据仍开放，R5 目标未完成，无 production、清理或 push。
