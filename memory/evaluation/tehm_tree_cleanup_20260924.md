# TEHM 文件树清理记录（2026-09-24）

本记录只说明本次文件清理，不改写旧实验的原始 verdict、manifest 或 digest。清理前 Git HEAD 为 `4ea55665cd6e6828a46afe98e683f76c654145a7`；其历史保留下面删掉的 Git 跟踪内容。

## 已移除

- 整个 `memory/tests/`：308 个 Git 跟踪文件（196 个 `test_*.py`、110 个 fixture、`__init__.py` 与 `conftest.py`），以及目录内忽略的 pytest/Python 缓存和一份测试生成的 `fix_log.jsonl`。用户已明确接受旧实验入口和证据路径因此失效。
- 五个不被当前 R4 CLI 调用的旧入口：`memory/scripts/freeze_evidence.py`、`freeze_evidence_v3.py`、`verify_evidence_freeze_v3.py`、`run_frozen_regression.py`、`cleanup_campaign_disk.sh`。前四个是旧证据/完整测试链，其中运行测试的路径现已失效；最后一个是针对已不存在 `orfs-v1` 的旧一次性磁盘清理脚本。全部为 Git 跟踪文件，可从清理前提交恢复。
- `memory/` 范围内 26 个可再生 `__pycache__`/`.pytest_cache` 目录，以及一个被忽略的零字节 `memory/tehm.sqlite`。**没有**对被忽略但重要的 `memory/docs/` 执行宽泛 `git clean`。
- 顶层空目录 `/data1/zhangdy/tehm-contract-action32-v128v133-r2`、68 KB 的旧 `/data1/zhangdy/tehm-maintenance`（2026-09-13 清理脚本及三份旧 XML 报告），以及 `/data1/zhangdy/tehm-tmp-archives` 的两份 2026-09-13 `/tmp` 归档快照，合计清理前约 151 GB。两份归档计划的 SHA256 分别为 `dfd9526cb17b58bd16ab950f7909a2367758f265ae57c60404418435572d62a1` 和 `98cbfb42390ee2ccded226f29be1e3b5ae1054766abd8b7e6f961ed055a61253`；归档是独立目录，删除后不能依靠本仓库 Git 恢复。

## 保留及已知影响

- `memory/tehm/`、当前 `memory/scripts/research_pilot.py`、`memory/evaluation/`、`memory/docs/`、仓库 `evidence/` 和外部 `tehm-campaigns/r4-real-pilot/` 原始产物均未清理。后者约 4.3 GB，当前 R4 S0/S1/S2/M0 的计划、审计、只读 M0 和事件链仍在。
- `memory/evaluation` 的旧 manifest 及历史 README/冻结证据中仍保留 `memory/tests/fixtures`、旧脚本和测试回归的**历史引用**。这些现在不是可运行路径；不为了让旧结果看起来可复现而修改已冻结内容或哈希。清理前的 `2085 passed` 是历史事实，**不是清理后测试覆盖率**。
- `tehm-campaigns` 除 `r4-real-pilot` 以外约 131 GB 的历史 campaign 尚未删除：其中包含 source-bound portable packages、原始 ORFS traces 和旧审计，不全是可再生缓存。待确定保留旧原始审计还是接受整批删除后再处理。

## 当前入口验证

在删除上述内容后，`PYTHONPATH=memory PYTHONDONTWRITEBYTECODE=1` 下 `memory/scripts/research_pilot.py --help` 退出 0；同一 CLI 的 `verify-s2-pilot` 对冻结的 S2 stage plan、完整事件链和六份独立 raw flow audit 只读复核退出 0。此验证仅证明当前 R4 S2 验证入口仍可运行，不能代替已删除的完整单元测试套件，也不声称所有旧脚本仍可运行。
