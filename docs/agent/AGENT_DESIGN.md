# r2g Agent 设计文档（草案）

2026-10-22 · 状态：待确认

## 1. 目标

把 r2g-skills 做成一个**可下载、模型可自选、界面好看**的 agent，面向比赛评委和外部用户：

- 用户克隆仓库 → 运行安装脚本 → 在配置文件里写上自己的模型 API → 浏览器打开界面。
- 两条入口：**用户提供 N 份 RTL**，或 **让 r2g 自己搜索 N 份 RTL**。
- 用户下"转图"（以后可加"转特征图 image"）指令后，agent 在后台自动跑后端流程、签核、修复，最后产出经过校验的数据集交给用户。
- 遇到失败时 agent 自己诊断、选修法、验证——这是它和固定流水线的根本区别。

## 2. 一条不可违反的原则：模型判断，代码判定

| 交给模型 | 必须是确定性代码 |
|---|---|
| 理解用户意图、拆解步骤 | 后端流程、签核、建图（现有脚本） |
| 读日志诊断**没见过的**失败、选修法 | **通过与否的判定**：DRC/LVS 结论、数据集校验（`verify_graph_dataset.py`）、签核门（`signoff_gate.py`） |
| 决定重试 / 换修法 / 上报给人 | 安装工具链（eda-install 的 bootstrap） |
| 向用户解释进展和结果 | 记账：基线 vs 修复、器具 vs 贡献 |

模型只能**调用**判定工具，不能自己宣布"通过"。没有判决（工具崩溃、超时）一律不算通过。数据集只收签核通过的设计。

## 3. 架构

```
┌────────────────────── 浏览器 ──────────────────────┐
│  对话 │ 任务看板 │ 结果/数据集 │ 设置（模型/平台）    │   webui/（React）
└───────────────▲─────────────────────────────────────┘
                │ HTTP + WebSocket（实时进度）
┌───────────────┴─────────────────────────────────────┐
│  r2g 服务（FastAPI）                                  │   agent/server
│   ├─ agent 循环：模型 ⇄ 工具调用（模型无关）           │   agent/loop
│   ├─ 模型适配器：OpenAI 兼容 / Anthropic / Gemini      │   agent/llm
│   └─ 任务队列 + 后台 worker（长任务不阻塞对话）         │   agent/jobs
└───────────────▲─────────────────────────────────────┘
                │ 工具调用（同一套工具也以 MCP 服务器对外）
┌───────────────┴─────────────────────────────────────┐
│  工具层：稳定的 `r2g` 命令，统一 JSON 输入输出         │   agent/tools + bin/r2g
└───────────────▲─────────────────────────────────────┘
                │ 调用现有脚本（不重写）
┌───────────────┴─────────────────────────────────────┐
│  四个技能：eda-install / rtl-acquire / signoff-loop / def-graph │
│  + knowledge.sqlite（修复经验） + 签核 deck / 规格表           │
└─────────────────────────────────────────────────────┘
```

**为什么工具层同时做成 MCP 服务器**：MCP 是开放的工具接口标准。做成 MCP 后，除了我们自己的界面，Claude Code、Cursor、Cherry Studio 等支持 MCP 的客户端也能直接用 r2g，不绑定任何一家模型。现有用 Claude Code 技能的方式继续可用。

## 4. 工具层（P1）

现状：每一步都已有脚本（输入项目目录，产出 `reports/*.json`），缺的是统一入口、统一的机器可读输出和任务管理。工具层**包装现有脚本，不重写**。

| 工具 | 包装的现有入口 | 返回 |
|---|---|---|
| `env.check` / `env.install` | `eda-install/bootstrap.sh`、`check_env.sh` | 各平台就绪状态（需把文本报告改为 JSON） |
| `rtl.search` | `rtl-acquire` 的 `discover_repo_manifest_candidates.py`、`search_and_expand_until_target.py` | 候选列表 |
| `rtl.import` | 用户上传目录 → `init_project.py` / `promote_candidates.py` | 项目列表 |
| `flow.run` | `run_orfs.sh` | 任务 id；完成后 `stage_log.jsonl`、PPA |
| `signoff.run` | `run_drc.sh`、`run_lvs.sh` / `run_netgen_lvs.sh`、`run_rcx.sh` | DRC/LVS/RCX 判决（代码判定） |
| `diagnose` | `diagnose_signoff_fix.py --list`、`analyze_execution.py` | 排好序的修复方案 |
| `fix.apply` | `diagnose_signoff_fix.py --apply`、`fix_signoff.sh` | 修复后重新签核的判决 |
| `dataset.build` | `run_labels.sh` → `run_features.sh` → `run_graphs.sh`（或 `run_stage_dataset.sh`） | 数据集路径 + manifest |
| `dataset.verify` | `tools/verify_graph_dataset.py --json` | 校验结论（代码判定） |
| `job.status` / `job.cancel` | 新的任务队列 | 进度、日志尾 |
| `knowledge.query` | `knowledge.sqlite` | 某症状的历史修复与成功率 |
| `report.preview` | `render_gds_preview.py`、`render_drc_violation.py` | 版图 / 违例截图 |

命令行形态：`r2g run --rtl <dir> | --search "<关键词>" --count N --platform nangate45 --output graph`，内部按"导入/搜索 → 流程 → 签核 → 诊断修复循环 → 建图 → 校验 → 打包"编排。

## 5. 模型适配（P2）

主流 API 只有三种协议：

| 适配器 | 覆盖 |
|---|---|
| OpenAI 兼容（主力） | DeepSeek、通义千问、Kimi、智谱 GLM、豆包、OpenAI、本地 vLLM / Ollama |
| Anthropic 原生 | Claude |
| Gemini | Google Gemini（亦可走其 OpenAI 兼容端点） |

用户配置 `~/.r2g/config.yaml`：

```yaml
model:
  provider: deepseek          # qwen / kimi / glm / openai / anthropic / gemini / custom
  model: deepseek-chat
  base_url: https://api.deepseek.com
  api_key_env: DEEPSEEK_API_KEY   # 只写环境变量名；密钥不进文件、不进日志
platforms: [nangate45, sky130hd]
compute: {workers: 8, cores_per_design: 8}
```

- 启动自检：连通性 + 是否支持工具调用；不支持就提示换模型。
- 系统提示词由现有 `SKILL.md` 和 `failure-patterns.md` 摘要生成。
- **诊断知识尽量放在代码和知识库里**，模型只做编排——这样能力弱一些的模型也能完成任务。
- 发布前用至少三家模型（DeepSeek、通义、Claude）跑同一套基准任务，结果写进文档。

## 6. 界面（P3）——给评委看

本地网页（FastAPI + React + 成熟组件库，深色/浅色主题）：

1. **首页**：一句话介绍 + **「体验示例」按钮**——内置几个小设计，几分钟内从 RTL 跑到出图，评委零准备即可看到全流程。
2. **对话**：与用户配置的模型对话；agent 的每个动作以步骤卡片实时展示（"综合完成 → DRC 发现 12 处天线违例 → 按报告插入二极管 → DRC 清零 → LVS 匹配"），体现"自己诊断、自己修"。
3. **任务看板**：一批设计的总进度、每份的状态（通过 / 修复中 / 失败原因），可点开看日志。
4. **结果**：版图预览、DRC/LVS 结论、修复前后对比、数据集统计图、一键下载。
5. **设置**：模型与 API、平台、算力。

同一套代码以后部署到服务器即为网页版。

## 7. 长任务

一批设计要跑几十小时，agent 不能等着：

- 工具调用提交任务立即返回任务 id；worker 在后台跑；进度经 WebSocket 推到界面。
- 复用现有的 SQLite 认领队列（`rtl-acquire/vendor/rtl-expander/scripts/processing_queue.py`）和 engineer_loop 的 ledger 思路。
- 吸取 216 上的教训：并发按 `flows × NUM_CORES ≈ nproc` 控制；一次只跑一个批次；容器按名字回收；用"每分钟新产出"而不是负载判断是否卡住。

## 7.1 每个用户自己的知识库（2026-10-23）

仓库里的 `knowledge.sqlite` / `heuristics.json` 是随代码分发的"出厂经验"，技能拒绝直接写它。
agent 首次启动时把它复制到 `R2G_HOME/knowledge/`，之后所有阶段（修复诊断、ingest、学习）都读写
这份副本：每个设计跑完（包括综合失败）都 ingest，随后重新学习 `heuristics.json`。`git pull` 带来
新的出厂经验时，用 `knowledge_sync merge` 并入：只增不改，过诚实性检查，失败则回滚并在
`/api/health` 里显示 `merge_refused`。写入失败会作为任务事件显示，不再静默。用户的经验不会自动
上传；想贡献时手动 `knowledge_sync.py export` 导出经验包，由我们审核后合并。实现：
`agent/r2g_agent/knowledge.py`。

## 7.2 全机并发上限与磁盘（2026-10-23）

网页、`r2g run`、MCP 是三个独立进程。每个设计运行前先占 `R2G_HOME/slots/` 下 `workers` 个
fcntl 锁之一，三者合计不超过上限，进程崩溃时锁自动释放；排队时任务事件写“排队中”。每个设计
记录所属进程（pid + 启动时间），启动时的恢复逻辑只把**所属进程已不在**的设计判为中断——以前
在终端敲一次 `r2g status` 就会把网页服务正在跑的设计全标成中断。设计结束时删除 ORFS 目录里
该 FLOW_VARIANT 的 results/logs/reports/objects（项目 backend/RUN_* 已有完整拷贝，签核需要时
`_restage_for_signoff.sh` 会恢复）。整个工作目录只在用户执行 `r2g clean --yes` 时删除
（默认只列出；只删已结束任务；数据集包保留）。实现：`agent/r2g_agent/housekeeping.py`。

## 7.3 找 RTL 与对话附件（2026-10-23）

`search_rtl`（rtl-acquire 的 GitHub 搜索与质量打分）→ `fetch_rtl`（浅克隆到
`R2G_HOME/rtl_sources`，>500 MB 拒收）→ 按目录列出候选设计：顶层候选、用到却没定义的模块
（`ifdef FORMAL` 等验证代码按嵌套层级剔除）、是否自包含、仓库 LICENSE 识别结果。只有自包含的
才可直接提交；模型必须告知许可证，由用户选择。对话框的回形针上传一个 RTL 文件夹（只发 HDL 和
`$readmem` 数据文件），消息以 `[附件] … 路径：…` 开头，模型把该路径交给 `start_job`。
验证：DeepSeek 真实对话两轮——附件提交任务；“找一个小 SPI 控制器、先别提交”时搜索、拉取两个
仓库、推荐自包含的 freecores/spi 并指出另一个依赖 iCE40 原语，未提交任务。GitHub 上拉下的
alexforencich/verilog-uart 与 ZipCPU/wbuart32 全流程双清、校验 297/297。

## 7.4 批量模式（2026-10-23）

一个文件夹里每个子文件夹是一个设计（`batch.scan`；顶层定不下来的列出原因并跳过，绝不猜）。
入口：网页首页「批量」（上传大文件夹，或直接填服务器上的路径）、`r2g run --batch DIR`、
模型工具 `scan_batch` / `start_batch`。
- **重启不丢**：网页服务启动时接手所属进程已死的设计——排队的继续排队，运行中的先按记录的
  进程组号停掉残留的 ORFS 进程（核对命令行里有该设计的项目路径才停），再从头重跑；同一设计被
  中断超过 2 次判失败。两个服务共用一个 R2G_HOME 时用比较并交换认领，不会重复接手。终端命令
  （`r2g status` 等）什么都不接手、也不改状态。服务正常停止时先结束自己的子进程组。
- **磁盘**：剩余空间低于 `min_free_gb`（默认 50）时暂停开始新设计，调低或清理后自动继续；
  通过的设计默认删掉各次运行的 results/objects/final（只留数据集、报告、日志、RTL，
  `keep_intermediate` 可关），实测 17 个小设计 75 MB。
- **大任务页**：超过 12 个设计时是计数筛选 + 失败原因分组（去掉文件名和数字后归类）+ 搜索 +
  分页；事件只取最近 300 条、5 秒刷新；可随时「下载已完成的部分」，通过的设计可单独下载。
- 并发和磁盘设置即时生效；设置页按本机线程数给出建议值。
验证（208，独立 R2G_HOME）：18 个设计的目录扫出 17 个 + 1 个跳过；运行中 `kill -9` 服务，
重启后 3 组残留进程被停掉、14 个设计恢复、每个设计始终只有一份流程，最终 15 通过 / 2 失败
（两个故意的语法错误，原因写出文件和行号）；TERM 停服务不留子进程；磁盘下限调到 100000 GB
时暂停、调回后 20 秒内继续；2000 个设计的假任务：接口 0.15 秒 / 414 KB，页面 0.5 秒渲染。
注意：agent 的 die/时钟规则是它自己的，不是 216 实验冻结的规格表；要做可比的数据仍用 216 驱动。

## 8. P0：先把验证过的修复沉淀进技能

否则一个新的 agent 不知道这几天学到的东西。现状与落点：

| 修复 | 现状 | 落点 |
|---|---|---|
| 天线：按 DRC 报告插二极管，3 级阶梯 | r1 已在（`deck_driven_antenna_diode`）；r2（累加网单）、r3（`ANTENNAAREARATIO 10`）只在 `experiments/` | `_antenna_catalog` + `fix_signoff.sh` 逐级升级 |
| SystemVerilog：slang 预处理后走原流程 | rtl-acquire 有 slang 开关；"预处理器"版本只在 `experiments/` | 新建 signoff-loop 前端修复目录 |
| 去掉被 include 的头文件 | 只在 `experiments/orfs_spec.py` | `validate_config.py` / 前端修复 |
| `$readmem` 路径修正、从上游补齐缺失数据文件 | rtl-acquire 有路径修正；补齐只在 `experiments/frontend/` | 前端修复 |
| LVS 后备：KLayout 抽取 + Netgen 比对 | 只在 `experiments/lvs45/` | `run_lvs.sh` 旁，比对器无判决时自动启用 |
| 禁用违规单元（METAL1.5） | 通用版 `dont_use_violating_master` 已在 | 确认在 nangate45 上能触发 |
| 跳过布线后增量修复（RSZ-0074） | 只在文档 | `analyze_execution.py` |
| 端口缓冲计入 die 面积 | 只在 `experiments/make_spec_table.py` | `init_project` 的尺寸推导 |

每条沉淀都要：有测试、写进 `failure-patterns.md`、在知识库里留下记录（ingest），并保持"诊断驱动——只对失败的设计应用"。

## 9. 安装（P4）

1. `git clone` → `bash install.sh`：调用 eda-install 的确定性 bootstrap（检测机器、无 root 时走免 sudo 路径、装工具链、写配置、验证）。**安装不靠模型**；模型只在安装失败时读报错给建议。
2. `r2g config`：引导填写模型配置。
3. `r2g selftest`：一个小设计从 RTL 跑到出图，证明环境可用。
4. `r2g ui`：打开界面。
5. 打包：Python 依赖用 `pyproject.toml` 管理（目前仓库没有任何打包文件）；文档写清最低配置（Linux、核数、磁盘）。

## 10. 阶段与工作量（粗估）

| 阶段 | 内容 | 估计 |
|---|---|---|
| P0 | 修复沉淀进技能（上表） | 3–4 天 |
| P1 | `r2g` 命令行 + 统一 JSON + 任务队列 | 3–4 天 |
| P2 | MCP 工具服务器 + 模型无关的 agent 循环 + 三个适配器 | 3–4 天 |
| P3 | 本地网页（对话、看板、结果、设置、一键演示） | 5–7 天 |
| P4 | 安装、自检、多模型实测、用户文档 | 2–3 天 |
| 并行 | 130 / 180 基线与 held-out 验证（论文证据） | 持续 |

合计约 3–4 周。P0 与 P1 可以立即开始（只在 208 上写代码，不影响 216 上的实验；216 的部署副本等实验结束后再更新）。

## 11. 风险

- **模型工具调用能力不一**：靠"知识在代码里"降低依赖；多模型实测。
- **算力门槛**：真实批次需要多核 Linux；一键演示只用小设计，几分钟可完成。
- **与实验冲突**：实验期间不更新 216 上的部署；改动全部走测试。
- **"转 image"未实现**：现有是图数据集；特征图（CircuitNet 风格）需新增一个阶段，数据（放置/布线 DEF）已具备。

## 12. 待确认

1. 目录布局：新增顶层 `agent/`（Python 包）和 `webui/`，技能目录不动——可以吗？
2. 第一版支持哪些平台：nangate45 + sky130hd（gf180 跟进）？
3. 输出类型：第一版只做图数据集，特征图放第二版？
