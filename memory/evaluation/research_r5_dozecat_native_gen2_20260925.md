# R5 dozecat native gen2：工具兼容性解除，原生基线真实 PASS

在已授权的 `_r5_pilot` 中续接 [B3](research_r5_final_source_screen_b3_20260925.md)，按 Revision5 §7.2、§19.2–19.4 另立资格代次。**原版 dozecat native 测试实际运行 1000 cycles，6 项检查 PASS、0 FAIL；尚未做故障敏感性实验，不是 repair task、独立来源认证或 TEHM 迁移收益。** B3 的 Verilator 5.035 UNKNOWN 保持不变，冻结 gen5 方法和 F1 四视图各 0/1 结果不变。

## 固定输入与真实执行

工作根 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/paper/dozecat-native-gen2/`。`preregister.md` 在新工具获取和本代 native 执行前锁定：沿用 B3 的 `dozecat/axi_lib@f15227300d72e79bc537817c89340a084d3b9072`、`vaxivip@330c66b8d229916dba5b68101494d8663c3c9432`、全部 73 个 stage 文件、原 Makefile/测试/警告选项；不改变 stimulus 或添加 seed。原 TB 为固定 directed stimulus，没有显式随机种子计划。

有界本地工具检查找到的两套 CAD 都是 5.035。单独获取官方 [Verilator v5.038](https://github.com/verilator/verilator/tree/f037ac50b419f675ea23fa38abfc363505d7b43a)，固定 commit `f037ac50b419f675ea23fa38abfc363505d7b43a`；源码实际含 PROCASSINIT，安装后 synthetic lint smoke 也接受原选项。它是已验证的兼容版本，不声称是最低必需版本。[官方变更记录](https://github.com/verilator/verilator/blob/f037ac50b419f675ea23fa38abfc363505d7b43a/Changes) 说明 5.038 将 assertions 改为默认开启，因此不能把两工具代次视为语义完全相同。

工具构建、安装均仅在新实验目录内，无 sudo、系统替换或 frozen 软件修改。原 `make -j2` 用时约 442 秒，以 rc=2 结束，原因是手册页依赖 help2man 缺失；完整日志保留。单独登记恢复后获取 Ubuntu 索引中的 `help2man=1.49.1`，核对包 SHA，局部解压并仅对构建补入 PATH；随后一次增量 make/install 成功。这不是从头重建工具，也不是硬件试验失败。

`plan.json` 在 native attempt 前固定工具安装清单、源码、脚本、宿主工具摘要、300 秒上限、`-j2`、零重试。原生运行约 9.19 秒，rc=0，波形 497,478 字节。隔离进程只读挂载源码/工具，仅 obj_dir、波形和构建 stamp 可写；原始 corpus、主仓库、网络均不可见。没有 mutation、binder、primitive、Memory 读写或模型调用。

## 审计与结论边界

独立 native 审计核对原始命令、文件清单、结果摘要及 6 条实际 read PASS 明细；12 项 synthetic adapter 检查通过，零检查、缺波形、矛盾或重复 summary、超时不能 PASS，functional FAIL 不被 wrapper rc=0 掩盖。

另做标记为 **post-run diagnostic** 的编译与波形检查：`__verFiles.dat` 确认全部 14 个 RTL 输入、native SV top 和工具输入；C++ `.d` 确认 6 个唯一 BFM headers。原 Makefile 的每个 `-Wno-*` 选项均保留。15 个缓冲实例的 state 被记录，其中 5 个 source-side 实例确实出现 state=2；其他 10 个没有。这里只统计 2000 个 VCD dump 时间点的状态观测，不伪称 2000 时钟周期、精确握手覆盖或故障检出率。

补充 auditor 开发时曾误断言“7 个唯一 header”和“实例名包含 skid”；初始及中间脚本、两次只读失败重放日志均保留。最终改为实际 6 个唯一 headers，并逐一核对 15 个实例的 data/handshake/register 信号集；未改原始运行、native PASS 判据或任何准入门槛。

原生 scoreboard 仍只是检查读值是否出现在任一 slave memory，并不严格按地址/事务匹配。实际进入 SKID 也不等于对应 payload 故障可被检出。当前只解除工具阻断、建立本代 clean baseline；`fault_qualified=false`、`final_admitted=false`，新增方法任务为 0。

## 两份封存与实际恢复

主根 `_r5_pilot/archive/dozecat-native-gen2/`；第二份 `/tmp/tehm-r5-dozecat-gen2-tcj1hpm7/`。原包含 7543 文件，包括锁定 release 源码、完整工具安装、stage、构建与 native 原始记录；不打包编译缓存对象和 `.git`。两份 tar SHA 和解压全清单一致。

初始包的恢复入口有一次插入错误导致语法不合法，发现后没有执行它；保存 syntax-check 失败，另附 `recover_v2.py`。实际 recovery r1 重算两个原始审计后，在启动模拟器之前 UNKNOWN：文件型 tar 未保留 stage 中空 obj_dir，只读挂载下无法创建 mount point。保留其完整 raw receipt、日志与费用，不计作硬件 FAIL。

预登记 recovery r2 后，重新解压同一包，只恢复原本存在的空 `stage/sim/tb/axil_interconnect/obj_dir`，所有文件摘要不变；同一修正入口在隔离路径中实际 cold 编译并运行原 case。结果仍是 1000 cycles、6 PASS、0 FAIL，两个原始审计复算相同，实际恢复波形与原始波形**全文件逐字节一致**，状态观测也相同。

恢复补包外层 74 文件，含原包、入口补丁、空目录恢复规则、全部失败和成功恢复记录；两份 SHA/解压清单再次一致。后续复跑需使用该补包中的 `recover_v2.py` 和目录恢复规则，不能只用原包中的错误入口。两设备 2081/2050 仍是同机本地副本，`/tmp` 不代表长期托管或异地备份；没有删除原始证据。恢复使用既有宿主编译/系统依赖，不声称完整操作系统可重建。

| 工件 | SHA256 |
|---|---|
| preregistration | `94f138e55c7abfc99049d8f2ec94821fad37c8bdfc43bf36546645d25493a810` |
| native plan | `a7dfed066f6faa22de5df6d183be74a71ff92af3c353cb608f1fdbcb5c9c2db3` |
| original native receipt | `55dc147f1d3f428cdd8aa807d308f300d54f59d439f09e6138dcc167138b1e1d` |
| native audit | `5bddf3262b3c96333b7f0656fa4f7e3dae7023c1a7b1adaab5ca914b397ef4e9` |
| compiled closure/trace diagnostic | `5e7c3b8cae8563120ffe826a3e6d891e859906af8561a0bf610acb7fbcf1e63d` |
| original archive | `0fcf577fc0b99e92990739d80733334b5ae97d8de6478fe7f57ec8e62424c6a0` |
| recovery supplement archive | `9eac43c255a2a81e454d1be707833963764a7642ce35ce4172e119ee266a6dcb` |
| successful actual recovery | `f38764a2974a5916180443de5736ba6dee1158b97253b02495bc7802a2725a6c` |
| final seal | `6dda1c268798cc20172e25c55f2537646c09f74d4a959b151ea411429a908702` |

本阶段为一个新工具代次的 clean native attempt，加一个实际成功恢复；另有一个在模拟器启动前失败的恢复。它们不增加候选、独立来源或方法统计样本。上游 checkout 和 frozen gen5 Git tree clean；M−/M+/Mremove SQLite 摘要再次与 F1 freeze 一致。

下一步是固定所需目标／保持义务和有界负控，再测原生判定力，同时继续来源历史和暴露审查。没有完成这些门禁前不进入 final 方法比较，不根据本结果改 gen5 binder，不将当前观察重新包装为未见数据。论文规模抽样/样本依据仍开放。无 API、production、清理或 push；R5 总目标尚未完成。
