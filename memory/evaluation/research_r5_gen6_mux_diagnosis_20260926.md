# Gen6 开发入口：外部负例的三层拒绝诊断

用户明确当前优先获得正向修复，不先设置确认性提升百分点。该优先级不授权隐藏负结果、放宽 oracle、追加模型预算或把旧样本重标为未见。本次据 R5 §14/18 登记：已观察的 drewbabel gen5 PILOT fault 进入未来 gen6 DEV 历史；旧 gen5 PILOT 身份及负结果保持不变。F1 不进入此开发输入。

## 实测结论

**仅放宽文件前后指令检查不会让旧 binder 支持该目标。** 固定 v7 对同一已观察 buggy source 的三个预登记诊断输入依次拒绝：

| 诊断输入 | 实际拒绝 |
|---|---|
| 原始源，SHA `764a1870…` | `v6_delegate:code_outside_unique_module` |
| 仅在诊断文本中去除 `default_nettype` 和单个 FORMAL 块 | `v6_delegate:width_parameter_contract_missing` |
| 再仅在诊断文本中去除 WIDTH 声明的 `int` | `v6_delegate:state_or_handshake_witness_missing` |

全部状态 `UNSUPPORTED`，没有调用 apply/action、没有生成 candidate、没有编译/仿真或模型调用。两项文本归一化只是暴露下一拒绝层，不是可执行预处理器、等价性证明、修复补丁或结果救援。

源码和既有 v6 分支直接对照可见：目标使用两个时序块、暂存 valid bit、输入/输出握手关系及 payload mux；既有 WIDTH=8 分支要求特定 EMPTY/PARTIAL/FULL 状态机及精确关系。首个 lexical 错误遮住了后续参数语法和结构不匹配。不能把最终结构拒绝描述为已证明该机制语义上不可修复。

## 边界与恢复

工作根：`_r5_pilot/dev/gen6-mux-skid-diagnosis-r1/`，先有 `preregister.md`，再运行固定三项诊断。binder 进程仅挂载冻结代码、buggy 源、诊断入口和系统库，禁网，不挂载 clean 源、私有测试、Memory DB 或 F1。旧 v7 代码未修改。

原运行与第二副本实际隔离重放得到逐字节一致的 result JSON，输入代码/源码清单前后不变。第二副本在 `/data1/zhangdy/.cache/tmp/tehm-r5-gen6-diagnosis-ngs8ejdc/`，同机临时存储而非异机灾备。恢复回执 SHA256 `18d67a3b3ef14024648339e452e2146508f22c3e2358ca741766bda776517318`。入口 `run.py`、`diagnose.py` 和完整清单保存在该包及第二副本。

本次是拒绝层诊断，不是新 binder 已实现或正向修复结果。新增方法任务、FINAL 样本、真实调用、Memory 写入均为 0。旧 gen5/F1 和三份 Memory 的身份不改变。

## 下一步的最小开发合约

新版本另行登记 mux-based skid 结构支持，不通过删除旧 guard 冒充兼容修复。必须明确公共宏/参数、唯一暂存捕获、valid/ready 方程、输出 mux 与 sideband 关系、唯一写入和源码摘要；不接收 private TB、gold 或 mutation 坐标。分析时忽略的 formal 区块必须有明确 inactive 前提，实际输出保留未修改字节，不能把本次诊断用删除过程直接用作生产动作。

先做 source-only 正/负/歧义/篡改/幂等开发检查和真实 DEV oracle，再按新代次建立 TRAIN 权威、冻结共同代码与 Memory，选取此前未用于开发的新目标。当前 drewbabel DEV 上将来即便 PASS，也只报告开发验证；独立 transfer 必须另有目标。Memory 归因比较和 transform-only 必须共享新 primitive，不把新增软件能力全部归因给 Memory。
