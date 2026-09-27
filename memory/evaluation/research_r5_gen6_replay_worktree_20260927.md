# R5 gen6 软件重放入口（2026-09-27）

在开始 v9 `memory/tehm` 源码修改前，新增只读用途的 detached Git worktree：`/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/software/frozen-gen6-f51f8fc`，HEAD `f51f8fc6d269a405e6044e7efa05b3665a58025c`，创建后 clean。这里是 gen6 v8 的原软件身份；后续主工作树增加或清理 Python 文件不能被误认作它的重放代码。

从该 worktree 执行 `PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1 python3 -m tehm.evaluation.research_r5_train_m0_v8 verify --output /data1/zhangdy/RTL/RTL_testbench/_r5_pilot/memory/r5-v8-gen6-m0-r1`，冷验证 `valid=true`，report digest `sha256:af461d667815d501723fbf9b95781bc2d21d325aa8cadaa99085b63bc0d6840d`；M− 为 `NO_SKILL/NO_SKILL`、M+ 为 `CONSIDER/SELECT`、Mremove 为 `NO_SKILL/NO_SKILL`。这是已有 TRAIN Memory 的独立软件入口复验，不是目标 oracle 或 ΔMemory attribution。

此 worktree 与原仓库仍共享同一 `.git` 对象库及本机存储故障域，**不满足** R5 §19.4 要求的独立第二备份。发布或删除唯一原始证据前仍须单独完成可校验备份与隔离恢复；本记录不授权自动 push 或清除旧工件。
