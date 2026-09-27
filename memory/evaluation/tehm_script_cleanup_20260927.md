# TEHM 旧验证脚本清理（2026-09-27）

按用户要求，从当前主工作树退役 12 个无现行可执行调用的旧 R5 检查入口，共 1,964 行：

- `research_r5_asset_authority_v3_gate_checks.py`
- `research_r5_asset_binding_v7_checks.py`
- `research_r5_axis_adapter_q_checks.py`
- `research_r5_axis_adapter_recovery_checks.py`
- `research_r5_rtl_scoped_v3_checks.py`
- `research_r5_rtl_scoped_v4_checks.py`
- `research_r5_rtl_scoped_v5_checks.py`
- `research_r5_skid_binding_v4_checks.py`
- `research_r5_skid_binding_v5_checks.py`
- `research_r5_skid_binding_v6_checks.py`
- `research_r5_skid_action_v7_checks.py`
- `research_r5_train_asset_authority_v7_checks.py`

删除前工作树干净；Python import 扫描及仓库非 Markdown 文本检索确认这些入口没有现行调用（被删除的 v4 scoped checks 对 v3 checks 的引用也一并退役）。保留 v8/v9 检查、统一 `research_pilot.py` 和冻结 gen6 worktree。较早编号的 binder、action、adapter、rollback、authority 模块仍被当前兼容链调用，不能按编号删除；历史文档提到的退役检查命令不再承诺在主工作树运行。

## 删除前备份与隔离恢复

在另一文件系统 `/tmp/tehm-script-cleanup-20260927.XHoMK9/` 创建完整 Git bundle：`pre-cleanup.bundle` SHA256 `cc812b0a18d20453ebfb1015ae9169de76728865ae369edfe85d84f49c700ff1`；`git bundle verify` 通过，完整包含删除前 `publish-memory` 提交 `33aaa787c07ad5776efa34bebd6027d29e923b20`。从 bundle 在同目录隔离克隆并检出相同提交。另复制完整 v9 APEX DEV case、干净源和 C1 负控，case 树与原件 `diff -qr` 一致。恢复副本运行 v9 9/9 检查；使用冻结 Verilator 5.038 从所复制的候选源和 testbench 重新构建，`+TARGET` 与 `+PRESERVATION` 均 PASS。工具链仍引用 `/data1/zhangdy/RTL/RTL_testbench/_r5_pilot/`，不是跨主机 hermetic 恢复。`/tmp` 与工作区不在同一磁盘，但它是临时存储，不应视为长期异地归档。

## 删除后限定回归

- v9 source-only binder：9/9 通过。
- v8 binder：先误用不属于 v8 grammar 的 APEX source，19 项中出现失败；改用冻结的 `gen6-mux-skid-v8-r1/original/sources/fault/source.sv` 后 19/19 通过。前次失败不是清理造成的代码回归。
- v8 TRAIN asset authority：30/30，`valid=true`。
- 统一 `research_pilot.py --help`：退出 0。
- 从未改动的 `frozen-gen6-f51f8fc` worktree 冷验 M0：`valid=true`，report digest 仍为 `sha256:af461d667815d501723fbf9b95781bc2d21d325aa8cadaa99085b63bc0d6840d`。

本次仅退役主工作树脚本，不删除原始实验数据、证据或冻结软件；没有模型调用、没有 GitHub 推送。旧脚本可从删除前提交或上述 bundle 精确恢复。根目录 gen6 verifier 不应被用来给修改后的主工作树冒充原软件身份。
