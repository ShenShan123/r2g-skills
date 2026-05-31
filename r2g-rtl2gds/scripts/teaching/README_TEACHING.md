# R2G 教学任务套件 · 安装与使用说明

这一套东西分两部分：

- **`repo_drop_in/`** —— 放进 **agent-r2g 仓库**（学生 `git pull` 时一起拉到）。
- **`teaching_root/`** —— 学生在自己 VM 上放置的目录，里面只有一个
  `TEACHING_POLICY.md`（教学模式的"开关"）。

## 1. 放进 r2g 仓库的内容

把 `repo_drop_in/scripts/` 合并进仓库的 `r2g-rtl2gds/scripts/`：

```text
r2g-rtl2gds/scripts/
  ledger/                     # 运行账本（防伪造证据链；41/41 单测通过）
    canonical.py              #   - canonical JSON + record_hash（写入端/校验端共用）
    append_ledger.py          #   - 账本写入器（CLI+库；含 agent_direct 拒写护栏）
    metrics_parsers.py        #   - 每个 step 一个解析器；解析失败吐 None，不编零
    __init__.py
  teaching/
    run_stage.sh              # 统一 stage 运行器（调 SKILL 流程脚本 + 自动写账本）
    status_enums.py           # 所有状态枚举/必需 CSV/平台锁 的唯一真相源
    verify_submission.py      # 报告检查 / autograder（单份 + 跨提交查重）
    check_my_case.py          # 学生提交前自检（复用 verify 逻辑）
    README_TEACHING.md        # 本文件
```

提交到 GitHub 后，学生在 `/home/zhz/`（或任意目录）`git pull` 即可获得这些脚本。

## 2. 关于 CLAUDE.md：可以删

`CLAUDE.md` / `AGENTS.md` 的唯一特殊作用是被 Claude Code / Codex **自动加载**。你的
新模型是"学生用 §9 的 stage prompt，每次先读 TEACHING_POLICY.md"，所以这个自动加载
入口是多余的——我已经把原 CLAUDE.md 的 5 条核心规则折进 policy 的 §2 诚信底线。

**删 CLAUDE.md 是安全的**，因为真正的强制点不在 agent 运行时，而在**提交时的
`verify_submission.py`**：不管学生读没读 policy、用没用脚本，提交上来一律按 policy
规则机器校验，绕过 policy 的提交会被判 INSUFFICIENT_EVIDENCE / 标红。

唯一代价：学生若**不用** stage prompt、直接裸聊，agent 就没有任何运行时约束兜底。
如果你想保留一个极薄的兜底，把本套件里的 `AGENTS.md`（5 行指针，可选）放到仓库根；
不想要就删掉它。二选一即可，不必同时保留 CLAUDE.md 和 AGENTS.md。

## 3. 学生怎么用

1. `git pull` 更新后的 r2g 仓库到 `/home/zhz/`（或任意目录）。
2. 把 `TEACHING_POLICY.md` 放到教学工作区（如 `/home/zhz/r2g_teaching/`）。该目录即
   `<teaching_root>`，产物会写到它下面的 `cases/<design>/`。
3. 按 policy §9 的 prompt 逐阶段跑：

   ```text
   请先读取本课程的 TEACHING_POLICY.md。
   design name: usb_cdc_top
   RTL 路径：<RTL_PATH>
   执行 Stage 1。
   ```

   agent 会调用 `run_stage.sh`，它内部按 SKILL.md 跑真实流程脚本，并在每步结束时
   **自动写账本**（学生/agent 不手写账本）。
4. 跑到 Stage 4 后，提交前自检：

   ```bash
   python3 r2g-rtl2gds/scripts/teaching/check_my_case.py --teaching-root /home/zhz/r2g_teaching
   ```

   它会列出每个 design 会被标红的项。**如实记录的失败/阻塞不会被扣分**；只有无证据的
   PASS、伪造、切平台（红线 3）、抄袭（跨提交撞哈希）才会出问题。

## 4. 教师怎么批

收齐所有学生的 `teaching_root` 后（每人一个目录，放在一个 batch 目录下）：

```bash
python3 r2g-rtl2gds/scripts/teaching/verify_submission.py --batch /path/to/all_submissions
```

产出：
- 每个学生目录下：`status_summary.csv`（每行一个 design×stage + 校验结果）、
  `SUBMISSION_REPORT.md`（人读版）。
- batch 根目录：`COLLISIONS.csv`（跨提交内容撞哈希 = 抄袭/共享，需人工裁定源头）。

把所有 `status_summary.csv` 一拼，就是你升级 r2g 要的全班失败模式数据集。

## 5. 校验器查什么（与 policy 对齐）

`verify_submission.py` 逐 (design, stage)：

1. **状态枚举合法**：状态必须在 policy §5/§6 的集合里（来自 `status_enums.py`）。
2. **报告存在**：CASE_STATE 引用的阶段报告文件真实存在。
3. **PASS 必须有证据**：声称 `STAGE4_EXTRACTION_PASS` 必须 4 个 label + 8 个 feature CSV
   都在且非空；`*_LABELS_PASS_*` / `*_FEATURES_PASS_*` 分别查对应组。
4. **无机器绝对路径**：report / CASE_STATE 里不得出现 `/home/`、`/data1/` 等（policy §3）。
5. **平台锁**：CASE_STATE 的 `platform` 必须是 `nangate45`（红线 3）。
6. **UNKNOWN 占比**：`nodes_gate.csv` 的 `cell_type_id` 大面积为 95(UNKNOWN) 会被标红
   （多半是切了平台或缺 nangate45 库）。
7. **账本（若存在）**：校验哈希链连续、每条 record_hash 自洽、无 `agent_direct` 写入。
   账本缺失记为软提示；账本存在但断链/篡改视为伪造嫌疑。

跨提交：对 `.gds` / `.def` / `5_route.def` / 12 个 Stage-4 CSV 算哈希全局比对，
≥256 字节才参与（避免空/表头文件误报），同一哈希出现在 >1 个提交即标红。

## 6. 调参位（按你们实际情况）

- `status_enums.py::FORBIDDEN_PATH_SUBSTRINGS` —— 若你们机器的 home 前缀不同，按需增减。
- `status_enums.py::UNKNOWN_SHARE_WARN_THRESHOLD` —— UNKNOWN 占比告警阈值（默认 0.5）。
- `verify_submission.py::MIN_DUP_BYTES` —— 跨提交查重的最小文件字节数（默认 256）。
- `verify_submission.py::DUP_SUFFIXES` —— 参与查重的产物类型。

## 7. run_stage.sh 的待接线处

<<<<<<< Updated upstream
`run_stage.sh` 的 Stage 1–3 已按 SKILL.md 文档化的脚本签名接好（`# >>> FLOW` 标记处）。
**Stage 4 Part A（4 个 label）也已接好**：从 `CASE_STATE.md` 读 `def_path` / `odb_path`，
调 `scripts/extract/labels/` 下的 `extract_wirelength.py` / `extract_congestion.py` /
`extract_timing.tcl` / `extract_irdrop.tcl`，输出强制规范名，每步落账本。

**只剩 Stage 4 Part B（8 个 feature）的入口待你确认**：feature 脚本在
`scripts/extract/features/`（`metadata.py` / `nodes_*.py` / `edges_*.py` + `case_paths.py` /
`cell_type_map.py` / `lib_db.py` 等）。仓库截图里**没看到统一入口 `run_all.py`**——如果有
就把它接到 `run_step stage4 feature_extract ...`；如果没有，就按各脚本真实签名逐个接。
脚本里标了 TODO 注释，**不要臆造入口**。

可以先用 `DRY_RUN=1 bash run_stage.sh 4 <design>` 干跑，确认 Part A 的四条命令和账本写入
正常（已验证），再接真工具与 Part B。
=======
`run_stage.sh` 的 Stage 1–3 已按 SKILL.md 文档化的脚本签名接好（`# >>> FLOW` 标记处）；
若你们仓库的脚本名不同，改这些行即可。**Stage 4 的 label/feature 命令是 design 相关的**
（依赖 DEF/ODB/lib 路径），脚本里给了 `run_step` 包装的模板注释，把它们接到你们的
`<label_script_root>` / `<feature_script_root>` 即可——关键是每个脚本调用都要经过
`run_step`，这样才会落进账本。

可以先用 `DRY_RUN=1 bash run_stage.sh 1 <design>` 干跑，确认编排和账本写入正常，再接真
工具。
>>>>>>> Stashed changes

## 8. 验证状态

- 账本模块：41/41 单测通过（`scripts/ledger/` 旁附 `run_selftest.py`，无第三方依赖）。
- `verify_submission.py` / `check_my_case.py`：已用合成的"合规 / 伪造 / 抄袭"三类提交跑通，
  正确区分。
- `run_stage.sh`：`bash -n` 语法通过；EDA 编排部分需你们在真实环境接线后端到端验证。
