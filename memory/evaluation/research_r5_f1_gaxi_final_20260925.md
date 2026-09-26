# R5 F1：冻结 gen5 在 GAXI 上的首批描述性 FINAL_TEST

控制文档为 Revision5（SHA256 `61555b78ade7aba886c8076354c4f48097e24fc291a8f04de37acaf3c0c326a6`）。这是新的、方法冻结后注册的有界 FINAL_TEST，不是把旧 drewbabel pilot 重命名，也不是论文规模实验完成声明。

## 结果及适用范围

一个外部构造故障任务 `f1_0001`，一个同 DUT 健康 counterpart，四个视图共 **8 次候选执行、24 次实际 oracle 执行**，全部完成，UNKNOWN 为 0。四个视图的 VerifiedRepair@B 均为 **0/1**；所有候选均未改动源码。M+ 相对 M−、Mremove、transform-only 的配对修复指标差均为 0，**未观察到跨来源迁移或 ΔMemory 修复增益**。

| 视图 | 故障上的路由 / 选择 | 故障 target / preservation / native | 修复 | 健康 counterpart |
|---|---|---|---|---|
| M− | NO_SKILL / NO_SKILL | FAIL / PASS / FAIL | 0/1 | 三项 PASS，无改动 |
| M+ | CONSIDER / ABSTAIN | FAIL / PASS / FAIL | 0/1 | 三项 PASS，无改动 |
| Mremove | NO_SKILL / NO_SKILL | FAIL / PASS / FAIL | 0/1 | 三项 PASS，无改动 |
| transform-only | BYPASS_MEMORY / DIRECT_PRIMITIVE，binding UNSUPPORTED | FAIL / PASS / FAIL | 0/1 | 三项 PASS，无改动 |

M+ 的真实拒绝链为 `source_binding_rejected:skid v7 source binding rejected: v6_delegate:unsupported_or_extra_public_parameters`；transform-only 命中相同参数契约守卫。本次公开参数为实际的 `DATA_WIDTH=8, DEPTH=2`，未在观察拒绝后删除 DEPTH、增添 WIDTH 别名或修改 binder。此结果说明固定输入契约下没有动作，不证明所有 GAXI 结构都不受支持，也不构成 binding recall 或最优拒绝证明。

健康 counterpart 单列为一个同 DUT 健康对照输入、四次视图执行，观测到 0 次错误改动；不能与故障任务合并为五个任务，更不能据此估计总体安全率。此前不同设计的健康 non-target 控制保留为旧证据，本批没有重计。恢复执行也不新增统计样本。

## 冻结、来源与暴露边界

实验目录：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/final/rds-gaxi-gen5-f1/`。

1. 保留 B1 的两项重复机制排除历史。B2 固定 `sean-galloway/RTLDesignSherpa@66e4e1b044f79be2c66fbd466c8918ed15e343c6` 的同步 `gaxi_skid_buffer`，8-bit/depth-2、10 ns、seed 1729。构造故障和 private target/preservation/native 资格见 [B2 资格报告](research_r5_rds_gaxi_qualification_20260925.md)，不是上游天然缺陷。
2. `exposure.py` 从原始保存输入重核 431 项关键文件，包括 344 个冻结方法源码、TRAIN 证据身份、三份 Memory bundle、B1 排除、B2 历史关系和六项资格结果；实际 TRAIN authority 重放由运行时 cold verifier 完成。方法 epoch 先于 B2 注册及 private qualification。来源分组为 `observed_distinct_implementation_closure` / `rds_gaxi_shift_array`，仅相对于已审查八个既有机制叶节点及保存的历史/编译闭包；不认证独立作者、全部公开源码唯一性或模型预训练未见。
3. evaluator 已见 clean/fault/private tests；注册生命周期记录支持方法此前未在 B2 上运行，但没有完整 same-UID 访问遥测或外部可信时间戳。不能称所有参与者双盲。方法冻结后的 evaluator-private qualification 不修改 gen5 方法或 Memory；source review 本身不自动授予 FINAL_TEST 权限。
4. 在首次 B2 方法调用前，单独写入 F1 preregistration、八槽 attempt registry、opaque source input、public context 和 `freeze.json`，明确只含一个有界描述性任务，不按 TEHM 结果补选替代任务。旧通用 methods-plan 的 `final_tasks=[]` 保留为历史快照，未回改；F1 单独注册不把旧 broad `final_test_ready=false` 变成论文规模许可。
5. gen5 方法 HEAD 固定为 `2ce921a599c406ab63331561a182d6f4f1bef8cb`，consumer 是旧 gen5 字节相同副本。Memory canonical report digest 为 `sha256:f31072bf2c233974c9fbbf4bb4c30539942f07a57a5b6b7e9022611e90f3206b`（不是 JSON 文件字节哈希）。每个 Memory arm 实际 fresh read-only SQLite→RAM reload，M+ 使用原 TRAIN scoped replay；cold verifier 检查原 145-row/20-table 增量及 Mremove 重建。前后 SQLite 无漂移，没有目标学习或新 Memory。
6. 所有八份候选先生成完，再执行任何 private candidate oracle。consumer 只获得一份源码、原 mechanism-guided 公开契约和经清洗的 action handoff；不挂 clean 对照、private TB、原 corpus、Memory DB、其他 arm 或网络。此 answer-free 边界是已审计的受控确定性 consumer，不是自动诊断或无限制 Agent。

每槽至多一个候选，consumer 60 秒，每个 oracle launcher 总上限 300 秒，内部命令各自上限 300 秒（非可相加的 600 秒），编译 `-j2`，没有重试。故障/健康输入分别为 `525093befa72a291b7899803c42fee79258c8f8aeaab42295e1c52504100095e` / `0b6ca353d3d24669a7411d53e377281d2837ae66d21eb887c2e6bcb3179be57a`。

## 原始审计、分析修正与成本

执行前 candidate oracle adapter 用六份既有 qualification raw result 加 24 个合成反例检查（30/30），只泛化候选功能 FAIL 的判读，不更改测试刺激或成功义务。mock-only runner 五项检查覆盖八槽顺序、consumer/route/shared-cold/partial-oracle 失败分母；不运行真实 binder 或仿真。另有无目标输入的隔离 import preflight。

实际运行后，独立 auditor 核对原始 route/select/handoff、candidate、launcher/mount/namespace 和文件清单，从 command、compile log、XML、summary/markers 重判全部八槽、24 个 oracle，并校验 VCD 等产物摘要；此处没有重新解析 VCD 时序/握手。原冻结 `audit.py` 首次报告 INVALID：四个 NO_SKILL arm 的一个分析断言误认为输出 memory/no-memory budget 都必须为 1。实际调用输入始终是 `memory_budget=1, no_memory_budget=1`；冻结 router 的既有 NO_SKILL 策略会重分配为 memory 0 / no-memory 2，selector candidate budget 仍是 1。旧 gen5 pilot 也有同样行为，并非本次方法漂移或执行了两个候选。

保留失败 `audit-r1.json` 和冻结 `audit.py`，仅追加 `audit_v2.py`，钉住原 auditor、冻结 router 和旧 authority 原证据，在内存中替换唯一错误预算断言。新审计 `audit-r2.json` valid=true；未修改方法、oracle、freeze、原运行或结果标签。追加检查的 8 个预算篡改和 8 个 receipt RAM 篡改均被拒绝（16/16），3 个合法预算正控通过；丢槽、重复槽、伪造成功/namespace/时序/SQLite 等均保持注册分母。分析修正可见，不以覆盖失败制造一次通过。

`readout.py` 再执行独立 raw audit 并与保存结果严格一致，再调用既有 SHA 锁定计量源码 `research_r5_paper_protocol.py`。四个故障视图分别保留 N=1，健康 counterpart 分开；没有把未动作或拒绝排除。现行 `research_r5_paper_protocol_checks` 此次重跑 67/67 通过；这是合成计量契约验证，不计作真实实验样本或完整仓库回归。

实际记录：八槽 candidate phase 合计 66.659 秒；共享 cold TRAIN verifier 148.773 秒；24 个 oracle launcher 合计 156.174 秒。更内层 oracle command wall time 逐槽保存在 `readout-r1/summary.json`；内外嵌套时钟不相加。未完整计量 acquisition/开发成本，不能记为零。模型/API 调用与 token 均为 0。

## 证据封存与恢复

执行前归档 `_r5_pilot/archive/final-rds-gaxi-gen5-f1/preexecution.tar` 与 `/tmp/tehm-r5-final-f1-z2ohohns/preexecution.tar` SHA256 均为 `48efd6e2affffc012f257867cb56f36ec6c4e60ac215c8aa50089ac56cf67c7c`，737 个外层文件及内部原 gen5/软件 bundle/GAXI 资格包均验证，解压清单相同。其冻结在方法执行之前，未添加 post-run 内容覆盖原包。

本批 post-run 补包和实际隔离恢复均已完成。补包从原执行前封存包重建独立 gen5 Git clone、TRAIN corpus 与 B2 资格闭包，验证 440 个冻结外部 pin，并精确补齐 28 个缺失/旧版本文件；新分析 auditor、失败记录、反例、readout 及其计量源码均保留。补包为独立追加，不覆盖 preexecution.tar；两份 SHA256 均为 `34869b7bfc43fe3567920e042bceb185ff76ac9797db1290869f42d029a726ca`。

恢复使用 `/tmp/tehm-r5-f1-postrun-x7ypbqoj/` 中经校验的第二副本，原 corpus 和主仓库不挂载、无网络，仅保留锁定的系统工具链。归档内 `audit_v2` 重算原八槽审计，输出与 `audit-r2.json` 逐字节一致；随后实际 cold TRAIN verify、fresh M+ load/route/select、原 consumer 和三个 oracle，仅复现原故障 M+ `a_02`。恢复 launcher exit 0、204.51 秒、stderr 空，矩阵仍为 FAIL / PASS / FAIL；handoff、candidate 和三个 scope 的 VCD 均与原臂一致，Memory 及冻结软件前后不变。没有声称重新执行全部八臂，也没有增加方法任务或统计样本。

完整恢复原始输出及依赖补包封存于 `_r5_pilot/archive/final-rds-gaxi-gen5-f1/postrun-recovery-r1/recovery-seal/recovery-evidence.tar.gz`，第二份为 `/tmp/tehm-r5-f1-recovery-evidence-n7tu8btk/recovery-evidence.tar.gz`。两份归档 SHA256 为 `2efd57b0800050c9314cc8a9c720caeebb2c449ed22308e9d086cff72d203db1`，解压后全清单的 219 个外层文件一致；主代理另行只读复核归档、解压清单、原 run 全工件及冻结 pin，通过且未重跑目标。

恢复 receipt SHA256 `3a6cadcd602fb14c5072f38982f5559bf4383feacd90371bbd5306046f90814a`，launcher SHA256 `59b56ac162244cd0e7df3def23c1144a383e77bec560e6452ed82f741570f326`；[封存回执](/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/archive/final-rds-gaxi-gen5-f1/postrun-recovery-r1/recovery-seal/seal.json) SHA256 `48fbb78496c2102290d2cb84d8043ad83529fa3fa2bb994ba07d7210c73b7e9c`。

设备号 2081/2050 只是同机两份存储，`/tmp` 不代表异地或长期持久备份。原生故障执行既有的 secondary Unicode logging error 未被删除；功能 FAIL 根据已记录的 assertion/XML 判定，不把编码异常当作目标故障证据。全部原始失败、审计错误和恢复过程均保留。

关键固定摘要：

| 证据 | SHA256 |
|---|---|
| `freeze.json` | `2e9aa185a10f8b682095fd1c28c9f7febda86a2d5294715f024dc79fe94581ad` |
| `exposure-r1.json` | `20a63ea635f00c153d847794d6d2d2cc0154faaafd4deabd387ba6e8fd923a8a` |
| `run-r1/receipt.json` | `5c830e6f648f56356d30e8d24ac58671ab31fa30fa155be4913bc1497f6279ac` |
| 保留失败 `audit-r1.json` | `1ad11fc8e63a90ffef722bcb12448eb0e654c7e0b1b270a057ff90519baccb08` |
| 分析更正源码 `audit_v2.py` | `60b986c279ffa27598aaa1cb63afbef9a3bc57d81483d6449fda2403db4d0441` |
| `audit-r2.json` | `5c53866b9a43762a4ffa85af2c78adabada9f5877cbb604ec38882b08e7d371e` |
| `audit-checks-r1.json` | `980e6126d1b8988bb336a0d4f981ff149ad83f03fd6afdc0704bfa91e89a97f5` |
| `readout-r1/receipt.json` | `a062e5146fd7efd961513d4f7a0d3c81f931e4c9c0f6c5c940e8c53a489a9af5` |

## 剩余工作与声明限制

F1 是一个已执行且原始审计通过的描述性 FINAL_TEST 任务；不是“没有任何 final task”，也不是论文确认性最终样本已充分。更广泛外部来源候选框架、样本依据及后续冻结仍未完成。F1 现已观察，后续不得当未见数据重新选入、按拒绝结果筛选易例，或加入 TRAIN 后声称同一未变方法。

不继续针对本任务调 binder/参数契约，不把零增益说成正迁移。后续若有方法改变必须另立 generation，并保留 F1 为观察历史。Agent 比较未运行；真实 provider 调用需要新授权及固定 call/token budget。本阶段没有 upstream 修改、production promotion、在线学习、删除、远端 push；R5 总目标仍未完成。
