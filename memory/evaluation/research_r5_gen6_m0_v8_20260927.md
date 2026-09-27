# R5 gen6 v8 TRAIN Memory 三视图

## 结果与范围

新建软件 epoch `f51f8fc6d269a405e6044e7efa05b3665a58025c`，绑定 v8 Asset strict authority 和 v8 canonical／Knowledge RAM 两份冻结前置。`research_r5_train_m0_v8.py` 从同一批三个已登记 reused-DEV TRAIN 任务构建六条 control/treatment canonical transition、三对 L2、一个基于三个固定来源组的 L3 path，再经核心严格 Knowledge／Asset receipt 显式建立研究用 M+。没有模型调用、新 simulator 执行、heldout 目标或 production promotion；Asset 留在 candidate，选择器是 shadow-only。

| 冷加载视图 | TRAIN 查询 route | Asset selection | 语义行数 |
| --- | --- | --- | ---: |
| M− | `NO_SKILL` | `NO_SKILL` | 1 |
| M+ | `CONSIDER` | `SELECT` | 146 |
| Mremove | `NO_SKILL` | `NO_SKILL` | 1 |

Mremove 从 M+ 删除本代全部训练增量及其依赖派生状态，覆盖 20 张表；它与 M− 的 41 张 TEHM 表逐行语义等价，且实际 route/selection receipt 相同。这里的三视图变化只在 AXIS 的 **TRAIN 消费预检** 中观察到，不是对未见任务执行三臂、不是 fresh oracle 修复收益，更不是 ΔMemory attribution。三来源组仍只是有界记录的文件来源组，不证明统计独立；ZipCPU payload oracle 仍属 augmented。

## 冻结身份与复验

- epoch：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/epochs/r5-v8-gen6-m0-epoch-r1.json`，SHA256 `d382ccef94b0975dbb1a4a267bd835066fb74cc11022cb392e7ac870b04226da`。
- 输出：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/r5-v8-gen6-m0-r1`；build report SHA256 `da2a9b1853bc9a0306f783301cd1ea66de9270ea2a2e1cca80286bd80c9d960b`，内部 report digest `sha256:af461d667815d501723fbf9b95781bc2d21d325aa8cadaa99085b63bc0d6840d`。
- bundle digest：M− `f6597b8e8e5eb991236e136d0b50aa988aa3b13ce0b588b08dca6a92fb2e632a`；M+ `cf2030eb7231a4d87f76e5fa1f6796fd7c13aed01b102bc2febfb54f42ee1cff`；Mremove `e26d8670c510fd970ecb25926299e1e7d0d0a8d3ed2cc531516386bd3c802001`。
- 独立复验命令：`PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_train_m0_v8 verify --output /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/r5-v8-gen6-m0-r1`。它冷加载三份 bundle，核对 manifest、spec 与 sidecar 摘要、完整增量闭包、M−／Mremove 等价，并在冻结 acquisition 的 RAM replay 上下文内重审六条原始 TRAIN、历史 Knowledge authority、Asset authority 和 M+ source-only selection；本次运行返回 `valid=true`。

旧 gen5 epoch、M0 与失败／负结果不被覆盖。下一阶段须预先选定真正未见 RTL scope，冻结当前 runtime/Memory/controller/oracle/binder，在相同 task 上分别加载 M−／M+／Mremove，重新路由、绑定、构建候选并执行 fresh native／合格 augmented oracle。只要来源关系或 oracle 判定力不满足，保留 NO-GO／UNKNOWN，不把本次 TRAIN 内 `NO_SKILL/SELECT/NO_SKILL` 当成目标修复率。
