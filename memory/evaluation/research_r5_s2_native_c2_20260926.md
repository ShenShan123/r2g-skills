# R5 S2 C2：真实 DEV RTL evaluator 接线与冷恢复

接续 [C1 组件](research_r5_s2_controller_c1_20260926.md)，已实际跑通固定提案→C1 候选写入→隔离预处理/编译→原生测试→私有判定→controller 枚举反馈。**三个策略标签下的脚本演练都得到首轮 target FAIL / preservation PASS / native FAIL，第二轮三项 PASS。** 这是使用已知 DEV 答案的集成校准，不是三种 Memory 策略的真实比较，不是 answer-free 修复，也不进入迁移或 FINAL 分母。

## 范围与预登记

工作根 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/agent/s2-native-c2/`。`preregister.md` 先于本次 evaluator 创建与运行登记；原 repo 为 `namangoyal-work/fpga-tick-to-trade`，commit `ee3ef5fab4d82127c74733e037231e2c7f1f8f58`。使用已在 gen3 开发中观察过的 `axis_skid`，不读取 F1 或将该源改名为未见目标。

两个原生测试、Makefile、tb_util 均未修改：`skid_random_backpressure` 是 target，`skid_full_throughput` 是 preservation；native 指这两个测试组成的限定 suite，不是整个仓库所有模块。目标测试检查随机背压下 2000 个输入的 payload/last 顺序，保持测试检查始终 ready 时 200 个 offer 的输入不拒绝；不据后者宣称所有非目标时序义务都已证明。native 内部显式 RNG 仍为 `0xC0FFEE`，本代全局 cocotb seed 固定 `20260926`。

固定脚本对三个 policy labels 提供同样的两条响应：先 no_action，若已知 FAIL 允许继续，再替换为既有 DEV candidate。fault SHA `caae468c6ed9f94e45958f7033d0abee3fa5f576d0279d61de1a0a2bf384c3db`；已知 candidate SHA `652723dccd678e33d23547086b3efae819a9060ff4897b1391331beaca5e7253`。它们均来自旧 DEV 工件，不是模型推导。没有实际 No Memory/Legacy/TEHM 检索，没有 Memory use 或 ΔMemory 归因。

## 新 evaluator 的能力与限制

`research_r5_s2_native.py` 是固定单叶 scope 的新 DEV adapter。现有 cocotb 解析器要求 pytest wrapper，而此处是 make，故没有伪造 pytest 结束行或放宽旧解析器。新适配器核对 XML 结构、两个确切 test IDs/class、seed、非 skipped/error、native TESTS/PASS/FAIL/SKIP 汇总与 make rc；缺失/冲突/超时为 UNKNOWN。实际 make rc 为首轮 2、次轮 0，功能结果不只看 rc。

第一阶段仅挂载候选源码和系统工具，`iverilog -E -g2012` 产生预处理源码，没有 private TB/原始 corpus/网络。新有界语言策略拒绝预处理后仍有 `$` 的文件，保守到注释/字符串也拒绝，另拒绝空/NUL/超限输入。这样本 profile 不执行直接或宏展开的 HDL system task/function；`FORMAL` 未定义，原形式断言按正常仿真预处理排除。不支持属于 UNKNOWN，不是硬件 FAIL。

第二阶段只读挂载该预处理文件、原生私有测试和固定 cocotb 2.0.1 依赖，使用原 Makefile 的单源覆盖，fresh `/out/build` 编译。固定 compiler path 与 VVP 中实际 source filename、候选/预处理/VVP 摘要组成输入链。private TB 和原 candidate 运行后复核不变；controller 仅接收 target/preservation/native enum、整体 verdict 和 stop，不接收 expected、gold、原生断言或文件路径。

这不是任意恶意 RTL 的安全认证，也未验收未来 Agent 的完整进程/网络权限。限制 `$` 是本次新 DEV profile 的显式适用域，不改变 v7 binder 或 gen5。未来 broker 必须继续隔离权限；工具漏洞、同权限并发篡改以及尚未实测的攻击场景不由当前六次正常 native 执行排除。

## 实际执行与软件检查

每策略标签最多两次候选，C1 状态机先预算预留、再验证候选摘要、预留 evaluator、最后根据可信反馈推进或停止；原始六次均终态。每次独立预处理限 30 秒，native make 限 90 秒，零重试，进程组超时终止及 16 MiB 单输出文件限制。六次原始 native make 墙钟之和约 7.8092 秒，不含预处理/准备/审计等全部开发成本。

本次 C1 账本里的 8000 input + 2000 output 是**脚本演练预留量**，并非实际 token 使用或授权。实际模型调用和 model tokens 都是 0。六次 native suite 各两项检查不是 12 个独立任务；恢复也不增加样本。没有生成或声明 VCD 波形证据。

3 组新离线 adapter/language 检查通过，含两个有效汇总控制、13 种缺失/冲突输入与保守语言拒绝；C1 的 16 组检查和既有 67 项 bookkeeping 检查再次通过。它们不替代真实 native 执行，也不等于全仓回归。当前未完成真实超时/攻击输入的专项进程演练，后续不得称那些边界已经验验证。

## 封存、原始审计与实际恢复

原始 audit 独立遍历实际 XML testcase，核对源码、预处理文件、VVP、命令/原始文件摘要、三策略标签顺序、hash-chain 账本和公开反馈。完整输入/result 包 256 文件；恢复补包 111 文件。第二副本 `/tmp/tehm-r5-s2-native-s2oilee1/`。

实际隔离恢复先从副本重算全部原始 audit，再重新执行六次完整提案/native 序列；原仓库、corpus 和网络不可见。六项源码/预处理摘要、义务 verdict 和规范化 raw audit 一致。两份原始包、恢复包、解压清单及恢复输出经独立哈希核对。

一次额外只读核对假定新旧 VVP 应逐字节相等而断言失败。逐项检查显示六项预处理源码均 byte-identical，VVP 均不同，diff 包含 Icarus 的地址式 scope/net 标签。原 VVP/新 VVP 都保留各自 SHA，不修改二进制或用标准化结果覆盖它们；**不声明 VVP byte-identical、完整 XML byte-identical 或形式等价**。这不违反本代预登记的实际重建与行为结果恢复要求。

| 工件 | SHA256 |
|---|---|
| preregistration | `fe5c4d3c94cfa6b261170f62e3bb8f1c2e476392829393ac382c2791f5223ff9` |
| frozen plan | `6deadf23dde62503cb6e61bd32fc98a18b151308905cdfb4c4a2cbb2302cae83` |
| native adapter | `99ba6c824536cde96835f2545a8218998578291a610346d0d553cc058d200e81` |
| original raw audit | `2ca832b60aa263658b2108c2b3284c4a476554b62f39d051a722960d71537def` |
| original archive | `a2a201e89db85d3eff71fb1ade5700b0505b83e1ab775afe098329eb2df1897a` |
| recovery archive | `54b86f49379899ecac4a51b8f2e425cd01d4da7812523595f56e2a14c7721971` |
| actual recovery | `7d4b29f0f7cfdc3a5f98fde236033130f2b5e84353d1f2564ccc69310e015f02` |
| seal | `857cb1eb4c0b7007880bad5680417f4fe5e9fb0a24395e7ac626def1cbe77961` |

备份仍是同机 `/tmp`，不是长久托管或异地故障域。无 upstream/gen5/Memory 修改，无 production、模型调用或 push。冻结 gen5 工作树及三份 Memory SHA 再次核验不变，F1 四视图 0/1 不变。

下一步是专项越权/超时验证及三策略公共 prompt/Memory context 与 broker 接入。先前询问的 provider/model 和最多 6 次/60000 token 建议仍未获回复，不视为授权。S2 尚非完整 Agent；论文 R5-8 来源抽样/样本依据也仍开放，R5 目标未完成。
