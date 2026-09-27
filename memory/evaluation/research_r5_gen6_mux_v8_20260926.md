# Gen6 v8 mux DEV 修复与旧入口清理

## 结果及边界

已观察的 drewbabel axis_skid 在预登记的 DEV-only v8 路径下修复成功。
它不是未见目标迁移、Memory 增益或论文 FINAL 样本；旧 gen5/F1 结果不变。
没有新增模型调用、Memory 写入或 Asset authority。

| 输入 | target | preservation | native |
| --- | --- | --- | --- |
| 原故障源 | FAIL_SECOND_DELIVERY | PASS | FAIL_NATIVE |
| source-only 推导候选 | PASS | PASS | PASS |

原生测试：故障源 1599 checks / 76 mismatches；候选 1599 checks / 0 mismatches。
这是一个 DEV 任务，不是 1599 个独立实验样本。原运行与独立解压副本各执行
6 次 oracle（故障/候选各三项），判定矩阵、候选和动作回执一致。

## 实现

- 实现：`memory/tehm/evaluation/research_r5_skid_binding_v8.py`。
- 检查：同目录 `research_r5_skid_binding_v8_checks.py`，12 组通过。
- 完整活动模块 grammar 推导角色、capture/control/mux 关系和唯一 RHS；
  不依赖模块名称、固定修改坐标、正确源、私有测试或其他臂结果。
- 当前严格限定 WIDTH=8、显式空宏上下文、两进程 valid-bit/mux 结构。
  FORMAL 保持关闭；inactive FORMAL 和 default_nettype 原样保留。
- 唯一修改为输出 payload mux 的 buffered 分支由 live input 改为 captured slot；
  其余源字节不变，sideband 不修改。绑定/动作只声明句法范围，功能由私有 oracle 判定。
- source-only worker 在禁网隔离空间中运行，不挂载私有 oracle、正确 RTL、
  完整 Memory、主工作树或工具链；可信 evaluator 单独挂载私有测试和工具链。
- 不替换 v7，也未注册为可检索/promoted 的 Memory 资产。

故障 SHA256：`764a18706f86f57a40bcede1eb3805e393c48585a48c52fe1f16d23060242829`。
候选 SHA256：`ebe739d7287bf59a133fe113fbb4b454447212c2c4df1f3629cb7bfd115c190a`。

## 可恢复证据

证据根：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/gen6-mux-skid-v8-r1`。
包含 preregister、单一 run.py 入口、worker、冻结代码/input、原始/恢复日志及 receipt。

- receipt SHA256：`89f24de6b993376609622253c99aeeb2a2d74bac1060c8e62308908c962fc51c`。
- 独立副本：`/data1/zhangdy/.cache/tmp/tehm-r5-v8-mux-dev-lr5s_l37`。
- archive SHA256：`4d8a6c15b18b0961cadd22fd488079b66432f3614f4de253e0784dc860f6e954`。
- 副本恢复实际重跑 source-only 检查、动作和六次 oracle；不是仅对摘要。
- 同机临时备份，不是异地持久备份。恢复仍依赖主机只读 OSS CAD toolchain；
  lock 固定 iverilog/vvp 摘要，未声称打包整套工具链或跨主机 hermetic 恢复。

## 按用户要求退役旧脚本

从主工作树删除以下 6 个已提交、无未提交修改的历史入口，共 3374 行：

- `memory/tehm/evaluation/research_r5_train_m0.py`
- `memory/tehm/evaluation/research_r5_train_m0_v2.py`
- `memory/tehm/evaluation/research_r5_train_m0_v3.py`
- `memory/tehm/evaluation/research_r5_train_m0_v4.py`
- `memory/evaluation/research_r5_gen4_nontarget_control.py`
- `memory/evaluation/research_r5_gen4_nontarget_audit.py`

最新 TRAIN Memory 构建入口保留 `research_r5_train_m0_v5`。
删除前扫描仓库 Python、shell、JSON/YAML/TOML 自动化引用，除这六个文件内部引用外
未发现调用。历史文档中的旧命令保留为历史记录，不再是当前工作树入口；复现旧代次
应使用冻结软件副本或 Git 历史，而不是用新软件覆盖旧代次。

恢复方式：从清理前提交 `5fd4622` 提取上述精确路径，或解压
`/data1/zhangdy/.cache/tmp/tehm-obsolete-r5-CS1NKo/retired-scripts.tar.gz`
到一个新的恢复目录。归档 SHA256：
`839f5ae4a3fd920aa4cdefb446cc73e5eda2617c6ced84b114320cf6325ec0d3`。
删除前 tar compare 已确认归档内容与工作树一致。

旧编号不等于废弃：v7 仍依赖较早的 binder、rollback、adapter 和 authority 模块；
这些及其回归检查保留。没有删除历史负结果、冻结证据、外部 corpus 或 `_r5_pilot`。

删除后：controller/provider 22 项离线测试及 v8 的 12 项检查通过；
`research_pilot.py --help` 和最新 `research_r5_train_m0_v5 --help` 通过。
冻结 gen5 工作树保持干净，三份 gen5 Memory SHA256 与清理前一致。
这些是限定回归检查，不宣称完整仓库所有旧流程均重新运行。

## 下一步

需要合法 TRAIN authority、新代冻结和真正未见目标，才能检验 answer-free transfer
与 ΔMemory attribution。不能把本次观察后开发的 DEV 正例计为此证据。
R5 主目标仍开放；不新增 API 调用，也不自动推送。
