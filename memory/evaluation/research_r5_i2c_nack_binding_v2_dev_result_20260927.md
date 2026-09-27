# R5 I²C NACK 状态锁存 v2 DEV 结果（2026-09-27）

依据[事前 v2 合约](research_r5_i2c_nack_binding_v2_dev_contract_20260927.md)，新增
`tehm.evaluation.research_r5_i2c_nack_binding_dev_v2` 和独立对抗检查。冻结 v1
在原 freecores 目标候选上返回 `UNSUPPORTED` 的结果不变；本代把该来源明确
列为 `DEV_OBSERVED`，不作为 v2 未见 target 或 FINAL。

v2 对 alexforencich dedicated missed-ACK 和 ZipCPU Wishbone ERR bit30
两种已见形态委托未修改的 v1 action；新增 freecores Wishbone status bit7
四文件 source-only 绑定。输入仅为内存中的 buggy RTL closure、公开
`interface`／`ARST_LVL=0`／空宏配置和 DEV 模板。binder 不接收路径、clean、
testbench、oracle、mutation manifest 或 Memory；所有四份源码的 hash 与
唯一编辑 span 纳入 witness。它只给语法受限的动作建议，功能裁决归原生
testbench，Memory 权威仍为 false。

三份已观察 DEV fault 的 v2 绑定均唯一 `BOUND`，各自产生一处 action，候选
逐字节等于登记 clean；三份 clean 均 `NO_MATCH`。v2 对抗检查 7/7 通过，
覆盖无目标、额外 writer／模块、状态路由与端口别名、未知 context／宏、
closure 漂移、篡改 witness、注释诱饵及文件／网络旁路。冻结 v1 检查另行
回归 10/10 通过。检查结果是 DEV conformance，不是 oracle 修复率。

在独立 DEV 根
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/i2c-nack-binding-v2-freecores-r1/`
从固定上游 commit `3b067f00ccced753b0502024766a51f58f3e04bc` 建新 archive，
放入已登记 fault 顶层 RTL，再由 v2 action 实际生成 candidate。差异只在
正常分支 `rxack <= 1'b0;` → `rxack <= irxack;`，两条 reset clear 未变。
按先前冻结的七文件、无宏 Icarus 11.0 范围新编译与仿真（60／120 秒上限），
两步 exit 0、stderr 空；原生日志到达 `received a5`、`received 5a`、
`Check for nack`、`Testbench done`，无 `ERROR`。本已见 DEV candidate 对
登记 scope 的原生判定为 **PASS**，不以 clean hash 一致代替仿真。

最终 [audit.json](/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/dev/i2c-nack-binding-v2-freecores-r1/audit.json)
SHA256 `823149b38301de969cb9969641a0f107dd9fd7792f8f945337eba554034be0d6`，
`valid=true`，封存 32 个原始／构建／回执文件 hash。另起进程冷审重算所有
文件 hash、binder 和检查代码身份、binding/action、候选字节和日志语义，
全部通过。首次 audit 因脚本用 `replace(..., 1)` 误指向 reset 分支而得到
`valid=false`，原文件保留为 `audit-initial-failed.json`；更正后的中间通过
回执也保留为 `audit-before-hardening.json`，不能抹去失败路径。

**未完成**：v2 尚无重新登记并回滚验证的合法 TRAIN Memory，也未预选第四个
未见独立目标；没有 T、三态配对 A、ΔMemory 归因或论文统计正结果。
下一步若要重用这三个 DEV 来源作 researcher-assisted TRAIN，先在 v2 软件
身份下重新执行 fault／candidate／source-rollback 与核心准入，冻结 M0 和
transform-only 对照，再预登记第四来源的目标；不能把本 DEV PASS 直接授予
Memory。没有模型调用、production promotion 或 GitHub 推送。
