# R5 来源预审 B2：新增 GAXI scope 的原生基线

## 本轮结论

依 R5 §7.4，在 B1 的两项同源封装排除后，定向获取一个新的公开候选：[sean-galloway/RTLDesignSherpa](https://github.com/sean-galloway/RTLDesignSherpa)。原生同步 `gaxi_skid_buffer` 的 **一个配置、一个功能测试基线通过**，但错误检出、私有 target/preservation 与最终来源分组尚未完成；没有 TEHM 绑定或修复结果。

原始仓库位于 `/data1/zhangdy/RTL/RTL_testbench/sean-galloway/RTLDesignSherpa`，采用固定 commit 的浅层、相关目录 sparse checkout，**不是全仓库历史/所有组件的完整获取或验证**。本批只有 1 个预登记候选，没有第二候选或自动替换；此前 20 仓库登记和 B1 的原始分母不重写。

| 项目 | 实际状态 |
|---|---|
| 源码版本 | `66e4e1b044f79be2c66fbd466c8918ed15e343c6`，MIT，原始 checkout 无修改 |
| DUT 内容摘要 | `0b6ca353d3d24669a7411d53e377281d2837ae66d21eb887c2e6bcb3179be57a` |
| 范围 | 同步 GAXI；`DATA_WIDTH=8, DEPTH=2`，10 ns 时钟，seed 1729，`gate` 场景；不是 CDC 或整套 AMBA 回归 |
| 原生入口 | `test_gaxi_skid_buffer[8-2-10-gate]` → `gaxi_skid_buffer_test`；pytest 和 cocotb XML 各恰好 1 项 PASS |
| 测量 | 3 组各 8 个数据，加 15 个连续数据；VCD 冷审计：39 入/39 出，44 个输出阻塞周期、7 次阻塞后的输出握手、0 数据不匹配、0 期末积压 |
| 实验分母 | 1 个候选、1 个配置；恢复不是新样本；新增 repair task = 0 |
| 方法状态 | gen5 方法 `2ce921a`、v7 binder/action、Memory report `f31072bf...` 均未修改；binder/model 调用均 0 |

## 执行与故障保留

预登记发生于首次检查该候选 RTL/TB 内容之前；实际 native-plan 又在首次功能执行前固定源文件、包、工具字节摘要、完整入口、参数、种子、300 秒上限和验收条件。53 个上游文件逐字节对照固定 Git 对象。按上游 filelist 保留 `counter_bin` 和 reset include，即使 top 不实例化计数器；不将额外编译文件算成测试或新来源。

依赖单独安装在 evaluator 目录，不改全局 Python。使用上游要求的 cocotb 1.9.2、cocotb-test 0.2.5、cocotb-framework 0.6.5 等最小导入闭包，共 30 个 distribution；该 framework 来自同一作者的 RTLDesignSherpa-DV，不能算作第二个 RTL 来源。包内 `__version__` 仍标为 0.6.1，因此身份以 distribution metadata 和完整字节摘要为准，不以该字符串替代版本锁。WaveDrom 非功能测试没有执行，但其无条件导入的 OR-Tools 依赖保留。

隔离 runner 只挂载只读 source/pydeps/toolchain 和可写输出，不挂载原始 corpus、TEHM Memory 或 Git，不联网。选择一个精确原生 pytest node，不使用上游 Make 全回归/自动重试目标；每次全新 build，无需删除旧结果。DUT、TB、checker 语义未改。

1. `collection-r1` 正确收集 1 项测试，不算功能执行。
2. `native-clean-r1`：编译成功，但 0 ns 时 rich 日志遇到 ASCII `UnicodeEncodeError`，进程 rc=1；保留为基础设施失败，不报 RTL 语义失败。
3. `native-clean-r2`：另行登记仅增加 `PYTHONIOENCODING=utf-8`；同源码、测试、参数、工具和种子，冷启动 rc=0、原生功能 PASS。
4. 原 runner 只在 logs 查 XML，故 r2 receipt 保守地写为 `UNQUALIFIED`。实际 cocotb-test 0.2.5 会覆盖 `extra_env` 中的 XML 路径，将结果写到 sim_build 的随机名 `*_results.xml`。独立冷审计使用实际 XML、原生最终成功行和退出码，得到 baseline PASS。**旧 receipt 不改写；只修复证据定位，没有放宽验收条件。**

冷审计拒绝 8 项验收篡改，包括非零 rc、错误配置、错误 seed、以 WaveDrom 冒充功能测试、有效 XML 内 FAIL/SKIP、缺最终成功行、额外 testcase。初版两项 XML 反例格式不合法，随后改为合法的 failure/skipped 元素重新运行；两次 audit JSON 相同，保留两个文件。VCD 队列核对是补充审计，不替代原生 checker，也不证明尚未运行的 mutation sensitivity。

## 来源边界

已对照 3 个 TRAIN 叶节点及 PULP/SkillSurf/ychud/drewbabel/abarajithan 共 8 个明确历史参考：DUT 既不逐字节相同，也不在仅去除注释/空白后相同。静态检查显示 selected top 的存储是本模块的参数化 shift-array，无实例化其他机制叶节点；使用本仓库 reset macro。GitHub metadata 报告非 fork。

这些检查**不证明独立作者来源**，也不是对全部历史 DEV、公共 RTL 的全量复制检测。状态保持 `PROVISIONAL_NOT_CERTIFIED`，后续仍需来源历史/聚类复核。不能因为 owner、文件名、commit 或 hash 不同就宣称新的独立统计组；资格审查也没有用于调优冻结方法。

## 证据与恢复

原始证据根：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/paper/final-source-screen-b2/`。脚本与私有原始结果不进入 TEHM Git。

- 来源预登记 SHA256：`8082147858a97e65bd7012f0a41723fabfea359b08c03e6999221c73edf58183`。
- native-plan SHA256：`312e94cfc9debf85a9435e6d8f71156cfde04425172a0800dced2bb815b1444f`。
- source-screen SHA256：`ec7aaf025f4c53e313a562f9bcf7c4b52de7c8ff1c81999c1a81c24176db2f7b`。
- r2 原始 receipt SHA256：`b0262c8b0d4464146cd0027fbacfd25153f1c39ca0e39cd37556cb047a8ce029`。
- r2 冷审计 SHA256：`a50c46a6e2fa5de9e56f8e3fb88a3f4305e282d05bff248bbb0fa57469a682e1`。
- 5,570 文件封存包 SHA256：`c5c37998bcc987dbae4e1ea8fb2c818b0a907acb930c39f258776ec745d26d4b`。

匹配归档位于 `_r5_pilot/archive/final-source-screen-b2/` 和 `/tmp/tehm-r5-source-b2-23l5svn9/`；提取后全部文件摘要一致。恢复实际重跑了同一原生功能测试及冷审计，而非只读取旧 PASS，VCD 全文件逐字节相同。恢复时原始 corpus 与主仓库均隐藏，网络隔离；Python、编译器与 OSS CAD 仍是字节固定的系统依赖。恢复结果也有两个匹配副本。设备 2081/2050 是同机不同文件系统，`/tmp` 不是异地长期保管，未授权清理。

## 下一步门禁

保持这个预登记配置，不依据 TEHM 支持或正结果换候选。下一步先补来源历史审查，再另行登记 evaluator-private 的 payload 故障、clean/fault target 与 preservation 资格试验；必须实际观察 clean PASS、fault target FAIL 和 preservation PASS，才考虑冻结新任务及三态调用。原生基线 PASS 不能替代这些门禁。

`final_test_ready=false`，最终任务清单与统计样本依据仍未冻结。没有新 Memory、目标驱动方法修改、模型/API 花费、production promotion、上游修改或 push。
