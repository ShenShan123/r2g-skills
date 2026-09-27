# R5 S2 C5：共享 prompt、动作桥接与 source-only 执行

承接 C4 的真实 Memory 检索，按 Revision5 §10.4、§11、§13–14、§19 接通相同动作能力、受控资产别名和私有 evaluator。**三个策略的通用变换及 TEHM 资产动作都生成同一候选，在已观察 DEV scope 下通过登记义务；这不是 Memory 独有收益，也不是 Agent 比较。** 无真实模型、在线学习、新 repair task 或 FINAL 数据。

## 新组件与权限边界

`tehm/evaluation/research_r5_s2_actions.py` 定义相同 system prompt 和 v2 proposal schema：`schema/base_digest/action/arguments`，全部字段精确检查。四类动作对所有策略使用同一入口：

| 动作 | 模型可提交的参数 | 执行语义 |
|---|---|---|
| no_action | 空对象 | 原源不变，不授予成功 |
| replace_sources | 声明文件的完整替换文本 | 复用 C1 路径、大小、重复和源码摘要检查 |
| transform_v7 | 空对象 | 所有策略均可调用同一个冻结 source-only primitive，不需要 Memory |
| apply_memory_asset | 仅 `memory_asset_1` 别名 | 必须有匹配的真实 C4 私有选择回执；不允许模型提交 payload、slots 或 verdict |

公共 task/spec/feedback 与 prompt 相同；只有 C4 的 Memory 内容不同。task 已公开指定研究机制，不宣称盲定位。资产桥接检查公共包摘要、task 摘要、策略、bundle、selected asset ID 和精确模板，并从当前源码/参数重推 v7 payload。最小 worker 请求不含私有回执、反馈、知识记录或测试路径；worker 再推导一次 payload 后使用实际 v7 action 执行。

私有 authority 由可信 runner 从 C4 经恢复核验的固定文件导入；digest 是身份核验，不是针对恶意 runner 的签名认证。本阶段没有再查询 Memory，不把回执消费描述成新学习或新检索。C4 private receipts 不进入 model prompt。source-only worker 只挂载 Python 代码、buggy source、最小 action request 和新输出目录，原 corpus、Memory 与 private TB 不可见。

拒绝/不支持不等于功能成功；action receipt 的 functional_verdict 始终 NOT_EVALUATED。功能判定仍由未修改的 C2 native adapter 返回，再由 C1 投影固定枚举。旧 C1 proposal schema 保持不变，新桥接显式使用 v2，不静默改变冻结接口。

## 预登记执行与实际结果

`_r5_pilot/agent/s2-actions-c5/preregister.md` 在运行前登记四次 native schedule：每策略一次 generic transform，再一次 TEHM asset transform。输入只复制 C4 的 buggy source、真实公共/私有回执、冻结 Python 代码和 C2 test/dependency closure，**未复制已修好的 DEV 源作为动作输入**。C4 与 C5 都是已观察 DEV，不重标 source-disjoint 或 unseen。

| 策略／动作 | 候选改变 | target / preservation / native |
|---|---|---|
| No Persistent Memory / transform_v7 | CHANGED | PASS / PASS / PASS |
| Legacy / transform_v7 | CHANGED | PASS / PASS / PASS |
| TEHM / transform_v7 | CHANGED | PASS / PASS / PASS |
| TEHM / apply_memory_asset | CHANGED | PASS / PASS / PASS |

四个候选 SHA 均为 `652723dccd678e33d23547086b3efae819a9060ff4897b1391331beaca5e7253`。这是实际 source-only 变换生成，未从已知 candidate 文件读取。原运行 native wall 合计约 6.18 秒；另有动作、预处理、审计和恢复开销。每臂 C1 ledger 先记录一次 8000+2000 的**离线预算预留**，它不是模型 usage 或调用授权。没有因为 TEHM 有资产而删除 No Memory 的同一 primitive。

6 组动作检查通过：共享 prompt/catalog、generic/asset 一致候选、精确 proposal/重复 key、缺 Memory/伪 alias/回执篡改、worker payload/source 复核、合法 source-only 拒绝及通用编辑/no-action。另复跑 16 项 C1、3 项 C2、67 项 paper bookkeeping 均通过。这些不是 repair/transfer 的新增统计样本。

## 原始审计、第二副本和真实恢复

独立 raw audit 检查实际 XML 的两项 test IDs、native 汇总和退出码、预处理与 VVP 摘要、编译路径、候选源码、四条独立 ledger hash chains、worker 精确只读挂载及共享 prompt。未依赖模型声明或只看进程 rc。四次结果完整保留，没有选择性重试。

第二份 `/tmp/tehm-r5-s2-actions-0z_49_38/` 保存完整输入和原执行；从该副本、原根隐藏的环境重新运行全部 6 组动作检查及四次实际 native 评估。候选、动作回执、公共 feedback、规范化 raw audit 与 report 均复现；不要求地址相关 VVP 字节一致。原/恢复两边的 8 次 native 均被核验，仍只有一个已知 DEV calibration scope，不增加方法分母。完整归档和恢复归档的精确文件集、tar 内容/解压字节、工具锁、两个副本及回执又经单独只读检查。恢复原始产物也复制回工作根。

| 工件 | SHA256 |
|---|---|
| actions module | `88cecf31079e23f61858b9c3a43e217cfe934c6107c746d003d83147f443d28c` |
| preregistration | `5fc2d067029a1bd322db858c1b5207a6a78e6a2e234a1d4a306bfea12cd51f8d` |
| input plan | `97c5bcc93149f148f6e80deade8bfdae8e871b9ad84d3135219abf063b0901ea` |
| raw audit / reproduced audit | `9b50b9efcc547975d4591871ebf1e6311b5555098646adf5a32e5d9f1235f7e6` |
| original archive | `a0228345062b3b064fd31f13855c9b204d084e79789443b6f9f29905832454f6` |
| recovery archive | `31bcebfc375d6708c399c84110a826ac31b0a83e88a510955be06fb6fbbcb693` |
| recovery receipt | `3cbc08f159091fee043a6f125e5c2f4f94758e8818fca53e96395ed8af41b6e7` |
| seal | `852b8a75afbd23c11a1b29e5ca9063168189a7ec8350a6465ea9472e3d565b3d` |

副本仍是同机易失 `/tmp`，不声称长期托管或全机可移植；系统工具为显式外部依赖。没有改冻结 gen5/v7、C1/C2/C4、TRAIN Memory、上游设计或 F1。

## 剩余主链

C5 完成的是 DEV action broker 与固定 prompt，不是调用真实模型的完整 controller。还需连接 provider response/usage、可信 token 计量、campaign 级跨臂预算和错误终态；确认具体 provider/model 和固定调用/token 授权后，才启动真实 DEV Agent 比较。当前单次动作入口不宣称多轮更新后的检索 readiness。论文规模 R5-8 的正式抽样/来源分组/样本依据仍开放。无 push；不以本次全 PASS 替代后续独立 corpus 实验，也无需再扩张这组 DEV 验证矩阵。
