# 实验四：物理几何修正与新数据准备

本次变更是研发修复与回顾性验证，不改写 v2 的冻结程序和成绩。

## 几何语义

逻辑图节点始终对应综合网表的实例。master ID、功能、驱动强度和 Liberty 属性描述综合逻辑单元，不因为后端换尺寸就改变逻辑身份。

物理原点、方向、宽高、中心及归一化中心描述当前允许读取的 DEF 中的物理实现。若 DEF 中该实例已经从 `inv_1` 换成 `inv_8`，中心必须按 `inv_8` 的 LEF 宽高计算。此规则不读取更晚阶段的 DEF。

| 图阶段 | 允许的物理快照 |
| --- | --- |
| floorplan | 无 DEF；位置保持缺失 |
| placement | floorplan DEF，沿用已有坐标可信性门控 |
| cts | placement DEF |
| route | CTS DEF |

如果快照指定的实际 master 缺少有效 LEF 宽高，则几何尺寸和中心为 NaN。不能用零或综合旧尺寸伪造当前位置的几何中心。若没有快照组件，仍可保留综合尺寸，但位置不因此变为可用。

独立审计增加归一化中心检查，原点由 OpenDB die bbox 读取，不假设 die 从 (0,0) 起始。坐标绝对容差仍为 0.001 um；归一化坐标容差为 1e-6，相对容差均为 1e-5。

后续 v0.3 独立审计进一步发现，多图形 pin 的位置曾使用所有图形联合 bbox 的中心，而 OpenDB `getAvgXY` 使用各图形中心的算术平均。修正后 pin 坐标直接对照 OpenDB；net bbox/HPWL 只核验物理端点集合与 Yosys canonical 端点集合完全相同的直接 net。HPWL 是 X、Y 两个独立 DBU 量化跨度之和，因此使用有推导依据的 0.0021 um 绝对容差。

## 验证方式

`validate_experiment4_geometry_fix.py` 复制 v2 的小型 runtime，仅替换 `02_extract_features.py`。原 base graph 与 labels 复制到新目录，重新提取 features、组装 heterograph、生成 snapshots。它不调用 LLM，也不重跑 ORFS；全部 24 个设计都验证，包括原本没有发现差异的对照设计。

最终验证目录：`/home/yangao/r2g_exp4_physical_semantics_fix_validation_20260908`。

完成结果：24/24 个设计通过当前独立审计；96/96 张阶段图通过新旧节点、标签与核心边变化范围检查。独立标签核验覆盖 42,906 个 routed-wirelength、42,599 个 ground-cap、4,942 个 setup slack 和 4,942 个 hold slack，均为 0 缺失、0 错误。当前未实现的其他数值维度仍不计作已验证。

旧四方法与修正后的 R2G 均使用独立审计 v0.3；旧四方法结果保存在 `/home/yangao/r2g_exp4_semantic_audit_v0_3_retrospective_20260908`，修正后结果保存在最终验证目录的 `audit_v0_3_final/`。旧审计记录继续保留，不混用不同审计版本的总分。

## 未暴露数据库存

只读检查已有 232 个输入完整的合格设计，并排除旧 v1/v1.1/v2 全部集合中出现过的任务、同一仓库、相同源码闭包及任何完全相同的 RTL 文件。

剩余 161 个潜在未暴露设计，来自 118 个仓库：小型 48、中型 49、大型 64。71 个设计被排除，逐项原因保存在 `unseen_inventory.json`。一个设计可有多个排除原因，因此原因计数不能相加当作设计数量。

这只是库存，不是已经冻结的测试集。字节哈希不能识别改名或轻微修改的克隆，也不能自动识别未申报的历史暴露。最终抽样时仍需检查集合间和测试集内部的源码关系。不能按转换结果选择新样本。

## 后续正式协议的边界

- 开发期可用标准解析工具的权限应一致：不能一方允许原生 Yosys/OpenDB，另一方必须从零写解析器，却把差异全部解释为智能或转图能力。
- 将核心语义正确性与原生 schema/打包合规分开报告；缺少身份映射标记无法评估，不等同于所有张量都错。
- 候选转换器冻结前固定特征/标签来源、单位、可见范围和数值容差。不要根据隐藏结果临时放宽规则。
- 完整标签 oracle、更多输入隔离测试和公平开发接口尚未全部完成，因此本次不启动新付费 campaign，不宣称实验四已经结束。

## 复跑

在 `/home/yangao/r2g-skills` 下执行：

```bash
/home/yangao/.conda/envs/gnn_env/bin/python tools/validate_experiment4_geometry_fix.py \
  --campaign /home/yangao/r2g_exp4_graph_conversion_v2_20260908 \
  --output /home/yangao/r2g_exp4_physical_semantics_fix_validation_20260908 \
  --feature-script /home/yangao/r2g-skills/r2g-skills/def-graph/scripts/r2g2/02_extract_features.py
/home/yangao/.conda/envs/gnn_env/bin/python tools/experiment4_semantic_audit.py \
  --campaign /home/yangao/r2g_exp4_physical_semantics_fix_validation_20260908 \
  --output /home/yangao/r2g_exp4_physical_semantics_fix_validation_20260908/audit_v0_3_final \
  --methods r2g-geometry-fix \
  --yosys /home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/yosys/bin/yosys \
  --openroad /home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad
```

只有原输入与代码绑定、生成产物哈希均一致时才跳过已完成设计。代码改变时需新验证目录，不能把不同版本结果混在同一份验证记录中。
