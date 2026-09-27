# R5 I²C NACK 状态绑定 DEV 合约（开发前冻结，v1）

## 角色与先验

本代只用已登记的 `alexforencich/verilog-i2c` missed-ACK 输出 DEV fault 与 `ZipCPU/wbi2c` WB ERR bit-30 DEV fault 开发、回归。两者源码和单点修复在开发者工作区可见，故均非本代独立 TRAIN、PILOT_TRANSFER 或 FINAL_TEST。合约前的原生 clean/fault 判定力分别见 `research_r5_i2c_dev_oracle_result_20260927.md` 与 `research_r5_zipcpu_i2c_nack_dev_result_20260927.md`。本卡不授予 Memory 或生产 authority。

## 机制、允许输入与动作

唯一机制类是“从低层 I²C 缺席设备/NACK 事件到公开状态位的锁存丢失”。`bind(asset, buggy_source, public_context)` 只收内存中的单文件 UTF-8 RTL、冻结的 DEV asset 模板及公开接口契约；不收路径、仓库名、source hash 白名单、task ID、测试输出、gold、mutation 坐标、其他 arm 结果或 oracle 句柄。上下文只允许 `interface` 为 `dedicated_missed_ack_output` 或 `wishbone_status_err_bit_30`，且 `defined_macros=[]`。binder 不读取文件／网络，不执行 RTL、测试或候选搜索。

`dedicated_missed_ack_output` 结构需从公开 `missed_ack` 输出推导唯一状态寄存器、与之配对的 next-state 值、ACK 采样来源、非 reset 常值覆盖和 reset 清零；动作仅把非 reset 寄存器赋值 RHS 从 `1'b0` 改为已绑定的 next-state 信号，不碰 reset。`wishbone_status_err_bit_30` 结构需从公开 `o_wb_data` 的状态字 bit30 推导唯一错误锁存位、busy 和低层错误条件；动作仅把 `busy && low_level_error` 条件下对该锁存位的 `1'b0` 改为 `1'b1`，保留合法清除分支。两种子形状输出同一机制类但不同 action variant；不得从接口名称猜测内部信号或硬编码改动行号。

## 边界与拒绝

支持单个 module、声明的空宏环境、已知非语义 directive（`timescale` 或 `default_nettype none`）与注释遮蔽。其他 active directive、字符串/转义标识符、非 ASCII/控制字节、过大源、多个 module、额外/别名 writer、对外状态链不唯一、next-state/低层错误无明确证据、条件与 reset 不能区分时 `UNSUPPORTED` 或 `AMBIGUOUS`；无目标结构或已正确的源为 `NO_MATCH`。绑定 witness 包含源/context 摘要、角色、精确待替换 span、原 RHS 与 action digest；apply 重新绑定并逐字段验证，仅产生单 span 编辑及 `NOT_EVALUATED` receipt。原始源不变，候选重绑为 `NO_MATCH`，错误 context、陈旧或篡改 witness 拒绝。

## DEV 验收

先测两份注册 fault 各唯一 BOUND、clean 各 NO_MATCH，再测无目标、双 module 歧义、额外 writer/相似假结构、错误参数、注释诱导、非法 directive、幂等、witness 篡改、文件/网络旁路禁止。有效候选交给各自已冻结原生 DEV oracle 重新编译/运行：目标 PASS、原保持义务 PASS 才算本代 DEV 动作有效。不能用“候选与 clean SHA 相同”替代运行。alpha-renaming 仅为实现一致性，不算跨来源迁移。

完成这些检查后才能冻结这代 DEV software；如修改 binder、动作或 oracle，必须另起 generation。之后另找合法 TRAIN 来源和未见 target，不能把这两个 DEV 样本改名重用，也不能因为 transform-only 可修复而声称 ΔMemory 增益。
