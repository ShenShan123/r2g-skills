# Revision5：两份已见 TRAIN 的来源关系限定判定

状态：**仅对固定的 `axis_register[8-2]` 与 ZipCPU `skidbuffer` TRAIN
源码，判定为两个有证据支持的不同 source lineage；不证明故障统计独立，
不授予 Knowledge/Asset authority 或跨来源目标迁移。** 先前 DEV 历史报告
冻结时的 `independent_lineages_verified=false` 保持其历史语境；本次是
TRAIN 回执出现后另建的限定 adjudication，不回写原报告。

只读审计器 [`research_r5_train_lineage_audit.py`](../tehm/evaluation/research_r5_train_lineage_audit.py)
SHA-256 `1d729a3bafadc414713c1df0bb4aa84dc00e7814caa4e52e64d8301329239c25`
重新核验已封存的 GitHub API 原始提交 JSON、origin Git blob、两个 clean
checkout 的 HEAD、两份 TRAIN 预登记与新执行回执；并复用全 RTL 树跨项目
逐字节、去注释/空白文件和长 8 行窗口的重合筛查。既有 DEV 历史审计
曾得出 `[31, 63]` 个 RTL 文件、零相同文件、零实质相同窗口；本审计
要求这些结果和全部相关 SHA 不漂移，而不是只信目录 owner。

| 目标源码 | 首次文件提交 | 首次提交作者及内容 | 本次源码闭包 |
|---|---|---|---|
| Alex `rtl/axis_register.v` | [`74fa967`，2014-09-14](https://github.com/alexforencich/verilog-axis/commit/74fa9670712d2252493872681a653403689e8fc5) | Alex Forencich；新增该模块及其原测试 | 固定 SHA `599fde2d...`，单一 `axis_register` 模块，无 include |
| ZipCPU `rtl/skidbuffer.v` | [`ded500c`，2019-05-16](https://github.com/ZipCPU/wb2axip/commit/ded500c75dba4c528bf642947461227785365cbc) | ZipCPU；新增模块与 formal 配置 | 固定 SHA `ed1fda91...`，单一 `skidbuffer` 模块，无 include |

两仓库的冻结 GitHub 元数据 `fork=false`；目标文件各自标注不同作者、
许可与项目，均无指向对方项目的归属声明。两个目标不是同一个 DUT，
无共享 RTL 文件或 `include` 闭包；AXI skid-buffer 协议思想相同不等于
复制同一实现。ZipCPU 作者的[2019 年原始设计说明](https://zipcpu.com/blog/2019/05/22/skidbuffer.html)
也叙述了该模块的构建与验证背景，作为作者来源线索，不代替冻结的
commit/blob 和代码重合审计。所有这些只能降低显著复制/共同生成的风险，
不能从逻辑上排除私下改写或外部生成器。

Evaluator-only 原始回执：
`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/source-locks/train-lineage-v1.json`，
文件 SHA-256 `0dc2c01f68c8896721cbf3f05af093bf720d495b5ff820e74b1c1e284c9f0394`，
内部审计 digest
`sha256:ec0f9b1d9ac4e68a097e668f6a5245cd0504c5d4b13f4f58263a049396d1dfe2`。
独立进程 `--verify` 冷重建完全相同字节：

```sh
PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_train_lineage_audit \
  --verify /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/source-locks/train-lineage-v1.json
```

该回执把来源判定绑定到两份具体 TRAIN receipt、原始源码 SHA 与 role。
`axis_broadcast` 仍与 Alex register 算一个来源组；未来目标需另审计并防止
与 TRAIN 同源码/同 bug 的答案泄漏。当前只是**来源分组前置条件**通过：
核心 verified transition、Knowledge L3 复制证据、Asset 独立 oracle/保持
与回滚、三态 snapshot/delta 仍缺。现行 v3 Asset lifecycle 的 lineage
gate 保持 fail-closed，不能把本回执布尔字段直接传入 promoted 状态。
