# 实验四：数据转图能力的独立验证方案

状态：测量工具开发及旧结果回顾性审计。本文不替换旧 v2 冻结协议，不表示新的正式实验已经启动。

## 研究问题

实验四验证：固定的 EDA 数据能否被自动转成实体与连接正确、特征与标签可信、阶段信息不泄漏、可被图学习程序使用的数据集，以及完成这些工作需要多少资源。

主比较保留 R2G 与 GPT、Claude、Qwen 生成并冻结的通用转换器。静态格式合规是接口检查，不是转图正确性的替代指标。不能因为某方法未通过格式检查，就省略其可独立核验的语义结果。

## 最小主实验

1. 输入同一批已完成的 EDA 产物，不在本实验重新跑 ORFS；实验三不是必须的上游依赖。
2. 所有方法获得同一字段语义、单位、阶段可见范围、开发样本及工具权限。名称映射可以通过公开 sidecar 提供，不要求复制 R2G 内部实现。
3. LLM 在开发集内迭代生成转换器；固定 API、Token、执行次数与单次资源上限。冻结后测试阶段不再调用 LLM、不按测试结果修代码。
4. 用独立 oracle 对齐实体、拓扑和数值；另报原生输出接口合规率。
5. 对相同测试输入执行可解释的扰动与小批量加载测试，并分开报告各维度结果。

R2G 开发历史成本与本次运行成本分开叙述；LLM 转换器开发 Token、开发时间、测试转换时间和峰值内存分别报告，不能把一方开发成本与另一方仅执行成本直接当作同一口径排名。

## 评分维度与实现进度

| 维度 | 独立依据 | 当前实现 |
| --- | --- | --- |
| 实体与逻辑连接 | Yosys 原生 Verilog 解析的位、端口和 cell connection | gate/pin/net/IO 身份及三类核心边，含 precision/recall/F1 |
| 物理数值 | OpenDB 读取允许阶段的 DEF 与 LEF | gate/pin/IO 物理位置、die、直接 net bbox/HPWL |
| 标签 | 原始 route DEF、SPEF 和 STA full-path 文本 | routed wirelength、ground cap、canonical data endpoint 最差 setup/hold slack |
| 掩码与跨阶段对齐 | 按实体身份对齐，不依赖行顺序 | 节点形状、有限值掩码、四阶段标签一致性 |
| 阶段信息隔离 | 公开可见性规则及未来输入扰动 | 已实现若干静态检查与单样本最终 slack 扰动执行测试；完整执行级隔离未完成 |
| 鲁棒性 | 不改变语义的原始输入变换 | 已实现并执行注释扰动 canary；通用重命名、乱序及更多设计待补 |
| 可用性与成本 | 相同进程/设备条件 | 旧运行日志保留；新增两设计批加载/一次前后向 CPU smoke，不作为预测精度实验 |

未实现的 Liberty 全字段、pin shape/layer、拆分或改名 net 的线长/电容、RC/timing edge 标签、拥堵数值，不得按正确记分。完整字段总语义状态保持 `NOT_VERIFIED`；七组已实现的核心语义可以逐组评分。oracle 失败不算方法失败；没有适用标签标记 `NOT_APPLICABLE`，身份映射不足标记 `UNASSESSABLE`。

坐标绝对容差暂为 0.001 um，相对容差 1e-5；两位小数 STA 打印值的 slack 绝对容差暂为 0.011 ns。正式前通过合成真值用例固定字段语义与容差，不能看到方法结果后单独放宽。

## 验证评分器本身

- 手工可计算的小图作为真值，不使用 R2G 输出生成真值。
- 故意制造错边、重复身份、越界、单位放大、NaN 掩码伪装、跨阶段标签变化及早期 HPWL 泄漏，确认能检测。
- 一致地重排节点及关联边后应保持原评分。
- 独立输入扰动先经 Yosys 确认逻辑不变，再重新执行四种冻结转换器，检查语义载荷是否不变。此项与“篡改输出测试评分器”严格区分。
- 静态检查通过不等于完整泄漏检查通过；注释扰动通过不等于数值正确。

## 数据与正式启动条件

旧 v2 的 24 个 hidden 设计已经用于本轮审计开发，因此仅作为回顾性证据。若修改转换器或新增带反馈的开发阶段，新确认性测试须从未暴露的设计中按 repository/family 分组选取，并检查源码重合，而非仅换 task ID。

不按 R2G 已知通过、LLM 已知失败筛选测试集。按可用输入、规模和设计 family 预先选取；记录全部排除原因。样本数根据未暴露独立 family 的实际库存确定，不先承诺 36 个。

先在公开开发样本上验证执行链路与合理预算，再一次固定三个模型相同的资源上限。当前不自动扩大到 60 万 Token，也不因旧结果不理想反复付费重跑。

新正式执行前必须完成：核心数值定义消歧、独立覆盖清单及测试、未来输入隔离测试、identity adapter 规范、统一预算与 family 隔离清单。完整字段总分仅在对应 oracle 实现后启用；否则明确使用已验证的核心字段子集，报告完整接口合规作为独立附表。

## 本次已落地的证据

- 独立审计目录：`/home/yangao/r2g_exp4_semantic_audit_20260908`
- 输入扰动目录：`/home/yangao/r2g_exp4_semantic_comment_canary_manifestfix_20260908`。首遍目录保留测试脚本遗漏更新源码 manifest 的诊断，不作为转换器失败。
- 在 audio_i2s 上只加入合法 Verilog 注释，Yosys 原生解析确认逻辑不变：R2G/Qwen 四阶段载荷保持不变；Claude 多出注释里的 `exp4_fake` 单元，四阶段不变性失败；GPT 缺少身份字段，当前适配器无法评估。
- 批加载 smoke 使用不同设计两两配对、全部四阶段；输入含缺失值时统一使用数值加有效掩码编码，只允许三类核心逻辑边消息传递，标签派生边不进入网络。它检查可运行性，不证明图数值正确、跨设计类别编码一致或 GNN 预测能力。
- Slack 扰动目录：`/home/yangao/r2g_exp4_semantic_slack_canary_20260908`。这是人工标签负对照，不是真实 STA 重测：仅将已打印 slack 加 1.25 ns，同时更新隔离的 timing manifest。R2G 四阶段特征/核心边均不变，标签发生变化；Claude/Qwen 特征不变但标签也不变，不能以此证明标签提取正确；GPT 无法按身份评估。本测试不覆盖 SPEF、route DEF 或全部辅助边的隔离。
- 旧产物：`/home/yangao/r2g_exp4_graph_conversion_v2_20260908`，只读使用。
- 审计发现 R2G 某些实例仍以综合 master 的尺寸计算中心，而对应后端 DEF 已换为其他驱动尺寸。例如 riscv_divider 的 `_4003_`：综合为 `inv_1`，placement DEF 为 `inv_8`；图中心 x 为约 237.13 um，OpenDB 为 238.51 um。需明确“综合单元尺寸”与“该阶段真实物理中心”的不同语义，不能把差异通过放宽容差抹掉。
- 上述差异不在这轮审计中自动修改转换器；后续若修复，记录新版本并仅在新协议下验证。

## 复跑命令

在 `/home/yangao/r2g-skills` 下执行：

```bash
/home/yangao/.conda/envs/gnn_env/bin/python -m pytest -q tools/tests/test_experiment4_semantic_audit.py
/home/yangao/.conda/envs/gnn_env/bin/python tools/experiment4_semantic_audit.py \
  --campaign /home/yangao/r2g_exp4_graph_conversion_v2_20260908 \
  --output /home/yangao/r2g_exp4_semantic_audit_20260908 \
  --yosys /home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys \
  --openroad /home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad
/home/yangao/.conda/envs/gnn_env/bin/python tools/report_experiment4_semantic_audit.py \
  /home/yangao/r2g_exp4_semantic_audit_20260908
/home/yangao/.conda/envs/gnn_env/bin/python tools/experiment4_batch_smoke.py \
  --campaign /home/yangao/r2g_exp4_graph_conversion_v2_20260908 \
  --output /home/yangao/r2g_exp4_semantic_audit_20260908/batch_smoke.json
```

审计仅在输入、工具二进制、审计代码与输出 SHA256 全部一致时复用记录；不重新转换旧图、不消耗 Token。输入扰动脚本要求新的输出目录，拒绝覆盖旧 campaign。
