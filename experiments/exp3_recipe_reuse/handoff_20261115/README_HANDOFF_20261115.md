# 实验三子实验一：尚缺有效 recipe 的设计交付

交付位置：`192.10.84.203:/data1/zhangdy/RTL/test/`。

| 平台子目录 | 设计数 |
| --- | ---: |
| nangate45/ | 14 |
| sky130hd/ | 8 |
| gf180/ | 29 |
| 合计 | 51 |

以实验三的固定 676 个设计为总体，其中原 R2G recipe 未修复 107 个。排除后续已验证 learned recipe 成功的 33 个，再排除记录中超过 30,000 个单元的 20 个，以及已知 64 GiB 内存超限的 3 个，交付其余 51 个。规模依据取 baseline 综合、R2G sizing、最终 stdcell 数中的最大记录值。29 个综合失败设计尚无可靠单元数，明确标为未知并保留供前端问题排查，不能据此认定规模小。

按已有证据分为三种优先级，全部设计均在三个平台文件夹内：

- 24 个 `1_known_manual_fix`：已有模型 fallback 成功记录，但没有本次筛选依据中的有效 recipe，适合优先归纳修复规则。
- 10 个 `2_diagnose_remaining_failure`：尚需排查综合、LVS 或工具问题；不能保证修复成功。
- 17 个 `3_recover_authentic_memory_assets`：缺少存储器初始化资源，先从真实源仓库恢复资源，再处理综合。不能伪造 ROM 内容或简单清零。

每个设计目录包含：

- `original/`：原始 RTL 及资源、design.json；615 份 manifest 中登记的原始 RTL 文件均已核对 SHA256。
- `r2g_stop/`：R2G 停止时的 RTL、约束、必要 hook、失败报告和有界日志，保留自动 frontend 处理后的状态。
- `baseline/`：原基线配置、约束、结果及日志，供对照。
- `README.md`、`HANDOFF.json`：top、失败原因、规模依据、已有修复证据和优先级。
- `reproduce.sh`：设置 `ORFS_ROOT` 并加载 EDA 环境后，用 `bash reproduce.sh r2g_stop synth` 重现综合；目标 `finish` 执行后续布局布线，`original` 模式从原始输入开始。输出写入本设计的 `work/`。
- `FILES.json`：交付文件的哈希清单。

可运行 config 的项目内路径已改为 `HANDOFF_ROOT`；`config.recorded.mk` 保留原始绝对路径用于追溯。历史报告和元数据里的路径是来源记录，不是交付依赖。EDA 工具和 PDK 需使用接收者已有环境。

本交付未运行新的 EDA 或模型任务。现有 test 目录内容保留，新增文件对 zhangdy 赋予读写权限。`SELECTION_20261115.json` 记录全部 107 个候选的入选或排除原因，`HANDOFF_MANIFEST_20261115.json` 为交付统计，`VERIFICATION_20261115.json` 为最终核验记录。
