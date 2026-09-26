# R5 dozecat G2：合法输入下的原版 RTL 有界反例

接续 [G1 输入前提检查](research_r5_dozecat_augmented_oracle_g1_20260926.md)，新登记的 standalone 驱动满足输入 ready/valid 稳定性合约。**未修改的上游 `skid_buffer` 在输出受阻期间改变数据，首次输出握手交付第二笔而非第一笔输入；增强判定 FAIL。** 这是该公开流接口合约下的原版 RTL 有界反例，不是 TEHM 修复或迁移成功。原版不是健康参考，故复用 Q1 fault 的敏感性仍为 UNDETERMINED，不计检出率或 repair 分母。

## 冻结范围与运行

根目录 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/dozecat-legal-driver-g2/`。`preregister.md` 先于驱动创建与执行登记。角色 `DEV_RESEARCH_AUGMENTED_NOT_NATIVE`：不是原生 interconnect 测试，不是 TRAIN，也不能重新命名为未见 FINAL_TEST。上游 dozecat/axi_lib 固定 `f15227300d72e79bc537817c89340a084d3b9072`；私有 clean 叶文件与上游完全一致，SHA256 `d5863d8dd5195f29ba12794106cca31f941ac72e114de5d1f139fb7e7d18c114`。fault 仅复用原 Q1 的 SKID 输出 bit-4 翻转，SHA256 `099926d8f05c1e956601d82c7831f000f3fe4e79b7a12bb0262a22396a1fce0c`，没有新增变异或修复 RTL。

固定 WIDTH=36，数据为 `{32-bit payload, 4-bit strobe}`。两周期 reset 后运行 16 个 active cycles；顺序 offer `0x11111111F`、`0x22222222F`、`0x33333333F`，valid 不依赖 ready，直到真正输入握手后才在下一周期推进。下游 ready 在前四个 active cycles 为 0，此后为 1。无随机种子、适应性激励、错误后提前结束或重试。

每周期低相位设置输入并 eval/dump，保存公共端口 CSV，再上升沿 eval/dump、下降沿 eval/dump；CSV tick 为 `3*cycle+1`，对应 VCD 上升沿前的 `3*cycle` 时间戳。总计 18 个周期、54 个相位。这是人工 trace tick，不是物理纳秒或工作频率声明。audit 对所有公共端口逐相位核对 CSV/VCD 和预登记激励，不读取内部状态来作功能判定。

冻结 G1 的 `evaluate(samples)` 算法原样复用，独立适配新的 scope；16 项 synthetic 检查在实际执行前通过。随后锁定 source/driver/monitor/tool/runner/auditor。Verilator 5.038，`--cc --exe --build --trace -Wall --top-module skid_buffer -GWIDTH=36 -j 2`；每臂 cold compile 限 120 秒、simulation 限 30 秒，零重试。隔离空间只有固定源码/工具/系统依赖只读挂载与新输出目录可写，无主仓库、完整 corpus 或网络。

## 原始观测

| 项目 | clean 原版 | Q1 fault |
|---|---|---|
| 编译 / 模拟退出码 | 0 / 0 | 0 / 0 |
| 输入环境合约 | PASS，无违例 | PASS，无违例 |
| 接受输入 / 输出 | 3 / 2 | 3 / 2 |
| 输出 stall samples / pending 输入 | 3 / 1 | 3 / 1 |
| Payload queue comparison | FAIL | FAIL |
| Strobe 保持 | PASS | PASS |
| 输出阻塞稳定性 | FAIL，tick 16 | FAIL，tick 13、16 |
| Augmented verdict | FAIL | FAIL |

clean 最小见证：输入 A 在 tick 7 接受。tick 13 输出 A 且 valid=1、ready=0；tick 16 仍 valid=1、ready=0，输出却变为 B，期间无输出握手。tick 19 首次输出握手交付 B，而队列期望 A；tick 22 输出 C，而队列期望 B。输入自身在 tick 10→13 的受阻 offer 保持 B 和 valid。`witness.py` 不调用队列 monitor，而从 CSV 独立核对相邻阻塞端口和首次输入/输出，得到同一反例；CSV 已由另一路 raw VCD 审计核对。

fault 的首次输出为 `0x22222223F`，第二次为 `0x33333333F`，但不能据此称 probe 已检出：clean 本身已 FAIL。两臂的 pending=1 仅是有限 trace 未配对计数，不据此声称无限时间 liveness 失败。冻结 G1 字段 `preservation_order=PASS` 实际只表示未观察到“无排队输入却产生输出”，**不表示 payload 顺序正确**；真正队列数据比较已 FAIL。原始字段保留，报告不扩大其含义。

驱动打印 COMPLETE、退出 0 只证明采样完成，不能冒充 native 或 augmented PASS。结论限定为 WIDTH=36、此 reset/stall/offer 合约下的 standalone 缓冲行为，不推广为全部参数或整个 AXI interconnect 的正确性结论。

## 证据、成本与恢复

原始 clean 编译/模拟约 3.820118 / 0.115252 秒，fault 约 3.820364 / 0.064952 秒。新增 2 次原始 build/run 和 2 次实际隔离恢复；复用同一 probe，不增加 probe、方法任务、独立来源或 FINAL 样本。没有模型调用。这些命令耗时不代表全部开发/获取成本。

主封存 `_r5_pilot/archive/dozecat-legal-driver-g2/`，副本 `/tmp/tehm-r5-dozecat-legal-g2-z1lu_p_6/`。原包 218 文件、33 目录，恢复补包外层 88 文件。恢复先重算原始 audit，再从副本实际冷构建运行 clean/fault，增强结果一致，两臂完整 CSV 和 VCD 逐字节一致。主代理另外重算两份 tar SHA、包内文件内容、解压文件及目录清单；核对通过。独立核对命令先遇到宿主 Python 无 `hashlib.file_digest`，改用流式 SHA256 后完成归档检查；随后恢复 CSV 路径漏写 `runs-r1`，查实目录后核对波形和 Memory 成功。两次均为只读核对命令失败，未修改实验、结果或证据包。

| 工件 | SHA256 |
|---|---|
| preregistration | `875c61a8fc94662573a5c2666be21c738d429b93ab385cf58b8c2d8069a4e4ec` |
| frozen plan | `a2af9ba2b809a0e5fd2a64bf79fe2be5909e32d6463ca9295030e1e2cb51ad6f` |
| original audit | `79f9c73fa0bb82e77a791116307f041794ed54a122319b4e33f263f67bb3c086` |
| pointwise witness | `2d9783094e4b8be2fc18971d4f42370fb34a74bfaecb297f13fae5ccaec75f39` |
| original archive | `02f488947ab3aae69cb2d64f2ba982a351a4d2cf25f66122b91425b688701903` |
| recovery archive | `0e4e4974a14c1026f63aeed87079f1c35b2acf8f87bd672f87c05189d95edf51` |
| actual recovery | `c038f40d67990eed88a63308d8c797f05b3172a528e9ddd2e15bf3e819a315b4` |
| final seal | `15127f869b9ba2c33324df6ddda3d00dbe1f944273a764d33885ae8970ef86c7` |

两副本仍在同机设备 2081/2050，`/tmp` 不构成长久托管或异地备份。frozen gen5 `2ce921a599c406ab63331561a182d6f4f1bef8cb` 与上游工作树干净；三份 Memory 摘要与 F1 freeze 一致。现行 67 项计量 conformance 再次通过，不等于 67 次实验或全仓回归。没有 upstream 写入、production、在线学习、清理或 push。

## 主链状态

G2 完成“合法输入后区分环境与 DUT”的诊断，不再为了让此源变绿而修 BFM/DUT 或追加刺激。B3 的两个候选仍未获得该机制的健康、敏感性合格任务；G2 不能救回其 FINAL 准入。冻结方法及 F1 的四视图 0/1 读数不变，尚无正向 answer-free transfer 或 ΔMemory 增益。下一步回到已登记的来源抽样与主链协议收口；同 controller Agent 比较仍需单独固定协议及明确 provider/call/token 预算授权，当前不启动真实调用。论文规模样本依据仍未完成，R5 目标保持开放。
