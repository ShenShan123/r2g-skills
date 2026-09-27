# R5 S2 C4：真实只读 Memory 检索与公共投影

按 Revision5 §10.4、§11、§13–14、§19，承接 C1/C2/C3，完成已有 axis_skid **DEV 接口校准**的三后端真实查询。没有模型调用、候选执行、新 repair task、独立 transfer 或 FINAL 观察；不把资产选中记为功能修复。Frozen gen5、TRAIN Memory 和 F1 不变。

## 映射先登记，检索后执行

`_r5_pilot/agent/s2-memory-c4/preregister.md` 在首次查询前固定 source SHA、公开参数、C2 初始枚举反馈、查询字段、limit=1 和暴露边界。所有策略共享 task/specification/feedback；target 为 backpressure 下 accepted payload/last 序列保持，preservation 只指已登记的 downstream-always-ready 吞吐义务，不外推全面正确性。

新增 `tehm/evaluation/research_r5_s2_memory.py` 是有意锁定该 DEV 输入的映射组件，不是通用 Agent 或最终测试入口。它调用冻结软件的真实 None/Legacy 后端，以及 gen5 的 `route_memory → select_knowledge_grounded_assets`。M+ 从经校验的 bundle 载入 RAM，真实 scoped TRAIN authority replay 未跳过；不执行 learner 或写回 snapshot。

| 后端 | 实际查询／输入 | 实际结果 |
|---|---|---|
| No Persistent Memory | NoneMemoryBackend build_query/retrieve | EMPTY，0 candidates |
| Legacy | 原始三个文件摘要一致；check=rtl_functional、class=payload_transfer、status=failed；symptom `01806f6f0ec7b781` | diagnostic_view=high，真实检索 EMPTY，0 candidates |
| TEHM | 已有 gen5 family/profile/measurement contract；M+ bundle `d4f12cf6…`；公开 source/parameters | CONSIDER → SELECT，1 asset |

Legacy 不是空库、不是被构造为 cold_start 的空 context，也没有修改检索算法。新 RTL vocabulary 可能不在它的历史 symptom 索引内；本结果只适用于登记映射，**不支持“Legacy 不具备 RTL 能力”或 TEHM 相对收益的结论**。没有在看到 EMPTY 后搜索其他签名。若将来真正命中 Legacy recipe，组件保留完整私有结果并返回 REVIEW_REQUIRED，等待专门的 recipe 投影审查，不能默默过滤后宣称零命中。

## 公共内容与未完成的接口

公共输出只有固定枚举、共同 task、opaque alias，以及与冻结 v7 模板逐项相等的 binding template。绑定后的 target action payload、源码 witness、internal asset/knowledge IDs、provenance paths、raw route/selection receipt 均留在私有记录。额外字段、未知枚举、非预登记参数、source 漂移和不支持的模板 fail closed。

Authority 进程只读挂载冻结 TRAIN 验证所需 corpus/software，并禁网；它不是 model filesystem。另行启动的公共消费者只获得公共 JSON、buggy source、检查脚本和系统 Python，不挂载 corpus、Memory 或 evaluator。原运行和恢复各三次消费者均通过，网络 namespace 与宿主不同。这是具体挂载检查，不是任意恶意程序隔离证明。

`context_projection_ready=true` **不等于 agent_ready**；每个包明确 `agent_ready=false`、`action_execution_authorized=false`。typed action broker、共享 generic primitive 调用、完整 prompt、provider/token 计量和全 campaign 预算尚未接入。后续不能把模型自由编辑伪装成已经执行了所选资产，也不能削弱 No Memory 的共同动作能力。当前所有输入是开发时已观察的数据，不能改标 unseen。

## 验证、失败记录与恢复

4 组 C4 synthetic checks 通过，覆盖全部 27 种 verdict 组合、字段/参数/source 拒绝、私有字段不透传和不合法 selection。原执行与实际第二副本重放均运行真实三后端及这 4 组检查；6 份公共/私有 JSON 和 result.json 均逐字节一致。另复跑 16 项 C1、3 项 C2、67 项 paper bookkeeping，均通过。它们是软件/集成检查，不增加统计分母。

首次 runs-r1 因 frozen linked worktree 的 `.git` 指向未挂载主仓库而退出 1，发生于查询之前。完整失败日志保留。runs-r2 使用已有相同 SHA 的 standalone 软件副本；没有为解决此错误挂载主仓库。恢复从已校验的 gen5 archive 重新提取 TRAIN 依赖，并从软件 bundle 独立 clone；新 C4 输入也从第二副本恢复，不依赖原 C4 目录。

工作根 `_r5_pilot/agent/s2-memory-c4/`；第二份 `/tmp/tehm-r5-s2-memory-qtgvkzur/`。原包、恢复包、解压精确文件集、退出状态、输出摘要、公共挂载和原 Legacy 三文件未变经另一次只读核验。恢复产物两边均保存。旧 gen5 archive SHA `66efb3d0…` 是显式 TRAIN 恢复依赖，系统 Python/Git/OSS CAD 安装仍为外部环境依赖；不声称全机可移植。第二份是同机易失 `/tmp`，不是长期或异地托管。

| 工件 | SHA256 |
|---|---|
| mapping module | `faa7f40eebf819610b897a23a6704c0ae53713cea3d6cad2ea0132eb1d18b24f` |
| frozen C4 plan | `cd696db52b454653b26bdf1533ed64e5b6f2487468c1f308f79fe460b22c9499` |
| original archive | `b3e8625054b983f5ba35a488647456e96c3cc840ebf77e20c5fc97a523d4ea32` |
| recovery archive | `91c08f6fc76dc2846130d4d0bca087c83cb1970ba6951379b1b8f46bf5f0a706` |
| actual recovery receipt | `04d6db21af8434c3c267ba949574f005066bed4f005490e530fa58a6a91c88a3` |
| seal | `7015eafc544d765fb1a9cd5c741fe7be96b505b81cd2227677050f0675e03e28` |

下一步是共享 controller 的受限 action broker 与公共 prompt 接线，再完成真实 provider/model 与固定调用/token 预算授权后的 DEV 实验。无 push、production、在线 Memory 更新或 upstream 改动。R5-7 和论文规模的 R5-8 仍未完成；不需要继续扩张本次 probe matrix。
