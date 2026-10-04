# R2G — ICLR 2027

当前唯一论文工程。交付文件：

- [论文 PDF](main.pdf)
- [Overleaf 源码包](iclr_r2g_source.zip)：直接上传，选择 `main.tex` 编译。
- [LaTeX 入口](main.tex)

保留 ICLR 2027 官方匿名审稿模板、页边距、正文字体、行距及行号。正文只包含已经完成的研究；未执行投稿。

## 图表与结果

图表采用统一的蓝、青、珊瑚与金色系，配合无衬线图内字体、分面标题、浅色三态矩阵、三线表与适当留白。配色受作者给出的 [Nature Communications 论文](https://www.nature.com/articles/s41467-026-74781-8)启发，是适配本论文的设计，并非官方色卡或原图色值复制。所有正文图均为嵌入字体的矢量 PDF。

正文、表格和图中统一使用完整模型版本：GPT-5.6-sol、Claude Opus 5、Qwen3.7-Max、Kimi-K2.7-Code、DeepSeek-V4-Pro、grok-4.5、Gemini-3.1-Pro-Preview。

E4 使用 600k Token 上限的固定 24 题复测，详见[结果口径说明](e4_update_zh.md)。E1 的 R2G 总获取时长保持 33.52 小时。Full R2G 完整库评估：8/13（61.54%），DRC 7/7、setup 1/6，11 次修复调用，评估期无 LLM 调用。完整 Recipe 库使用过 B 题开发证据，正文方法中已说明其知识来源，原 M0–M3 和 LLM 数值保持不变。正文图 3、实验三段落和附录 B 已纳入本结果。

下游效用实验使用 278 个设计的固定 175/49/54 划分和三个随机种子。正文仅采用最直接支持 R2G 数据贡献的阶段预测、几何消融及选择性拓扑收益，不采用非单调的数据规模曲线作为主要论据。机器可读汇总位于 `evidence/gnn_*.json`；正文图 `downstream_utility.pdf` 由 `scripts/build_downstream_assets.py` 从这些冻结汇总生成。

## 编译与重绘

标准 TeX Live / Overleaf：`pdflatex main.tex` → `bibtex main` → 两次 `pdflatex main.tex`。

本机：

```bash
XDG_CACHE_HOME=/tmp/r2g-tex-cache /tmp/r2g-tectonic main.tex --keep-logs --keep-intermediates
python3 scripts/validate_paper.py
python3 scripts/package_overleaf.py
```

图表共用 `scripts/plot_style.py`，重绘脚本为 `build_workflow.py`、`build_e1_e3_assets.py`、`build_e4_assets.py`。可用 `/home/yangao/.conda/envs/gnn_env/bin/python` 运行。Full R2G 证据重算与逐题表生成使用 `scripts/build_full_r2g_assets.py`。数据脚本读取已冻结实验记录；正常编译无需重绘或访问原始审计路径。

`evidence/build_validation.json` 记录当前 PDF 的页数、主文末页、模板、字体、引用和溢出检查。`evidence/e4_revision_manifest.json` 记录当前源文件及图表哈希；`figure_data.json` 保存未改变的图表数据，`visual_style.json` 记录视觉规范。

## 文件整理

旧 DATE 工程、历次 `revisions/*.zip` 和未被正文使用的旧图已删除。保留一份完整论文 PDF 和一份 Overleaf ZIP；`figures/*.pdf` 是当前论文编译所需的五张矢量图，并非重复论文。源码、实验审计与原始实验数据仍保留。删除清单见 `evidence/cleanup_manifest.json`。

原始写作思路、[后续实验计划](research_notes/next_experiments_zh.md)及空白实验记录模板迁至 `research_notes/`，不编入论文或 Overleaf 包。AI 使用披露位于 `sections/05_ai_use_statement.tex`，提交前需由作者核对。

## Full R2G 评估证据

`evidence/e3_full_r2g/` 保存用户提供的材料副本；`e3_full_r2g_derived.json` 记录独立重算和来源哈希。已核对材料清单 60 个文件、13 份逐题记录、11 份 trial 证据。11 次均为实测调用，未将后续源码改写、其他设计或事后理想筛选混入本轮成绩。2 题无合法动作仍计入 13 题分母；完整库补充未新测 clean sentinel。

正文与图表采用研究主题名称，不展示 E1/E3/E4 等内部实验编号。实验归档及脚本中的编号保留用于来源追溯。Full R2G 与其他方法等间距展示，不设虚线分隔；图中仅显示 Full R2G；完整库使用评测题开发证据的事实集中在正文方法中说明。

流程图采用无图标的直角方框与文字箭头：上方展示 Recipe 开发和复用，下方展示数据处理、验收条件与输出；不使用卡片装饰。正文不采用“后来补充实验”的组织方式，Full R2G 与其他修复方法共同呈现。原始证据中的评估性质字段保持不变，便于审计。

图例和表头中的 Feature 指已审计的数值输入特征，Label 指监督标签；原始审计的 `numeric` 字段及统计口径保持不变。遵循所用 ICLR 模板，五张图的图注均位于图下，五张表的表题均位于表上；图内 a/b 为分面标签。

原有获取、修复和转换结果图已加入 API 美元估算标注，无新增成本表。采用 2026-09-17 核对的官方公开基础上下文、未缓存费率；Qwen 使用新加坡国际价格，DeepSeek 使用高峰价格。此为统一参考价格换算，不是中转站实付账单；不包含缓存折扣、长上下文溢价、EDA 计算及人工成本。附录 C 列出费率、来源链接和阶段范围。修复图标部署费用，另说明共享学习阶段约 $0.57；转换图标模型开发费用，R2G 历史工具开发费用未计量，显示 n/a。

`evidence/api_costs.json` 保存 Token 分项、官方价格来源、计算结果和 93 份原始账本/响应文件的哈希。更新时先运行 `python3 scripts/build_api_costs.py`，再重绘结果图和编译；获取阶段的独立 reasoning 计入输出价，其他两项的 completion 已含 reasoning，不重复计算。此文件独立于冻结的 `figure_data.json`，原成绩保持不变。
