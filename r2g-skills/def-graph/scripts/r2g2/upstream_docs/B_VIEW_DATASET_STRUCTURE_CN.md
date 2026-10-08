# R2G2.0 四阶段 B 视图数据集结构说明书（通用版）

## 1. 文档范围

本文档说明 `R2G2.0_dataset_full_4stage` 工程生成的四阶段 **B 视图**异构图数据集，适用于所有设计样本，不对应某一个具体设计的统计结果。

每个样本生成四张 PyTorch Geometric `HeteroData` 图：

```text
generated/<design_family>/<version>/stages/floorplan/heterograph.pt
generated/<design_family>/<version>/stages/placement/heterograph.pt
generated/<design_family>/<version>/stages/cts/heterograph.pt
generated/<design_family>/<version>/stages/route/heterograph.pt
```

四张图具有以下共同原则：

- Base Graph 拓扑统一由展平后的 post-Yosys 综合网表建立。
- 四个阶段使用完全一致的规范节点集合和逻辑连接关系，保证节点索引对齐。
- 输入特征只使用当前预测阶段开始前已经产生的信息。
- 所有监督标签统一来源于 Route 或 post-Route 结果，并以相同值和掩码挂载到四张图。
- 后端新增的 Buffer、Inverter、Constant Cell 和拆分小网络不改变 B 视图的基础拓扑。
- 物理小网络通过稳定 Pin 名称和网络血缘关系回标到综合网表中的规范大网络。

本文中的“Gate”“Net”“Pin”等首字母大写名称表示 B 视图中的规范实体；物理实现阶段产生的实体称为“阶段 Gate”“阶段 Net”或“物理小网络”。

## 2. 张量维度总览

### 2.1 图级和节点级张量

| 存储对象 | 含义 | 输入特征张量 | 特征维度 | 标签张量 | 标签维度 |
|---|---|---:|---:|---:|---:|
| graph | 设计级全局上下文 | `global_features` | 19 | 无 | 0 |
| `gate` | 综合网表中的逻辑单元实例 | `gate.x` | 30 | `gate.y` | 2 |
| `net` | 综合网表中的规范网络 | `net.x` | 28 | `net.y` | 2 |
| `io_pin` | 顶层输入输出端口 | `io_pin.x` | 30 | `io_pin.y` | 2 |
| `pin` | 单元实例内部引脚 | `pin.x` | 37 | `pin.y` | 2 |

每类节点的 `y_valid_mask` 与 `y` 形状相同，用于指出每个标签值是否具有有效监督。四个阶段的特征维度保持不变；当前阶段不可获得的物理特征保留对应列并写为 `NaN`，同时将相应的有效性特征置为 `0`。

每类节点最后一个输入维度均为 `graph_id`。单图文件中当前固定为数值 `0`，作为批处理或后续视图转换时的图归属占位字段。

### 2.2 边张量

| PyG 边类型 | 含义 | `edge_attr` 维度 | `edge_y` 维度 | 通用阶段范围 |
|---|---|---:|---:|---|
| `gate|has|pin` | Gate 对内部 Pin 的所有权 | 2 | 0 | 四阶段始终存在 |
| `pin|connects_to|net` | 内部 Pin 与规范 Net 的连接 | 2 | 0 | 四阶段始终存在 |
| `io_pin|connects_to|net` | 顶层 IO Pin 与规范 Net 的连接 | 2 | 0 | 四阶段始终存在 |
| `gate|congestion_geom|gate` | 同一网格内的稀疏物理邻域 | 1 | 0 | 仅 CTS、Route |
| `pin|timing_path|pin` | OpenSTA 有向时序路径端点对 | 0 | 2 | 四阶段共享，按标签条件建立 |
| `pin|timing_path|io_pin` | 内部 Pin 到顶层端口的时序端点对 | 0 | 2 | 四阶段共享，按标签条件建立 |
| `io_pin|timing_path|pin` | 顶层端口到内部 Pin 的时序端点对 | 0 | 2 | 四阶段共享，按标签条件建立 |
| `net|rc_coupling|net` | 规范 Net 之间的耦合电容对 | 0 | 1 | 四阶段共享，按标签条件建立 |
| `pin|rc_resistance|pin` | 内部 Pin 之间的等效电阻端点对 | 0 | 1 | 四阶段共享，按标签条件建立 |
| `pin|rc_resistance|io_pin` | 内部 Pin 到 IO Pin 的等效电阻端点对 | 0 | 1 | 四阶段共享，按标签条件建立 |
| `io_pin|rc_resistance|pin` | IO Pin 到内部 Pin 的等效电阻端点对 | 0 | 1 | 四阶段共享，按标签条件建立 |

Timing 和 RC 数值是监督标签，不是模型输入边特征。它们只保存在 `edge_y` 和 `edge_y_mask` 中，对应关系的 `edge_attr` 形状为 `[E, 0]`。如果某个设计没有可对齐的标签端点或有效标签行，则不创建该关系，不能用额外的 mapping/query 边强行补齐。

## 3. 四阶段因果边界

阶段名称表示模型要预测的实现阶段，输入截止点位于该阶段开始之前。

| 预测图 | 输入截止点 | 物理输入 | 标准单元坐标是否可信 | HPWL/拥塞输入 | Gate–Gate 几何边 |
|---|---|---|---|---|---|
| `floorplan` | post-Yosys | 不读取 DEF | 否 | 不可用 | 不建立 |
| `placement` | post-Floorplan | `2_floorplan.def` | 否；通常仅固定宏单元和 IO 有位置 | 不可用 | 不建立 |
| `cts` | post-Placement | `3_place.def` | 是 | 可用 | 建立 |
| `route` | post-CTS | `4_cts.def` | 是 | 可用 | 建立 |

`5_route.def`、最终 SPEF、PDNSim/SP 结果和 post-Route OpenSTA 报告不得进入阶段输入特征。它们只用于生成四阶段共享的监督标签。

### 3.1 可用性状态定义

| 状态 | 含义 |
|---|---|
| **全量有效** | 字段存在，且该设计中所有符合规范的实体都有有限值。 |
| **部分有效** | 字段存在，只有成功对齐且满足计算条件的实体具有有限值，其余行为 `NaN`。 |
| **全量 NaN** | 固定特征模式中保留该字段，但当前阶段因果边界不允许使用该信息。 |
| **存在** | 对应节点存储或边关系已经创建。 |
| **不存在** | 不创建对应边关系；这不同于关系存在但 `edge_attr` 为零维。 |
| **条件存在** | 只有源数据存在、端点能够对齐且至少包含一条有效记录时才创建。 |

`placement_valid`、`pin_position_valid`、`hpwl_valid`、`stage_segment_hpwl_valid` 和 `congestion_feature_valid` 等有效性字段本身在四阶段均为有限的 `0/1` 值。应读取它们的数值判断对应物理特征能否使用，而不是判断有效性字段本身是否为 `NaN`。

### 3.2 节点类型的阶段界定

| 节点类型 | Floorplan | Placement | CTS | Route | 界定原则 |
|---|---|---|---|---|---|
| `gate` | 存在 | 存在 | 存在 | 存在 | 固定为 post-Yosys 规范 Gate；后端新增单元不成为新节点。 |
| `net` | 存在 | 存在 | 存在 | 存在 | 固定为 post-Yosys 规范 Net，并显式保留 IO-only Net。 |
| `io_pin` | 存在 | 存在 | 存在 | 存在 | 固定为 post-Yosys 顶层端口集合。 |
| `pin` | 存在 | 存在 | 存在 | 存在 | 固定为规范 `(inst_name, pin_name)` 集合。 |

因此，同一设计的四张 B 图具有相同的节点数量、节点顺序和三类基础逻辑边。不同设计之间的节点数量不要求相同。

### 3.3 图级特征的阶段界定

| 特征组 | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| 逻辑、设计配置和 SDC 特征：节点数、平均扇出、放置目标、利用率、ABC 面积、Liberty 电容、电压、频率、时钟统计 | 可用 | 可用 | 可用 | 可用 |
| DEF Die 特征：`die_width_um`、`die_height_um`、`die_area_um2`、`dbu_per_um` | 全量 NaN | 可用 | 可用 | 可用 |

### 3.4 Gate 特征的阶段界定

| Gate 特征组 | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| 单元身份/功能、类型标志、驱动强度、面积、漏电、时钟域和逻辑时序层级 | 可用 | 可用 | 可用 | 可用 |
| 放置原点、中心、归一化中心、方向和放置状态 | 全量 NaN | 部分有效：仅成功对齐的固定宏单元等 | 部分或全量有效：取决于物理单元对齐 | 部分或全量有效：取决于物理单元对齐 |
| `placement_valid` | 全部为 0 | 仅有效物理 Gate 为 1 | 仅有效物理 Gate 为 1 | 仅有效物理 Gate 为 1 |
| 五项拥塞输入特征 | 全量 NaN | 全量 NaN | 对具有可信坐标的 Gate 有效 | 对具有可信坐标的 Gate 有效 |
| `congestion_feature_valid` | 全部为 0 | 全部为 0 | 仅有效 Gate 为 1 | 仅有效 Gate 为 1 |
| `graph_id` | 可用 | 可用 | 可用 | 可用 |

综合网表中的规范 Gate 可能在后端被替换、删除或重命名。例如 Constant Cell 可能被展开为多个物理常量单元。此时 B 视图仍保留原规范 Gate，但无法可靠对齐的物理字段为 `NaN`，对应有效性字段为 `0`。

### 3.5 Net 特征的阶段界定

| Net 特征组 | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| 逻辑类型、端点数、扇出、驱动/负载数、宏单元/时钟标志、时钟域和负载电容 | 可用 | 可用 | 可用 | 可用 |
| 阶段网络数量、拆分/改名、锚点覆盖率、对齐/歧义/血缘有效性 | 可用 | 可用 | 可用 | 可用 |
| `net_bbox_width_um`、`net_bbox_height_um`、`hpwl_um` | 全量 NaN | 全量 NaN | 按网络血缘条件有效 | 按网络血缘条件有效 |
| 小网络 HPWL 总和、最大值和平均值 | 全量 NaN | 全量 NaN | 按网络血缘条件有效 | 按网络血缘条件有效 |
| `hpwl_valid`、`stage_segment_hpwl_valid` | 全部为 0 | 全部为 0 | 仅完整且无歧义的聚合结果为 1 | 仅完整且无歧义的聚合结果为 1 |
| `graph_id` | 可用 | 可用 | 可用 | 可用 |

网络血缘描述字段四阶段均保留，因为它们同时记录“当前阶段 Net 如何映射回 Base Graph Net”。字段存在不代表某个网络一定能够无歧义地完成物理回标，应结合 `stage_lineage_valid`、`stage_lineage_ambiguous_flag` 和各项有效性字段判断。

### 3.6 IO Pin 特征的阶段界定

| IO Pin 特征组 | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| 方向/角色、时钟和时序端点标志、SDC 时钟/IO 约束、逻辑时序层级、所连 Net 类型 | 可用 | 可用 | 可用 | 可用 |
| 位置、归一化位置、Die 边界距离、物理层和最近 TAP 距离 | 全量 NaN | 按 DEF 对齐条件有效 | 按 DEF 对齐条件有效 | 按 DEF 对齐条件有效 |
| `pin_position_valid` | 全部为 0 | 仅有效 IO Pin 为 1 | 仅有效 IO Pin 为 1 | 仅有效 IO Pin 为 1 |
| `graph_id` | 可用 | 可用 | 可用 | 可用 |

### 3.7 Internal Pin 特征的阶段界定

| Internal Pin 特征组 | Floorplan | Placement | CTS | Route |
|---|---|---|---|---|
| Pin 类型/角色/方向、Liberty 电气量、所属单元身份/功能、功能标志、SDC 时钟上下文和逻辑时序层级 | 可用 | 可用 | 可用 | 可用 |
| 位置、归一化位置和 Die 边界距离 | 全量 NaN | 部分有效：通常仅固定宏单元 Pin | 按 Gate、LEF Pin 几何对齐条件有效 | 按 Gate、LEF Pin 几何对齐条件有效 |
| `pin_position_valid` | 全部为 0 | 仅有效 Pin 为 1 | 仅有效 Pin 为 1 | 仅有效 Pin 为 1 |
| `graph_id` | 可用 | 可用 | 可用 | 可用 |

### 3.8 节点标签的阶段界定

节点标签的数值和掩码由 Route/post-Route 数据生成，并原样挂载到四个阶段。下表中的“条件有效”表示 EDA 源数据和实体对齐覆盖可能不完整，不表示不同阶段使用不同标签。

| 节点标签 | Floorplan | Placement | CTS | Route | 标签来源边界 |
|---|---|---|---|---|---|
| Gate `cell_congestion` | 条件有效 | 条件有效 | 条件有效 | 条件有效 | Route DEF/拥塞计算 |
| Gate `ir_drop_mV` | 条件有效 | 条件有效 | 条件有效 | 条件有效 | PDNSim/IR-drop 结果 |
| Net `routed_wirelength_um` | 条件有效 | 条件有效 | 条件有效 | 条件有效 | Route DEF 物理线段 |
| Net `ground_cap_pF` | 条件有效 | 条件有效 | 条件有效 | 条件有效 | 最终 SPEF |
| Pin `setup_slack_ns`、`hold_slack_ns` | 条件有效 | 条件有效 | 条件有效 | 条件有效 | post-Route OpenSTA |
| IO Pin `setup_slack_ns`、`hold_slack_ns` | 条件有效 | 条件有效 | 条件有效 | 条件有效 | post-Route OpenSTA |

这些字段是训练监督，绝不能因为它们已经附着在图对象中，就作为对应阶段的模型输入。

### 3.9 边关系及边载荷的阶段界定

下表采用 PyG 三元组记法 `源节点|关系|目标节点`。元数据模式可能把三类基础逻辑边简写为 `gate→pin`、`pin→net` 和 `io_pin→net`。

| 关系/载荷 | Floorplan | Placement | CTS | Route | 输入与标签边界 |
|---|---|---|---|---|---|
| `gate|has|pin`，二维 `edge_attr` | 存在 | 存在 | 存在 | 存在 | 逻辑输入关系 |
| `pin|connects_to|net`，二维 `edge_attr` | 存在 | 存在 | 存在 | 存在 | 逻辑输入关系 |
| `io_pin|connects_to|net`，二维 `edge_attr` | 存在 | 存在 | 存在 | 存在 | 逻辑输入关系 |
| `gate|congestion_geom|gate`，一维距离 `edge_attr` | 不存在 | 不存在 | 存在 | 存在 | 可选拥塞输入关系；要求可信标准单元坐标 |
| Timing Path 关系，零维 `edge_attr`、二维 `edge_y` | 条件存在 | 条件存在 | 条件存在 | 条件存在 | 四阶段共享的 post-Route 监督关系 |
| `net|rc_coupling|net`，零维 `edge_attr`、一维 `edge_y` | 条件存在 | 条件存在 | 条件存在 | 条件存在 | 四阶段共享的 SPEF 监督关系 |
| RC Resistance 关系，零维 `edge_attr`、一维 `edge_y` | 条件存在 | 条件存在 | 条件存在 | 条件存在 | 四阶段共享的 SPEF 监督关系 |

Timing 和 RC 关系的 `edge_index` 本身也包含 post-Route 监督结构。默认消息传递编码器必须排除这些关系；只有相应任务视图可以显式启用。缺少端点节点或有效标签时，直接不创建关系，不生成辅助 mapping/query 数据。

## 4. B 视图规范拓扑

B 视图的规范拓扑只由展平后的 post-Yosys Verilog 网表建立：

```text
gate ──has──> pin ──connects_to──> net
io_pin ───────────────────────────> net

gate <──congestion_geom──> gate          可选物理输入关系
pin/io_pin ──timing_path──> pin/io_pin   仅监督关系
net <──rc_coupling──> net                仅监督关系
pin/io_pin ──rc_resistance──> pin/io_pin 仅监督关系
```

规范实体及其稳定键为：

| 实体 | 稳定键 |
|---|---|
| Gate | `inst_name` |
| Net | `net_name` |
| IO Pin | `iopin_name` |
| Internal Pin | `(inst_name, pin_name)` |
| Gate–Pin 所有权 | `(inst_name, pin_name)` |
| Pin–Net 连接 | `(inst_name, pin_name, net_name)` |
| IO Pin–Net 连接 | `(iopin_name, net_name)` |

### 4.1 IO-only Net

如果一个 Net 只连接顶层 IO Pin、没有任何内部 Gate Pin，它仍然必须作为规范 Net 节点保存，并建立 `io_pin|connects_to|net` 边。基础图构建不能只从内部 Pin 连接记录反推 Net，否则会遗漏 IO-only Net。

### 4.2 后端网络拆分与回标

Placement、CTS 和 Route 可能把一个综合大网络拆成多个物理小网络，尤其常见于宏单元连接、Buffer/Inverter 插入和常量网络处理。B 视图不以物理小网络替换规范 Net，而是执行以下对齐：

1. 使用规范内部 Pin 键 `(inst_name, pin_name)` 和 IO Pin 名称作为稳定锚点。
2. 找到包含这些锚点的阶段 Net，建立规范 Net 到物理小网络的一对多关系。
3. 对透明后端 BUF/INV 形成的连通分量，只在其具有唯一规范 Net 所有者时继续传播。
4. 如果一个后端连通分量同时锚定到多个规范 Net，则标记为歧义，不强行归属。
5. HPWL、实际布线长度和 RC 量均先在小网络上计算，再按照网络血缘聚合回规范 Net。

## 5. 图级输入特征：19 维

| 索引 | 字段 | 含义 |
|---:|---|---|
| 0 | `num_logical_cells` | 综合后的规范 Gate 数量。 |
| 1 | `num_logical_nets` | 规范 Net 数量，包括 IO-only Net。 |
| 2 | `num_ios` | 顶层 IO Pin 数量。 |
| 3 | `avg_fanout` | 逻辑 Net 的平均扇出。 |
| 4 | `die_width_um` | 当前输入快照的 Die 宽度，单位 µm。 |
| 5 | `die_height_um` | 当前输入快照的 Die 高度，单位 µm。 |
| 6 | `die_area_um2` | 当前输入快照的 Die 面积，单位 µm²。 |
| 7 | `dbu_per_um` | 每微米对应的 DEF 数据库单位数。 |
| 8 | `place_density` | 配置的放置密度。 |
| 9 | `place_density_is_default` | 放置密度是否来自默认值。 |
| 10 | `core_utilization` | 配置的目标 Core 利用率。 |
| 11 | `abc_area` | 综合/ABC 面积统计。 |
| 12 | `total_lib_pin_cap_fF` | 所有规范内部 Pin 的 Liberty 电容总和。 |
| 13 | `v_nom` | 标称供电电压。 |
| 14 | `freq_hz` | 设计时钟频率，单位 Hz。 |
| 15 | `num_clocks` | 从 SDC 解析出的时钟数量。 |
| 16 | `min_clock_period_ns` | 最小时钟周期，单位 ns。 |
| 17 | `max_clock_period_ns` | 最大时钟周期，单位 ns。 |
| 18 | `avg_clock_period_ns` | 平均时钟周期，单位 ns。 |

当前因果截止点没有 DEF 时，四项 Die 物理字段为 `NaN`。

## 6. Gate 节点

### 6.1 节点含义

`gate` 节点表示 `1_1_yosys.v` 中的综合逻辑单元实例。即使物理实现阶段删除、替换或重命名了对应实例，该规范节点仍保留在四张 B 图中。

### 6.2 Gate 输入特征：30 维

| 字段 | 含义 |
|---|---|
| `x_um`, `y_um` | DEF 中单元放置原点，单位 µm。 |
| `center_x_um`, `center_y_um` | 结合方向和 LEF 宽高计算的单元中心。 |
| `center_x_normalized`, `center_y_normalized` | 相对于 Die 范围归一化后的单元中心。 |
| `cell_type_id` | 由 `configs/encode_map.csv` 编码的工艺 Master 类型。 |
| `cell_function_id` | 时序单元、Buffer、Inverter、时钟单元、常量单元、物理单元等功能类别。 |
| `is_sequential_cell` | 是否为触发器或锁存器。 |
| `is_buffer_cell` | 是否为 Buffer。 |
| `is_inverter_cell` | 是否为 Inverter。 |
| `is_clock_buffer_cell` | 是否为 Clock Buffer。 |
| `is_clock_gate_cell` | 是否为 Clock Gating Cell。 |
| `drive_strength` | 从 Master 后缀等信息解析的驱动强度。 |
| `cell_area_um2` | LEF 单元面积，单位 µm²。 |
| `cell_leakage_power` | Liberty 原始漏电功耗值，数据集不在此处归一化。 |
| `clock_domain_id` | 根据 SDC 和逻辑时序传播推断的时钟域编码。 |
| `timing_forward_level` | 从时序起点到该 Gate Pin 的最长正向逻辑层级。 |
| `timing_reverse_level` | 从该 Gate Pin 向时序终点反向计算的最长逻辑层级。 |
| `timing_level_valid` | 逻辑时序层级是否有效。 |
| `orientation_id` | DEF 方向编码。 |
| `placement_status_id` | DEF 放置状态编码。 |
| `placement_valid` | 当前阶段允许使用且成功获得该 Gate 坐标时为 1。 |
| `congestion_pin_density` | Gate 覆盖网格中的平均 Pin 数量。 |
| `congestion_cell_density` | Gate 覆盖网格中的平均单元数量。 |
| `congestion_net_density` | 包围盒覆盖这些网格的阶段物理 Net 平均数量。 |
| `congestion_rudy` | 依据物理小网络包围盒和归一化重叠面积计算的平均 RUDY。 |
| `congestion_rudy_pin` | 按 Pin 数加权的网格 RUDY。 |
| `congestion_feature_valid` | 可信标准单元坐标使五项拥塞输入可计算时为 1。 |
| `graph_id` | 图归属占位字段，单图中当前为 0。 |

拥塞网格尺寸来自布线前已知的工艺参数：

```text
15 条 FastRoute track × 0.14 µm Metal3 pitch = 2.1 µm = 4200 DBU
```

### 6.3 Gate 标签：2 维

| 标签 | 单位 | 有效性字段 | 含义 |
|---|---|---|---|
| `cell_congestion` | 无量纲 | `congestion_valid` | Gate 所在固定 2.1 µm Route 网格中的水平/垂直已布线需求容量比最大值。 |
| `ir_drop_mV` | mV | `irdrop_valid` | `1000 ×（VDD 源电压 − 求解得到的 Gate VDD 节点电压）`。 |

对应掩码为 `gate.y_valid_mask = [congestion_valid, irdrop_valid]`。

## 7. Net 节点

### 7.1 节点含义

`net` 节点表示综合网表中的规范 Net。Placement、CTS 和 Route 可以将一个规范 Net 拆分成多个物理小网络，但不会在 B 视图中增加 Net 节点。稳定 Pin 键和 IO Pin 名称用于把阶段小网络回标到原始节点。

### 7.2 Net 输入特征：28 维

| 字段 | 含义 |
|---|---|
| `net_type_id` | SIGNAL、CLOCK、RESET、SCAN、POWER、GROUND 等网络类别编码。 |
| `pin_count` | 规范内部 Pin 和 IO Pin 端点总数。 |
| `fanout` | 逻辑负载端点数量。 |
| `num_drivers` | 根据 Liberty/IO 方向推断的驱动端点数量。 |
| `num_sinks` | 根据 Liberty/IO 方向推断的负载端点数量。 |
| `connects_macro_flag` | 是否连接大型 LEF Macro 或 RAM 类 Master。 |
| `is_clock_net` | 是否属于推断的时钟网络。 |
| `clock_domain_id` | 唯一时钟域编码；歧义或不可用时为 `UNKNOWN`。 |
| `total_sink_cap_fF` | 所有负载 Pin 的 Liberty 电容总和。 |
| `net_bbox_width_um` | 所有对齐物理小网络包围盒并集的宽度。 |
| `net_bbox_height_um` | 所有对齐物理小网络包围盒并集的高度。 |
| `hpwl_um` | 分别计算所有对齐物理小网络 HPWL 后的累加值，不是对规范大网络端点直接计算一个大包围盒。 |
| `stage_net_count` | 对齐到该规范 Net 的阶段物理 Net 总数。 |
| `stage_direct_net_count` | 通过稳定规范端点直接找到的阶段 Net 数量。 |
| `stage_inferred_backend_net_count` | 通过唯一归属的后端 BUF/INV 连通分量补充找到的阶段 Net 数量。 |
| `stage_net_split_flag` | 一个规范 Net 映射到多个阶段 Net 时为 1。 |
| `stage_net_renamed_flag` | 一对一映射但阶段 Net 名称变化时为 1。 |
| `stage_net_anchor_count` | 在物理快照中找到的规范端点数量。 |
| `stage_net_anchor_coverage` | 已找到规范端点数除以规范端点总数。 |
| `stage_net_alignment_valid` | 所有期望规范端点均成功对齐时为 1。 |
| `stage_lineage_ambiguous_flag` | 后端连通分量同时锚定到多个规范 Net 时为 1。 |
| `stage_lineage_valid` | 至少找到一个阶段 Net 且不存在歧义连通分量时为 1。 |
| `stage_segment_total_hpwl_um` | 所有对齐物理小网络 HPWL 总和；有效时与 `hpwl_um` 一致。 |
| `stage_segment_max_hpwl_um` | 对齐物理小网络中的最大 HPWL。 |
| `stage_segment_mean_hpwl_um` | 对齐物理小网络 HPWL 的平均值。 |
| `hpwl_valid` | 所有小网络几何和血缘均满足计算要求时为 1。 |
| `stage_segment_hpwl_valid` | 小网络 HPWL 聚合是否有效。 |
| `graph_id` | 图归属占位字段，单图中当前为 0。 |

HPWL 只在 CTS 和 Route 预测图中可作为输入，因为二者分别使用 post-Placement 和 post-CTS 信息。缺少或存在歧义的小网络几何应产生 `NaN`，不得退化为“仅使用 Base Graph 端点计算一个大包围盒”。

### 7.3 Net 标签：2 维

| 标签 | 单位 | 有效性字段 | 含义 |
|---|---|---|---|
| `routed_wirelength_um` | µm | `wirelength_valid` | 对齐到规范 Net 的所有 Route DEF 物理小网络 Manhattan 布线段长度之和。 |
| `ground_cap_pF` | pF | `ground_cap_valid` | 所有对齐物理小网络的 SPEF 对地电容之和。 |

对应掩码为 `net.y_valid_mask = [wirelength_valid, ground_cap_valid]`。

## 8. IO Pin 节点

### 8.1 节点含义

`io_pin` 节点表示顶层 Verilog 端口，稳定键为端口名称。即使该端口连接的是没有任何内部单元连接的 IO-only Net，IO Pin 和对应 Net 节点仍然保留。

### 8.2 IO Pin 输入特征：30 维

| 字段 | 含义 |
|---|---|
| `pin_x_um`, `pin_y_um` | 顶层 DEF Pin 位置。 |
| `pin_x_normalized`, `pin_y_normalized` | 相对于 Die 范围归一化后的 Pin 位置。 |
| `distance_to_die_left_um`, `distance_to_die_right_um` | 到 Die 左右边界的水平距离。 |
| `distance_to_die_bottom_um`, `distance_to_die_top_um` | 到 Die 上下边界的垂直距离。 |
| `pin_position_valid` | 当前阶段允许且实际提供物理 IO 位置时为 1。 |
| `pin_direction_id` | INPUT、OUTPUT、INOUT 方向编码。 |
| `pin_role_id` | Primary Input、Primary Output、Primary Inout 或 Clock Port 角色编码。 |
| `is_clock_port` | SDC 是否在该端口上创建时钟。 |
| `is_driver_pin`, `is_sink_pin` | 按顶层 IO 方向语义得到的逻辑驱动/负载角色。 |
| `is_timing_startpoint`, `is_timing_endpoint` | 逻辑时序起点/终点分类。 |
| `clock_domain_id` | 关联时钟域编码。 |
| `clock_period_ns` | 关联时钟周期。 |
| `clock_uncertainty_ns` | 关联 SDC 时钟不确定度。 |
| `clock_constraint_valid` | 时钟约束是否可用。 |
| `input_delay_ns`, `output_delay_ns` | SDC IO Delay。 |
| `io_constraint_valid` | Input/Output Delay 约束是否可用。 |
| `pin_layer_id` | 物理 Pin 所在层编码。 |
| `timing_forward_level`, `timing_reverse_level` | 逻辑正向/反向时序层级。 |
| `timing_level_valid` | 时序层级是否有效。 |
| `net_type_id` | 所连接规范 Net 的类型编码。 |
| `nearest_tap_distance_um` | IO Pin 到最近已放置 TAP Master 单元的欧氏距离。 |
| `graph_id` | 图归属占位字段，单图中当前为 0。 |

### 8.3 IO Pin 标签：2 维

| 标签 | 单位 | 有效性字段 | 含义 |
|---|---|---|---|
| `setup_slack_ns` | ns | `setup_valid` | 按 IO 端口名称对齐的 post-Route OpenSTA Setup Slack。 |
| `hold_slack_ns` | ns | `hold_valid` | 按 IO 端口名称对齐的 post-Route OpenSTA Hold Slack。 |

对应掩码为 `io_pin.y_valid_mask = [setup_valid, hold_valid]`。

## 9. Internal Pin 节点

### 9.1 节点含义

`pin` 节点表示某个规范 Gate 拥有的具名内部 Pin。其唯一身份必须使用 `(inst_name, pin_name)`，不能仅使用 `pin_name`。

### 9.2 Internal Pin 输入特征：37 维

| 字段 | 含义 |
|---|---|
| `pin_type_id` | Data、Clock、Reset、Select、Output、Power、Ground 等细粒度 Pin 类型编码。 |
| `pin_role_id` | DATA、Q、CLOCK、RESET、组合输入/输出、INOUT 等建模角色编码。 |
| `pin_direction_id` | Liberty 方向编码。 |
| `pin_cap_fF` | Liberty 输入 Pin 电容。 |
| `pin_max_transition_ns` | Liberty 最大 Transition 约束。 |
| `pin_max_capacitance_fF` | Liberty 最大 Capacitance 约束。 |
| `cell_type_id` | 所属 Gate 的 Master 类型编码。 |
| `cell_function_id` | 所属 Gate 的功能类别编码。 |
| `owner_drive_strength` | 所属 Gate 的驱动强度。 |
| `is_clock_pin`, `is_data_pin` | Clock/Data 功能标志。 |
| `is_reset_pin`, `is_set_pin`, `is_enable_pin` | 控制 Pin 功能标志。 |
| `is_sequential_pin`, `is_combinational_pin` | 时序/组合分类标志。 |
| `is_driver_pin`, `is_sink_pin` | 根据方向得到的逻辑驱动/负载角色。 |
| `is_timing_startpoint`, `is_timing_endpoint` | 逻辑时序起点/终点分类。 |
| `clock_domain_id` | 关联时钟域编码。 |
| `clock_period_ns`, `clock_uncertainty_ns` | 关联 SDC 时钟值。 |
| `clock_constraint_valid` | 时钟约束是否可用。 |
| `timing_forward_level`, `timing_reverse_level` | 逻辑时序正向/反向层级。 |
| `timing_level_valid` | 时序层级是否有效。 |
| `pin_x_um`, `pin_y_um` | DEF 单元放置坐标加上经过方向变换的 LEF Pin 偏移得到的物理位置。 |
| `pin_x_normalized`, `pin_y_normalized` | 相对于 Die 归一化后的 Pin 位置。 |
| `distance_to_die_left_um`, `distance_to_die_right_um` | 到 Die 左右边界的距离。 |
| `distance_to_die_bottom_um`, `distance_to_die_top_um` | 到 Die 上下边界的距离。 |
| `pin_position_valid` | 当前阶段允许使用，且所属 Gate 坐标与 LEF Pin 几何均存在时为 1。 |
| `graph_id` | 图归属占位字段，单图中当前为 0。 |

### 9.3 Internal Pin 标签：2 维

| 标签 | 单位 | 有效性字段 | 含义 |
|---|---|---|---|
| `setup_slack_ns` | ns | `setup_valid` | 按 `(inst_name, pin_name)` 对齐的 post-Route OpenSTA Setup Slack。 |
| `hold_slack_ns` | ns | `hold_valid` | 按 `(inst_name, pin_name)` 对齐的 post-Route OpenSTA Hold Slack。 |

对应掩码为 `pin.y_valid_mask = [setup_valid, hold_valid]`。

## 10. 边关系、边特征和边标签

### 10.1 `gate|has|pin`

- 含义：规范 Gate 指向其拥有的每个 Internal Pin 的有向所有权边。
- 数量关系：每个 Internal Pin 恰好对应一条边。
- 输入特征为二维：
  - `cell_type_id`：源 Gate 的 Master 类型编码。
  - `pin_type_id`：目标 Pin 的类型编码。
- 无标签。

### 10.2 `pin|connects_to|net`

- 含义：Internal Pin 指向其连接的规范 Net 的有向关联边。
- 数量关系：每个已连接的 Internal Pin 恰好对应一条边。
- 输入特征为二维：
  - `pin_type_id`：源 Pin 的类型编码。
  - `net_type_id`：目标 Net 的类型编码。
- 无标签。

### 10.3 `io_pin|connects_to|net`

- 含义：顶层 IO Pin 指向其连接的规范 Net 的有向关联边。
- 数量关系：每个已连接的 IO Pin 对应一条边。
- 输入特征为二维：
  - `pin_direction_id`：源端口方向编码。
  - `net_type_id`：目标 Net 类型编码。
- 无标签。

### 10.4 `gate|congestion_geom|gate`

- 含义：供拥塞模型使用的同网格稀疏物理邻域。
- 阶段范围：仅 CTS 和 Route 预测图。
- 构建规则：
  1. 使用 Gate 放置原点将 Gate 分配到固定 2.1 µm 网格。
  2. 对同一网格内候选 Gate 对按中心点欧氏距离排序。
  3. 以确定性顺序选择最近邻，同时限制每个 Gate 的无向度数最多为 5。
  4. 每条无向关系在 PyG 中保存为两个方向对称的有向条目。
- 输入特征为一维：`euclidean_distance_um`，即 Gate 中心间距离，单位 µm。
- 无标签。

网格归属使用放置原点，距离使用单元中心；由于单元宽度不同，中心距离可能大于 2.1 µm。该关系必须作为独立边类型保存，不能合并到基础逻辑边的 `edge_attr` 中。

### 10.5 Timing Path 关系

可能建立的端点类型组合为：

```text
pin|timing_path|pin
pin|timing_path|io_pin
io_pin|timing_path|pin
```

- 仅为实际存在且能够对齐的 OpenSTA 路径端点组合创建关系。
- 边方向表示 OpenSTA 报告中的起点到终点顺序。
- 输入特征为零维，即 `edge_attr.shape == [E, 0]`。
- 标签为二维：
  - `setup_delay_ns`：Setup/Max Path Delay 样本。
  - `hold_delay_ns`：Hold/Min Path Delay 样本。
- `edge_y_mask` 分别记录 Setup 和 Hold 标签有效性。
- 如果某类端点不存在或没有有效路径样本，则不创建对应边类型。

### 10.6 `net|rc_coupling|net`

- 含义：两个规范 Net 之间实际提取到的正耦合电容关系。
- 物理语义为无向，在 PyG 中保存为两个方向对称的有向条目。
- 输入特征为零维。
- 标签为一维 `coupling_cap_pF`，单位 pF。
- 有效性掩码为 `edge_y_mask[:, 0]`。
- 只保存实际观测到的正耦合对；没有边不等于负样本。

### 10.7 RC Resistance 关系

可能建立的端点类型组合为：

```text
pin|rc_resistance|pin
pin|rc_resistance|io_pin
io_pin|rc_resistance|pin
```

- 含义：在某个已对齐规范 Net 上，从提取出的驱动端点到负载端点的有向等效电阻关系。
- 输入特征为零维。
- 标签为一维 `effective_resistance_ohm`，单位 Ω。
- 有效性掩码为 `edge_y_mask[:, 0]`。
- 缺少端点类型或有效样本时省略相应关系，不使用 mapping/query 文件补齐。

## 11. 标签体系总览

### 11.1 节点标签

| 节点类型 | 标签 | 标签来源 | 回标键/方法 |
|---|---|---|---|
| Gate | `cell_congestion` | Route DEF 和固定网格计算 | Gate 物理位置对齐 |
| Gate | `ir_drop_mV` | IR-drop/PDNSim 结果 | Gate 实例或电源节点对齐 |
| Net | `routed_wirelength_um` | Route DEF | 物理小网络线段聚合到规范 Net |
| Net | `ground_cap_pF` | SPEF | 物理小网络电容聚合到规范 Net |
| Pin | `setup_slack_ns`, `hold_slack_ns` | post-Route OpenSTA | `(inst_name, pin_name)` |
| IO Pin | `setup_slack_ns`, `hold_slack_ns` | post-Route OpenSTA | 顶层端口名称 |

### 11.2 边标签

| 边关系 | 标签 | 标签来源 | 方向语义 |
|---|---|---|---|
| Timing Path | `setup_delay_ns`, `hold_delay_ns` | post-Route OpenSTA Path Report | 起点到终点，有向 |
| RC Coupling | `coupling_cap_pF` | SPEF | 物理无向，存储为双向 |
| RC Resistance | `effective_resistance_ohm` | SPEF/RC 提取结果 | Driver 到 Sink，有向 |

### 11.3 标签一致性

- 四阶段使用相同的标签值、边标签关系和有效性掩码。
- 标签缺失由 `y_valid_mask` 或 `edge_y_mask` 表示，不用数值 `0` 代替未知标签。
- 某个标签边类型完全没有有效样本时，不创建该关系。
- 标签保持原始物理单位，不在数据集构建阶段进行归一化、裁剪或训练集筛选。

## 12. 缺失值、掩码和防泄漏规则

- 当前预测截止点不可获得的输入特征必须为 `NaN`，不能用零填充，也不能从更晚阶段 DEF 回填。
- 特征是否有效由 `placement_valid`、`pin_position_valid`、`hpwl_valid`、`stage_segment_hpwl_valid` 和 `congestion_feature_valid` 等显式字段表示。
- 节点标签使用 `y_valid_mask`，边标签使用 `edge_y_mask`。
- 掩码只表示源数据和实体对齐有效性，不表示样本是否被选择进入训练集。
- Route/post-Route 标签即使存放在图对象中，也不能作为四阶段输入特征。
- 默认消息传递关系仅包括三类基础逻辑关联边。
- `gate|congestion_geom|gate` 是 CTS/Route 可选的拥塞模型输入关系。
- Timing 和 RC 关系属于任务专用监督关系，默认输入编码器必须排除它们，避免标签结构泄漏。
- 不存在的关系应保持“不创建”，不能用空标签、伪边、mapping 或 query 文件强行补齐。

## 13. 文件结构与辅助元数据

每个阶段图目录的核心文件为：

```text
stages/<stage>/
├── heterograph.pt
├── heterograph.metadata.json
├── heterograph.alignment.json
├── stage_snapshot.json
└── features/
```

各文件作用如下：

| 文件 | 作用 |
|---|---|
| `heterograph.pt` | 最终 PyG `HeteroData` 图。 |
| `heterograph.metadata.json` | 特征/标签模式、阶段可用性契约、来源摘要和完整性信息。 |
| `heterograph.alignment.json` | CSV 行、规范实体和图节点索引之间的对齐摘要。 |
| `stage_snapshot.json` | 当前阶段相对上一输入截止点的物理实体和网络血缘变化摘要。 |
| `features/*.csv` | 组装图之前的节点、边和图级特征中间表。 |

此外，网络拆分回标使用的直接快照通常位于：

```text
generated/<design_family>/<version>/snapshots/base_to_placement_snapshot.json
generated/<design_family>/<version>/snapshots/base_to_route_snapshot.json
```

这些快照用于确定 Base Graph 规范 Net 与 Placement/Route 物理小网络之间的一对多关系，使 HPWL、实际布线长度和 RC 标签能够正确聚合。快照是数据构建和审计信息，不应默认作为模型输入特征。

## 14. 通用加载与检查示例

```python
import torch

graph = torch.load(
    "generated/<design_family>/<version>/stages/cts/heterograph.pt",
    map_location="cpu",
    weights_only=False,
)

print(graph.node_types)
print(graph.edge_types)

print(graph["gate"].x.shape)
print(graph["gate"].x_schema)
print(graph["gate"].y.shape)
print(graph["gate"].y_schema)
print(graph["gate"].y_valid_mask.shape)

edge_type = ("gate", "congestion_geom", "gate")
if edge_type in graph.edge_types:
    print(graph[edge_type].edge_index.shape)
    print(graph[edge_type].edge_attr.shape)
    print(graph[edge_type].edge_schema)
```

人工或自动检查时，应至少确认：

1. 同一设计四阶段的规范节点数量和顺序完全一致。
2. 三类基础逻辑边在四阶段完全一致。
3. Floorplan/Placement 中不存在 Gate–Gate 几何边，CTS/Route 中每个 Gate 的无向度数不超过 5。
4. 各节点 `x`、`y`、掩码维度与本说明书一致。
5. 早期阶段不可用的物理特征为 `NaN`，对应有效性字段为 `0`。
6. 四阶段节点标签、边标签及其掩码一致。
7. Timing/RC 边没有被混入默认模型输入关系。
8. IO-only Net 未被遗漏。
9. 拆分网络的 HPWL、实际布线长度和 RC 值均按物理小网络计算后聚合到规范 Net。

## 15. 适用边界

本文档规定的是 B 视图的通用数据契约和字段语义，不规定任何单一设计的：

- 节点或边数量；
- 标签覆盖率；
- 物理实体对齐成功率；
- Timing/RC 条件边类型是否一定出现；
- 特定阶段有效物理坐标的行数。

这些统计必须从对应设计生成的 `heterograph.metadata.json`、`heterograph.alignment.json`、验证报告和四阶段统计报告中读取，不能将某个参考设计的统计值推广到其他设计。
