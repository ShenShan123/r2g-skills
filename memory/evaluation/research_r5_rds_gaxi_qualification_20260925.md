# R5 B2 GAXI：私有故障资格与独立恢复检查点

依据 Revision5 §6.6、7、8.4、9.3、14 和现有[论文计量协议](research_r5_paper_protocol_20260925.md)，继续同一个预选 B2 scope，没有根据 binder 命中或方法成功更换候选。此记录接续[B2 原生基线](research_r5_final_source_screen_b2_20260925.md)，不回写旧失败、旧 receipt 或冻结 gen5。

结论：**1 个预登记构造探针通过六项资格门禁；两次独立目录恢复均重现六项 RTL 结果，第二次恢复同时通过计量包检查。新增方法修复任务仍为 0。** 此处的 PASS/FAIL 是测量资格，不是 TEHM 修复、answer-free transfer 或 ΔMemory 收益。

## 1. 来源历史复核及其界限

来源仍为 `sean-galloway/RTLDesignSherpa@66e4e1b044f79be2c66fbd466c8918ed15e343c6`，DUT 为 `rtl/amba/gaxi/gaxi_skid_buffer.sv`，SHA256 `0b6ca353d3d24669a7411d53e377281d2837ae66d21eb887c2e6bcb3179be57a`。不修改原始 checkout。实际 DUT/include/counter、testbench、runner 和依赖继续使用 B2 固定闭包。

`paper/final-source-screen-b2/history-r1/` 保存按固定 HEAD 查询的 DUT/TB/reset/filelist 历史、原始 GitHub 响应及历史源码。DUT 路径查询得到 10 个提交；另外三个路径分别为 19、2、2。比较初始、重构前、重构、当前四个 DUT 版本与八个明确的既往 TRAIN/DEV/qualification/pilot/control 叶节点，共 32 对，未发现精确字节或去注释 token 序列相同。

[初始提交](https://github.com/sean-galloway/RTLDesignSherpa/commit/7ed82d1769bba494e3c0772ef9bee1ce1db402ac)没有父提交，保存的实现使用 packed-vector 存储；[后续重构](https://github.com/sean-galloway/RTLDesignSherpa/commit/ac1b0cbfba0d516fe4a81922625ef86657b1b25b)改为 unpacked-array shift-register。重构提交明确记载模型辅助文档/实现，不能据此证明独立作者或没有外部来源。

本次分组标签为 `rds_gaxi_shift_array`，判定仅为 `OBSERVED_DISTINCT_IMPLEMENTATION_CLOSURE`，`independent_authorship_proven=false`。序列相似度只是描述，不作为抄袭/独立性阈值；审查限于已保存的路径历史和八个参考叶节点，未证明导入前历史、rename/pagination 完整性或全网来源独立。不同 owner 不是独立性证据。

## 2. 预登记资格与原始结果

私有工作根：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/qualification/rds-gaxi-task-v1/evaluator-private/`，权限 0700。源码突变、私有 witness、期望向量和原始结果不进入 Git、Memory 或未来 source-only consumer。

在首次创建突变/运行测试之前登记：一个 occupied-buffer drain payload 故障，保持控制、reset、counter 与其他分支不变；target 检查阻塞后按实际握手输出的 payload 次序；preservation 检查 reset 和逐项排空的正常传输；native 使用原封不动的上游 aggregate。私有 target/preservation 是 researcher-authored case assets，不冒充 native 测试，也不是新通用修复能力。

固定 `DATA_WIDTH=8`、`DEPTH=2`、10 ns、native gate、seed 1729、Verilator 5.035 和同一 Python 依赖闭包。每项新建 build root，编译/仿真命令各限 300 秒，构建 `-j2`，无自动重试。原生 node 为 `test_gaxi_skid_buffer[8-2-10-gate]`，实际 cocotb function 为 `gaxi_skid_buffer_test`。

| 输入 | 私有 target | 私有 preservation | unchanged native aggregate |
|---|---|---|---|
| clean reference | PASS | PASS | PASS |
| 预登记构造故障 | 第二次输出功能性 payload FAIL，第一次输出正确 | PASS | 实际 packet-comparison FAIL |

不是“六个测试全部通过”；是六个原始结论满足事先登记的资格矩阵。构造故障不是上游天然 bug。一个 probe、一个配置；native 子测试、三种义务及恢复次数都不增加独立样本。

冷审计重新解析 XML、日志、输入/编译产物、mount/env、实际变更和 native VCD，不仅检查摘要：

- clean native：39 入/39 出、44 个输出阻塞周期、7 次阻塞后输出、0 payload mismatch，仿真 6040.101 ns。
- fault native：16 入/16 出、4 个输出阻塞周期、2 次阻塞后输出、1 payload mismatch，仿真 3020.101 ns。
- fault native 同时留下文件日志的 `UnicodeEncodeError`；实际 XML/assertion/VCD 已独立建立 packet 功能失败。该次级日志异常没有被掩盖，也没有通过换种子或修改测试重跑为 PASS。

16 项 native adapter 反例检查与 8 项独立冷审计篡改检查通过；它们是 synthetic conformance，不进入硬件任务分母。

## 3. 保留的基础设施问题和恢复

首次执行前，新闭包检查发现 B2 旧 inventory 排除了一个 `numpy/distutils/__pycache__/conv_template.cpython-310.pyc`。旧归档实际上已包含完全相同文件；新 runner 按其 SHA256 显式固定这一个既有字节文件，没有删除它、放宽为忽略所有缓存或升级依赖。预执行修补时的一次语法定位错误也在仿真前被发现并修正，见私有 `preflight-note.md`；没有对应硬件 attempt。

初始归档包含 6604 个文件和已提交分析代码 `78cd6394f0718f7a9e6679534b73e2433b200852`。恢复 r1 从第二副本解包，隐藏原始 corpus/主仓库/网络，六项 RTL 和冷审计通过，clean/fault native VCD 均逐字节相同；随后计量包导入缺少 `memory/contracts.py`，整体恢复终态为 FAIL。这次完整尝试及成本保留。

依赖补包只增加同一提交的 `contracts.py`，并在隔离启动时使用此前已固定的 BLAS/LAPACK 链接目标；没有改变原恢复脚本、RTL、测试、方法或判定标准。恢复 r2 先在同样隔离环境通过 67 项计量自检，再重新编译/运行全部六项，独立冷审计及两份 native VCD 字节比对通过。补全后的证据树为 6605 个文件。系统 Python、共享库和 CAD 工具仍是记录/校验的外部依赖，不宣称完整机器镜像或跨工具链可复现。

两份归档及两个恢复输出目录逐文件核对一致：

- 主副本：`_r5_pilot/archive/rds-gaxi-task-v1/`。
- 第二副本：`/tmp/tehm-r5-rds-task-dqefwavh/`，设备号分别为 2081/2050。
- 基础包 `evidence.tar.gz` 与依赖补包 `dependency-supplement-r2.tar.gz` 缺一不可；补包保存恢复启动程序，`recovery-r2/launcher.json` 保存本次实际隔离命令，原 `task/recover.py --inside` 执行六项恢复及 67 项检查。
- r1/r2 分别有 392/397 个输出文件匹配。第二副本是同主机临时存储，不是异地或受管长期保留；不因此授权清理旧证据。

本次 qualification 原始运行及恢复 r1/r2 各执行六项，共 18 项 oracle 执行，probe 分母仍为 1。三次各自 arm launcher wall time 合计为 39.245/38.638/38.942 秒；其中 compile/sim/native 子命令合计为 38.754/38.149/38.453 秒。这两种时间互相嵌套，不能相加为总成本；归档、开发、来源获取等未统一计时项不填 0。

## 4. 冻结方法和计量边界

方法仍为 `2ce921a599c406ab63331561a182d6f4f1bef8cb`，gen5 Memory 的 canonical `report_digest` 仍为 `sha256:f31072bf2c233974c9fbbf4bb4c30539942f07a57a5b6b7e9022611e90f3206b`（不是 JSON 文件字节哈希）。三态 bundle 与旧 pilot 冻结的 SQLite 字节身份未变。本次没有调用 target binder、route/asset selection 或修复 primitive，没有新训练与 Memory 更新。

计量层修正原来把所有 QUALIFICATION 都自动当作方法暴露的预筛规则：只有固定方法后的 evaluator-private 资格、没有方法答案输入/开发使用/观察后方法改变，才进入 `REVIEW_REQUIRED`；缺字段、错误身份或已有其他使用角色仍拒绝。调用方声明不是原始暴露审计，不授予 FINAL_TEST 或启动权限。当前 67 项检查包含 23 个新增拒绝/暴露反例，历史 44 项记录不回写。

实际 gen5 readout 已再次调用旧 pilot raw auditor：`paper/gen5-readout-r3` 的 manifest/results/summary/readiness 四文件与 r2 逐字节一致，仅 receipt 指向新的计量代码。仍只有旧 1 个方法 task，四个 arm 均 0/1 修复；B2 probe 不加入。此次隔离恢复只核对这些旧读数文件的身份，没有在恢复里重跑旧 pilot。

## 5. 关键证据身份

以下均为 SHA256；完整输入和产物清单在私有归档，不将私有答案作为跟踪文件公开。

| 证据 | SHA256 |
|---|---|
| source-history result | `b8b6625137fa97d695511e0e3b8f481447d187a4bddc6233617a0ba15e439e7b` |
| qualification preregistration | `75ce4762fd866bb4016582ff6c2094d9e6b36ba793a4a8344ef4d8b1e2815f18` |
| first pre-execution lock | `6e0bd32860932ea3a066656f4f50793a54936f6c17da352e4e5a15f6030c7b15` |
| first six-arm receipt | `28d05b607a23a3a94f2ecc0b9921143e544c3e44847f0bebb90724df960f42ed` |
| first cold audit | `784a792e6a816898fa09d6893403d143cc021c344f28fd54355870c8d28becc3` |
| base archive | `9054a06a7865c47cbcac974300fe4d2b4facbfe49cd82b09e1ea28e1c94bb174` |
| dependency supplement | `71e8103d2e150fc0418d13545272858ea6afed28e43c92f6421688bfcb153d88` |
| original seal | `d8eaa87fa6e0b992060854cb177db725e8ff53a427f4d37243c9b07345280d2e` |
| recovery r2 raw audit | `476fdbf0fab3f3e47be4453c737b3317298038aa82843a681f63a18a983e7ef8` |
| recovery r2 receipt | `5f21ed92ed656f5ee1f2bb942c62b9b165641cef7bcd815296181157de190484` |
| supplement/recovery linkage | `3c4daa477218a8d8b6c691cda9cb42efd6eea2790b3039cafdb07df7ae758d44` |
| current old-pilot readout receipt | `36297f3340feccb2f58ad011cdee6a89c30128cc34aaca693a2ddcce5b24cb4f` |

## 6. 下一项实质工作

资格前置已经完成，不再以重复 clean baseline 或扩充 mutation matrix 代替后续实验。下一步是对该来源/任务做原始暴露审查，明确可支持的 source-group 层级，冻结有界最终候选框架、opaque task card、角色和同代码/同预算/同 oracle 的配对执行合约，然后才调用冻结方法。不得查看 TEHM 结果后决定是否纳入，也不得先作为新 pilot 运行再改称 unseen final。

当前 `repair_task_denominator=0`（本次 B2）、`final_test_ready=false`、model/binder calls=0。最终数据和统计样本依据仍未冻结，独立作者证明未建立；Agent 三策略仍需另行授权固定 provider 预算。无 push、production promotion、上游修改或证据清理。R5 主目标继续开放。
