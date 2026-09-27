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

## 后续：v8 核心 Action／Asset 接线（同日）

新增 `rtl/skid_payload_action_v8.py` 和 `assets/skid_binding_v8.py`，复用原 v7
及 mux-v8 binder，接入原 `apply_rtl_action`、Asset structural binding、
source-copy 与 candidate replay 核验。动作目录升至 `rtl-actions-v0.9`。
显式 `defined_macros` context 走 mux-v8，其他 context 由未修改的 v7 合约判定；
没有 source ID、private oracle 或 gold 驱动的分支，也不做失败后搜索。

新 profile 为 `rtl.skid.payload.v8.dev`；两套旧 binder 的源码未修改。
这是软件扩展，不是 Memory 增量。Asset 可在 RAM 中 draft 注册和静态执行，
但 lifecycle 的纯 gate 与 strict authority 均明确拒绝 v8 promotion，理由为
`v8_raw_train_authority_not_implemented`。已有 v7 TRAIN 授权不自动转移至 v8。
本次没有新的 M0 builder、canonical schema 或持久 Knowledge/Memory。

禁网 worker 只读代码、四份已观察 faulty RTL 和公共 context，不见参考源、
原始 TRAIN receipts、测试、工具链、Git 或 Memory 数据库。同一个 synthetic draft
在 mux、LibSV、axis_register、ZipCPU 四输入上经核心绑定和执行；mux 输出与上节
已实测候选一致，另外三项输出与 v7 一致。这里只报告兼容性，不增加 TRAIN、
方法、独立来源或 FINAL 分母；没有新 native/模型调用，也没有真实检索收益声明。

验证：现有 v8 单一检查入口从 12 项扩为 19 项，包含 RAM draft、目标重绑定、
防篡改、candidate replay、伪造及非 strict promotion 拒绝。另有 v7 Action 18 项、
v7 Asset 29 项、controller/provider/native 解析 25 项通过；371 个 Python 文件语法通过。
第二份解压副本实际禁网重跑 worker，19 项与四候选均通过，完整报告及候选逐字节一致。

首次封装把 LibSV clean SHA 错填为 fault pin，冻结前 assertion 阻止了 worker；
该部分包完整保留为 `failed-preparation-r1`。按原 TRAIN 清单核对修正的只有 expected
pin，输入文件、算法与案例不变；`preparation-r2.md` 在首次 worker 前登记。

证据根：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/gen6-v8-core-r1`。
recovery receipt：`7128d003cac37af7dac1d69ab4a1a36b50df863f317c465093da2c35036e0b24`。
最终 792 文件包含失败封装、原始/恢复运行及回归日志，解压清单一致：

- seal SHA256：`97d51282e02aaffaedf1ff83c75bdbc5bbf45c5b23b73dcfa453d4285d304ada`。
- archive SHA256：`7cd300b332446e8c5cf77eefb8a61249ace0ed78d9203544898cb2cc91f27ede`。
- 独立副本：`/data1/zhangdy/.cache/tmp/tehm-r5-v8-core-138sdsd7`，仍是同机临时备份。

冻结 gen5 保持干净，三份 Memory 摘要不变，无 push。下一步须另登记合法 TRAIN，
在冻结新动作下采集并冷审原始 target/preservation/rollback，接入实际 evidence verifier；
随后才构建新 Memory、冻结新目标并做三态及共享 primitive 对照。R5 尚未完成。

## 后续：合法登记的 mux TRAIN 分量与源回滚

在新执行前将已观察 drewbabel 案例登记为 `RESEARCHER_ASSISTED_TRAIN_REUSED_DEV`；
不再作为后续未见/FINAL 来源，也不改写旧 PILOT/DEV 身份。动作使用冻结的
`70fa5e63bd941b6ae067159e26b49b1005586119` 核心代码及已核验文件清单。
这是一项新 TRAIN 执行，不是将旧 DEV PASS 回执改名。

原始 TRAIN 共 9 项 oracle：fault、core v8 实际生成的 candidate、source rollback，
每臂分别运行 target/preservation/native，结果如下：

| TRAIN 臂 | target | preservation | native |
| --- | --- | --- | --- |
| fault | FAIL_SECOND_DELIVERY | PASS | FAIL_NATIVE |
| candidate | PASS | PASS | PASS |
| rollback | FAIL_SECOND_DELIVERY | PASS | FAIL_NATIVE |

Rollback 在新目录先写入实际候选，再从原始 fault backup 恢复并重新编译运行，
不是复用 fault 臂结果；这不是 Memory Mremove。候选 SHA 与上一 DEV 结果一致。
source-only worker 不挂载私有测试、上游原版、Git、Memory 或网络。原 qualifier、
TB、Makefile 和工具保持不变。新增冷审入口 `research_r5_train_mux` 重推动作，
核对 worker 隔离参数、实际编译输入、命令、测试和日志，独立重算 verdict。

首次 9 项执行完成后，冷审 r1 因错误地把 native scoreboard 的 `$error` 一律拒绝
而退出 1；原始 bundle/auditor/launch/raw logs 完整保留。已有 qualification/DEV 日志
以及固定 TB 的第 104 行均确认这是功能失配标记。r2 只修正冷审器，要求 ERROR 行
逐条符合固定 payload scoreboard 格式、数量与非零失败汇总相符；其他错误仍不可 PASS。
原训练没有重跑；在执行恢复前登记并冻结 r2 overlay，仅 `audit.py` 与 lock 摘要改变。
动作软件和原有 oracle 没有改变，两次结果统一由 r2 冷审。不能声称原 r1 审计成功。

第二份解压副本实际执行预登记的另 9 项恢复 oracle，完整判定、动作、候选及回滚回执
一致；不增加训练来源/任务数。15 项解析反例和 7 项 metadata 篡改检查通过，
19 项既有核心检查通过。未新增模型调用，无 v8 Knowledge/Asset authority 或 Memory 写入。

证据根：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/training/skid-v8-mux-r1`。
第二副本：`/data1/zhangdy/.cache/tmp/tehm-r5-v8-mux-train-5fldlvj4`，同机临时备份。

- recovery receipt：`34129f5f8512bf2c916acea880762b7e0a0162a33942df4f6a33944668128a6d`。
- 最终 945 文件 seal：`3094172a44d7e6f884b32ab9ed334438bfd8676f3bbbcf657aa2cd7d195b4cce`。
- archive：`2cfa8e5084076aa2ffc605a6f325dbc3b539252f370fc6bd67471fd41c6fd10c`。
- r1 auditor：`2450a083fc86ada42281cc796881d4cc7740ddc9de6f00da9be4e67a4db508c8`；
  r2 auditor：`53ce91f3bd20313b8f49d4589c2b83bac2417cd69572b6d6249b3ee0585048e9`。

最终归档包括失败审计、修正版、全部原始/恢复执行和回归日志；解压清单一致。
冻结 gen5 工作树与三份 Memory 不变，无 push。当前只完成一个 TRAIN source component，
不是两个独立 lineage。仍须补齐其他来源的 v8 TRAIN 证据及核心 evidence verifier，
然后构建新 M−/M+/Mremove、冻结新目标并作共享软件的真实迁移/归因；R5 继续进行。
