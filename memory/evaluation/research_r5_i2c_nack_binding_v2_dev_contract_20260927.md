# R5 I²C NACK 状态绑定 v2 DEV 代次合约（实现前冻结）

## 代次与角色

冻结 v1（commit `1c01edd`）对预登记 `freecores/i2c` 目标候选给出 `UNSUPPORTED / public_context_not_supported`，且其原生负控有指定判定力；[原结果](research_r5_freecores_i2c_target_candidate_result_20260927.md)永久保留。**从本卡起，freecores commit `3b067f00ccced753b0502024766a51f58f3e04bc` 改列 `DEV_OBSERVED`，不再是 v2 的未见 target/FINAL 样本。** alexforencich 和 ZipCPU 同样只作 DEV。v2 是新 software generation，不能回填 v1 的迁移或 ΔMemory 结果。

## 单一机制与输入

机制仍为“I²C NACK/缺席地址的采样信息没有进入可见状态锁存”。v2 在 v1 已登记的 dedicated `missed_ack` 和 Wishbone ERR bit30 子形状上保持原 action 语义，并新增 Wishbone status bit7 子形状。新接口只收内存中的 buggy RTL source closure、冻结 DEV asset 模板与公开 interface/参数/宏说明；不收路径、owner、commit/hash 白名单、任务 ID、注入坐标、clean、TB、日志、oracle、其他 arm 或网络。旧两个子形状可由已冻结 v1 模块委托执行，v2 仅重新封装完整的 v2 witness/receipt；任何 v1 委托的行为变化要另起代次。

bit7 子形状的 sources 精确为 `top`、`byte_ctrl`、`bit_ctrl`、`defines` 四份文本，所有字节参与绑定摘要，大小各不超过 65,536 字节。公开 context 精确为 `interface=wishbone_status_err_bit_7`、`parameters={ARST_LVL: 0}`、`defined_macros=[]`。仅接受顶层一个语法受限的 `include`，其内容必须对应提供的 `defines` closure；不由 binder 自己打开文件。结构 witness 从公开 `wb_dat_o` 的 status-address read 和 status bit7 反推唯一状态寄存器，从 byte controller 的 `ack_out` 连接反推唯一低层 ACK 采样 wire，区分两个 reset 清零与一个正常时序赋值。动作仅把正常时序的 `1'b0` RHS 改为该 wire，不碰 reset 或其他 RTL。此为有界语法/数据流关系，不是完整 Verilog 解析或功能证明。

## Fail-closed 与验收

无匹配/已修好为 `NO_MATCH`；多 module/多候选/额外 writer/角色别名为 `AMBIGUOUS`；不支持的 directive、include closure 缺失或漂移、未知参数/宏、字符串/转义标识符、错误状态路由、非预期 RHS 或来源类型为 `UNSUPPORTED`。binding witness 绑定全部 source hashes、context、唯一编辑 span、原 RHS/替换 RHS/action digest；apply 重新绑定并精确验证，原始 sources 不变，候选只变一处，重复执行拒绝。至少测试三份已见 DEV fault 各唯一 BOUND、clean 各 NO_MATCH、无目标、歧义、相似非目标、非法输入、陈旧/篡改 witness、注释诱饵、无文件/网络旁路。

在独立新根从 v2 action 实际生成 freecores candidate，用预登记的日志语义 oracle 新编译运行：`Check for nack`、`Testbench done`、读写保持 marker 均到达，无 `ERROR` 才能记 DEV candidate PASS；不能以候选等于 clean hash 代替仿真。v1 两份 DEV 也需回归其原生动作/检查。v2 不直接接入 production，也不因 3 个 DEV 设计就授予 TRAIN Memory：若后续重用为 researcher-assisted TRAIN，须重新登记训练角色，在 v2 software 下新执行 fault/candidate/source-rollback 和核心准入，再预选**第四个**未见 target。transform-only 对照必需；无新模型调用。
