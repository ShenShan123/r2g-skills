# R5 gen6 C1 原生基线资格记录（2026-09-27）

本记录继续预登记的 C1 `Louis-DR/OmniCores-BuildingBlocks::valid_ready_skid_buffer`，仅做来源与 clean native baseline 审查。状态为 `CLEAN_PASS_ONLY`；尚无 fault-sensitivity、合格构造缺陷、三视图执行或独立迁移结果。

## 来源与执行闭包

- 上游：`https://github.com/Louis-DR/OmniCores-BuildingBlocks`，clone HEAD `6a6f144f9a786f63de1ec700c97e3f96aff11bc7`，`master...origin/master` clean；无 `.gitmodules` 或 `.gitattributes`，`git submodule status` 为空；仓库根目录含 MIT `LICENSE`。
- 原生单元：`sources/data/valid_ready/skid_buffer/`；顶层 `valid_ready_skid_buffer__testbench`。testbench 的设计 filelist 实际编译该 wrapper `valid_ready_skid_buffer.v` 和依赖 `../../access_enable/skid_buffer/skid_buffer.v`；测试 filelist 为 `valid_ready_skid_buffer.testbench.sv`，include 为 `sources/common/`。真实数据寄存器位于底层双入口缓冲，不在 wrapper。
- SHA256，依次为 wrapper `d72013217e574f8ff3bd4da8ca28343ebe1c87a1d1c85cffcc41a2956564cdb6`、底层缓冲 `d97e9e6abcf52c2e154175ec48d30549cb9382ea08c8693dc68d404a59085b61`、testbench `05c2125972ce098c60c039fa23abe1f258eb043e6aa3904d940ae8e69e1c16cd`、`random.svh` `e9eb4502156bac5b910d07070c1592fc571664b38311bc08f3d31ceed55770bd`、`boolean.svh` `9e377256193e9205569b57da956ab5295a45d4e7ee6bd60d4040e8aabe2e9631`、`constants.svh` `8107e132f7b4a8f262cdfe1fb0a8ca94245ee1b53ac2df31169e7234ccc8a9da`。

## 原生基线结果与边界

- 按仓库 `common.mk` 的 Icarus 标志尝试隔离编译；本机 `/usr/bin/iverilog` 在 `sources/common/constants.svh:9` 的 SystemVerilog `byte unsigned` 声明处语法拒绝，未形成 Icarus verdict。此为工具兼容失败，不是 RTL 功能 FAIL。
- 使用已有的 Verilator 5.038，保留 `--binary --timing --assert --trace --timescale 1ns/1ns -Wno-fatal +define+SIMULATOR_NO_BOOL`，三份 filelist 源与 `+incdir+sources/common`，在 `_r5_pilot/qualification/gen6-c1-native-r1/obj` 独立编译成功（退出 0，存在宽度警告）。随后在同一隔离目录运行二进制，退出 0；原始日志依次出现四个检查：writing to full、reading to empty、back-to-back transfers、random stimulus，最后 `$finish`，未出现 assertion error 或 timeout。日志 SHA256 `cb6172797f54a50eab3eb2ac90a918630fbfefab0d55b038b96d7c89e6889c71`，位置为 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/qualification/gen6-c1-native-r1/baseline.log`。
- 上游 clone 保持 clean，所有构建输出仅写入 `_r5_pilot`。当前 PASS 是单次 clean native 配置，不是负控判定力通过；随机检查的覆盖强度也尚未由 mutation 审计证实。
- 结构初看为 wrapper 加双入口交替读写缓冲，而非已训练的单 temporary-payload mux。来源 owner 与 TRAIN 不同，但机制叶独立性和 v8 binder 可迁移性均未认证；不能因为 clean PASS 就把它塞入目标修复分母。

下一步应先冻结一个合法、对应背压后 payload 义务的 fault probe 及 target/preservation 划分，在隔离副本验证原生 FAIL／保持 PASS 与实际 elaboration；若不成立，按预登记保留 `UNQUALIFIED` 而不补位或调 binder。未发生模型调用或 Memory 更新。
