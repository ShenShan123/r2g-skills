# R5 S2 C3：实际越权与超时边界探针

接续 [C2 native 接线](research_r5_s2_native_c2_20260926.md)，本轮按新预登记执行六类有界边界检查，**原始运行和第二副本实际恢复均通过**。复用未修改的 C1/C2 软件、测试、依赖和工具锁，没有为了探针通过而修改 guard。它是基础设施/对抗 DEV 验证，不是 repair qualification、Memory 比较或 Agent 结果。

## 实际检查，不是只看配置声明

| 探针 | 实际观测 | 结论边界 |
|---|---|---|
| HDL 直接 `$fopen` | Icarus 预处理 rc=0，结果包含调用；C2 gate 拒绝，native 目录未创建，公开义务 UNKNOWN | 该直接 system-function 输入不能进入仿真，不算功能 FAIL |
| 宏展开 `$fopen` | 宏预处理成功且展开结果含 `$fopen`，同样在 native 前拒绝 | 真实走过预处理后的检查，不以宏语法错误冒充 guard 成功 |
| include 私有测试 | 请求 `/private/tb/test_axis_skid.py`；预处理 rc=1、not found，native 未启动 | 私有测试不在该预处理 namespace 中；未读取其内容 |
| 只读挂载/可见性/网络 | 对 disposable candidate 和 private-test 副本实际 append 被拒绝，文件摘要不变；原 repo/corpus 路径不可见；新 net namespace 无法连接临时宿主 loopback 监听端口 | 只检查这些声明路径和连接，不声称所有 OS/网络攻击均已排除 |
| 输出文件上限 | 子进程实际尝试写 17 MiB，第 16 MiB 处得到 EFBIG（errno 27）；文件大小 16777216 bytes | 证明 C2 的单文件 RLIMIT_FSIZE 实际生效，不是总磁盘用量上限 |
| 超时父子进程 | 实际 fork 子进程并写 readiness/heartbeat；父子忽略 SIGTERM；2 秒 supervisor timeout 返回 rc124，未见匹配 argv 标记的活进程，心跳停止 | 实测同一个 C2 execute 的进程终止路径，不是运行 90 秒 RTL hang 或任意 daemon 族的普遍证明 |

临时 loopback listener 仅绑定本机 `127.0.0.1` 随机空闲端口，用完关闭；未访问外部服务。所有写入尝试都针对本次工作根中的显式副本，未写上游或主仓库。进程检查只输出精确 probe argv 标记匹配的 PID/state，不输出无关进程的参数或环境。原始与恢复完成后，主代理另一次只读检查也没有发现标记活进程。

本阶段每次 3 次实际 Icarus **预处理**和 3 次实际 Python probe，native 仿真、模型调用、方法任务均为 0。恢复重复相同六类检查，不增加样本。2 秒仅是这个基础设施测试的超时参数，没有改动 C2 的 native 90 秒运行预算。C1 token ledger 也未新增模型授权。

## 冻结输入与恢复

工作根 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/agent/s2-boundaries-c3/`。先登记 `preregister.md`，再固定三个源码探针、namespace probe、run/prepare 脚本和完整 C2 输入副本；所有 bytes 在实际测试前由 plan 锁定。

原始包 193 文件，恢复补包 45 文件；第二份 `/tmp/tehm-r5-s2-boundaries-rl4oz357/`。实际恢复隐藏原仓库/corpus，只读挂载该副本，重跑全部预处理与 Python probes。规范化 `report.json` 逐字节一致；PID、namespace ID、随机端口、heartbeat 次数、命令路径和耗时分别保留，未宣称这些执行身份相同。

主代理另外校验两份 tar 摘要、tar 每个文件成员、解压全文件清单、恢复目录和公开报告。完整原始拒绝/rc124/errno27 都保留为预期基础设施结果，不被改成硬件错误或新增 oracle-sensitivity 样本。备份仍是同机 `/tmp`，不代表长久托管或异地故障域。

| 工件 | SHA256 |
|---|---|
| preregistration | `7748609a1c2fec78c208f512e59af3916d9765a010e3181ae6ee051f6e1b4c55` |
| frozen plan | `b892a7f55f2a16f97d6a73c85bb9204c94dd29f0276825451ec7b72c6b06c35a` |
| original/recovered normalized report | `1652ae98e33ec44731bef8d5fe6508ed103cbc60759e639506e446e64bc7a110` |
| original archive | `873209f959eb0693a6fbcce0f20c5e247aaa65ff49e3aff3fcec9d6f55424d4f` |
| recovery archive | `5988665d4808d0a8ae19e0bddb8e7a73e668453d894dfe0959d0eef2cac0d313` |
| actual recovery | `cef278bfc980ba242de441350e5e2803afe967ad744b74aff29991b72c17eaf4` |
| seal | `3002eed658c184943d5aafef5aaf0578a66aefc8f60d29702ffd5f3e1cd4d74e` |

既有 3 组 C2 adapter 检查、16 组 C1 检查和 67 项 paper bookkeeping 均再次通过；它们仍不是全仓回归或 Agent 性能数据。

## 主链下一步

这些证据补齐 C2 报告中尚未实测的几类路径、语言及 supervisor 边界，不扩大成任意恶意 HDL、编译器漏洞、同权限并发攻击或完整 Agent sandbox 的认证。无需为了把 guard 表做满继续扩展探针矩阵。

下一项是固定三策略公共 context/prompt 与实际 Memory 获取映射：Legacy 保留历史算法和快照，TEHM 使用合法 TRAIN snapshot，No Memory 保留同样的通用动作能力；不得给模型挂 raw Memory DB、目标答案或私有 TB。provider/broker、真实 token 计数、总预算与模型选择/授权仍待落实。此前最多 6 次/60000 token 的询问没有获答，不视为批准。

冻结 gen5、三份 Memory、F1 读数均保持不变。无 upstream 修改、production、模型调用或 push。R5-7 Agent 与论文规模 R5-8 尚未完成，目标继续开放。
