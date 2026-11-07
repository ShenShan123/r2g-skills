// English for the UI, keyed by the Chinese source text. Two parts:
//  EN       -- exact strings: UI literals (some with {placeholders}) and fixed
//              backend strings (stage names, verdicts, captions, errors).
//  PATTERNS -- backend strings with numbers or names in them. `tr` translates a
//              captured piece (it is i18n.en, passed in to avoid an import cycle).
// A string found in neither falls through unchanged. `{n:design}` = "1 design"/"2 designs".

export const EN: Record<string, string> = {
  // separators and punctuation
  "：": ": ", "；": "; ", "、": ", ",

  // ---- layout / navigation
  "r2g · RTL 到图数据集": "r2g · RTL to graph datasets",
  "开始": "Start", "任务": "Jobs", "对话": "Chat", "设置": "Settings",
  "RTL → 版图 → 数据集": "RTL → Layout → Dataset",
  "模型未配置": "Model not configured", "浅色": "Light", "深色": "Dark", "切换主题": "Toggle theme",

  // ---- shared status / steps
  "通过": "Passed", "完成": "Done", "失败": "Failed", "进行中": "Running", "正在停止": "Stopping",
  "打包中": "Packaging", "排队中": "Queued", "已取消": "Cancelled", "运行中": "Running", "全部": "All",
  "综合": "Synthesis", "布局布线": "Place & Route", "签核": "Signoff", "修复": "Repair", "数据集": "Dataset",
  "校验": "Verify", "整体流程进度": "Overall progress", "查看{step}阶段图": "View {step} stage layout",
  "（无需）": " (n/a)", "加载中…": "Loading…", "取消": "Cancel", "下载": "Download", "详情": "Details",
  "图数据集": "Graph dataset", "图像数据集": "Image dataset",

  // ---- output picker
  "给 GNN 用：电路转成图，节点和边上带拥塞、线长、时序、IR drop 等标签":
    "For GNNs: the circuit as a graph, with congestion, wirelength, timing and IR-drop labels on nodes and edges",
  "给 CNN 用：版图切成网格特征图，标签是布线后的拥塞和 IR drop 图":
    "For CNNs: the layout as gridded feature maps, labeled with post-route congestion and IR-drop maps",

  // ---- flow parameters
  "参数加载失败，请刷新后重试。": "Failed to load parameters. Refresh and try again.",
  "{range} 个单元 {ns} ns": "{range} cells: {ns} ns",
  "r2g 经验参数（推荐）": "r2g defaults (recommended)", "自定义": "Custom",
  "按自己的需要改时钟、利用率、die 等，没改的项仍用经验值":
    "Set your own clock, utilization, die, etc.; anything left unchanged keeps the default",
  "时钟：按综合后单元数分档 —— {tiers}": "Clock: tiered by post-synthesis cell count — {tiers}",
  "利用率 {u}% · die 按面积、引脚数和电源网格下限（core ≥ {floor} µm）自动算 · 面积余量 ×{a}":
    "Utilization {u}% · die sized from area, pin count and the PDN floor (core ≥ {floor} µm) · area allowance ×{a}",
  "时序约束：建立不确定度 {s}% 周期，保持 {h} ps，输入输出延迟 {io}% 周期":
    "Timing constraints: setup uncertainty {s}% of period, hold {h} ps, I/O delay {io}% of period",
  "时钟周期": "Clock period", "按单元数分档": "Tiered by cell count", "所有设计用同一个": "Same for all designs",
  "≥ {n} 个单元": "≥ {n} cells", "< {n} 个单元": "< {n} cells", " （{unit}）": " ({unit})",
  "已改": "Changed", "自动 / 不设": "Auto / unset", "经验值 {v}": "Default {v}",
  "{help}（范围 {min}–{max}）": "{help} (range {min}–{max})", "全部恢复为经验值": "Reset all to defaults",
  // profile.FIELDS (backend /api/params)
  "% 周期": "% of period",
  "留空 = 按综合后单元数分档（经验值）；填一个数 = 所有设计都用这个周期":
    "Empty = tiered by post-synthesis cell count (default); a number = this period for every design",
  "利用率": "Utilization",
  "单元面积占 core 面积的比例。经验值 0.50：die 尺寸固定后，高于它的拥塞无法再修":
    "Cell area as a fraction of core area. Default 0.50: once the die is fixed, congestion above it cannot be repaired",
  "面积余量": "Area allowance",
  "综合面积没算进去的单元（tap、时钟缓冲、时序修复缓冲）的放大系数，实测值":
    "Measured scale factor for cells the synthesis area omits (taps, clock buffers, timing-repair buffers)",
  "core 最小边长": "Minimum core side",
  "电源网格能建起来的最小 core；小于它会 PDN-0185": "Smallest core the power grid can be built on; below it PDN-0185 fires",
  "固定 die 边长": "Fixed die side",
  "留空 = 按面积、引脚数和电源网格下限自动算；填一个数 = 所有设计都用这个 die":
    "Empty = sized from area, pin count and the PDN floor; a number = this die for every design",
  "输入输出延迟": "I/O delay",
  "端口路径也纳入时序检查；0 则只约束寄存器之间": "Puts port paths under timing checks too; 0 constrains register-to-register paths only",
  "建立时间不确定度": "Setup uncertainty",
  "只加在 setup 上（加到 hold 会插入大量缓冲）": "Applied to setup only (on hold it would insert many buffers)",
  "保持时间不确定度": "Hold uncertainty", "固定值，不随周期变化": "Fixed value, independent of the period",
  "放置密度加量": "Placement density add-on",
  "留空 = 不设（经验基线没设）；设了则不能低于 0.10": "Empty = unset (the baseline leaves it unset); if set, at least 0.10",
  "时钟分档的最后一档必须不设上限，其余各档必须有上限": "The last clock tier must have no upper bound; every other tier needs one",
  "时钟分档的上限要递增，周期要大于 0": "Clock tier bounds must increase and periods must be above 0",

  // ---- delete dialog
  "删除失败，请重试": "Delete failed; please retry", "状态查询失败，正在重试": "Status check failed; retrying",
  "删除任务 {title}": "Delete job {title}", "删除对话 {title}": "Delete chat {title}",
  "正在停止计算并删除生成数据": "Stopping computation and deleting generated data",
  "删除任务及生成数据": "Delete job and generated data",
  "删除对话及其创建的任务及生成数据": "Delete chat, the jobs it created and their generated data",
  "确认删除任务？": "Delete this job?", "确认删除对话？": "Delete this chat?", "未命名": "Untitled",
  "该对话及其创建的任务将被删除，任务生成的所有数据集、版图、报告和中间文件也会一并永久删除。":
    "This chat and the jobs it created will be deleted, together with all their datasets, layouts, reports and intermediate files, permanently.",
  "该任务生成的所有数据集、版图、报告和中间文件都会一并永久删除。":
    "All datasets, layouts, reports and intermediate files of this job will be permanently deleted.",
  "运行中的计算会先停止。原始输入和其他任务的数据会保留。删除后无法恢复。":
    "Running computation is stopped first. Original inputs and other jobs' data are kept. This cannot be undone.",
  "确认删除": "Delete",

  // ---- stage views
  "最终版图": "Final layout", "修复后": "After repair", "修复后的最终版图": "Final layout (repaired)",
  "打开原图": "Open full image", "原图": "Full image", "关闭版图大图": "Close enlarged layout",
  "版图暂时无法加载，请关闭后重试。": "The layout cannot be loaded right now; close and try again.",
  "{name} {label}放大图": "{name} {label}, enlarged", "上一张版图": "Previous layout", "上一张（←）": "Previous (←)",
  "下一张版图": "Next layout", "下一张（→）": "Next (→)",
  "大设计预览已抽样，完整数据保留在设计产物中。": "Large-design preview is sampled; full data stays in the design outputs.",
  "产物来源": "Source artifact", "阶段版图暂时无法加载，请收起后重试": "Stage layouts cannot be loaded right now; collapse and try again",
  "等待该阶段产物": "Waiting for this stage's output", "{name} 阶段版图": "{name} stage layouts",
  "收起各阶段版图演化": "Hide layout evolution by stage", "显示各阶段版图演化": "Show layout evolution by stage",
  "最终版本各阶段版图演化": "Final version: layout evolution by stage",
  "最终版本 · 点击缩略图放大": "Final version · click a thumbnail to enlarge",
  "版图演化": "Layout evolution", "{n} 种单元": "{n} cell types", "{n} 条电源条": "{n} power straps",
  "{n} 个单元": "{n} cells", "{n} 个时钟缓冲": "{n} clock buffers", "{n} 段布线": "{n} segments",
  "{n} 个天线二极管": "{n} diodes", "电源网络": "Power grid",
  "天线二极管": "Antenna diodes", "布线层": "Routing layers",
  "已修复": "Repaired", "签核干净": "Signoff clean", "已减少": "Reduced", "已清零": "Cleared", "无需修复": "Already clean", "未成功": "Not fixed", "第 {n} 轮": "Round {n}", "· 左右滑动查看": "· swipe to browse",
  "正在读取阶段版图…": "Loading stage layouts…", "可横向滚动的版图演化": "Horizontally scrollable layout evolution",
  "放大最终版图": "Enlarge final layout", "放大{label}版图": "Enlarge {label} layout", "{label}缩略图": "{label} thumbnail",
  "待生成": "Pending", "未确认": "Unverified", "未保留": "Not kept",
  // stage_views.py captions
  "综合单元组成（无物理坐标）": "Synthesized cell mix (no physical coordinates)",
  "最终布线": "Final route", "详细布线": "Detailed route", "时钟树": "Clock tree", "放置后": "Post-placement",
  "初始布局": "Initial floorplan", "物理视图": "physical view", "阶段完成快照": "stage snapshot",
  "最终版图未保留，无法还原": "Final layout not kept; cannot be restored",
  "等待最终布线产物": "Waiting for the final route output", "DRC / LVS 双清": "DRC / LVS clean",
  "签核未通过": "Signoff failed", "等待签核结论": "Awaiting signoff verdict",
  "任务中断，未确认最终签核": "Job interrupted; final signoff unconfirmed", "签核结论未记录": "Signoff verdict not recorded",
  "最终版本的该阶段快照未保留，无法还原": "This stage's snapshot of the final version was not kept; cannot be restored",
  "等待本次运行的阶段产物": "Waiting for this run's stage output",
  "该阶段沿用旧产物，但运行血缘未核验，无法确认属于最终版本":
    "This stage reuses an older output whose run lineage is unverified; it cannot be confirmed as the final version",
  "已保留的历史 Placement 快照": "kept historical placement snapshot", "已保留的 Placement 快照": "kept placement snapshot",
  "最终布线版图（历史 Route 快照未单独保留）": "Final routed layout (no separate historical route snapshot)",
  "历史中间文件已清理，无法还原此阶段": "Historical intermediate files were cleaned up; this stage cannot be restored",
  "最终布线版图": "Final routed layout", "最终布线版图（包含已完成的修复）": "Final routed layout (includes completed repairs)",
  "与修复前 Route 对比": "compared with the pre-repair route",
  "综合报告中没有可识别的单元统计": "No recognizable cell statistics in the synthesis report",
  "DEF 未保存矩形 die 边界": "DEF has no rectangular die boundary", "DEF die 边界无效": "Invalid DEF die boundary",
  "需要 OpenROAD 读取已有 ODB 快照": "OpenROAD is needed to read the existing ODB snapshot",
  "现有 ODB 读取失败，未重跑设计": "Reading the existing ODB failed; the design was not rerun",
  "导出的 DEF 过大，暂不生成轻量预览": "Exported DEF too large; no lightweight preview",
  "该阶段文件过大，暂不生成轻量预览": "Stage file too large; no lightweight preview",
  "阶段产物仍在写入，稍后再生成预览": "Stage output still being written; preview later",
  "阶段预览拒绝读取其他任务的 ORFS 目录": "Stage preview refuses to read another job's ORFS directory",
  "阶段预览的运行目录越界": "Stage preview run directory is out of bounds", "该阶段预览尚未保存": "This stage's preview is not saved yet",

  // ---- chat
  "查看内置示例": "List built-in examples", "提交任务": "Submit job", "查询任务进度": "Check job progress",
  "列出任务": "List jobs", "查看设计详情": "View design details", "读取日志": "Read log",
  "请修复引擎诊断": "Ask the repair engine to diagnose", "查询故障知识库": "Query failure knowledge base",
  "取消任务": "Cancel job", "查询数据集": "Query dataset", "检查工具链": "Check toolchain", "读取源码": "Read source",
  "在 GitHub 上搜索 RTL": "Search GitHub for RTL", "下载仓库并列出设计": "Fetch repo and list designs",
  "查看磁盘占用": "Check disk usage",
  "帮我在 GitHub 上找一个小的 UART 设计，跑一遍生成图数据集": "Find a small UART design on GitHub and build a graph dataset from it",
  "最近的任务进展怎么样？": "How are my recent jobs doing?",
  "跑一下三个内置示例，生成图数据集": "Run the three built-in examples and build graph datasets",
  "PDN-0185 是什么问题，怎么解决？": "What is PDN-0185 and how do I fix it?",
  "这个文件夹里没有 .v / .sv 文件": "This folder has no .v / .sv files",
  "新对话": "New chat", "历史": "History", "有什么可以帮你？": "How can I help?",
  "我可以帮你找设计、提交任务、看进度、解释失败原因。": "I can find designs, submit jobs, track progress and explain failures. ",
  "通过与否由签核工具判定，不是我说了算。": "Pass or fail is decided by the signoff tools, not by me.",
  "正在思考…": "Thinking…", "请到 ": "Check the model and API key in ", " 检查模型与密钥。": ".",
  "上传中…": "Uploading…", " · {n} 个文件": " · {n:file}", "移除附件": "Remove attachment",
  "上传一个 RTL 文件夹，agent 可以直接拿它提交任务": "Upload an RTL folder; the agent can submit a job with it directly",
  "上传 RTL": "Upload RTL",
  "说说要对这个设计做什么，例如：跑一遍生成图数据集": "Say what to do with this design, e.g. build a graph dataset",
  "输入问题，Enter 发送": "Ask a question; Enter to send", "发送": "Send",
  "思考过程（{n} 字）": "Reasoning ({n} chars)", "（出错）": " (error)", "打开任务": "Open job",
  "参数 ": "Arguments ", "结果 ": "Result ",

  // ---- home
  "把 RTL 变成签核过的数据集": "Turn RTL into signed-off datasets",
  "综合、布局布线、DRC/LVS 签核、失败自动修复、生成数据集并逐项校验，全部自动完成。":
    "Synthesis, place and route, DRC/LVS signoff, automatic repair of failures, dataset generation and check-by-check verification, all automatic.",
  "选择设计": "Choose designs", "一个设计": "One design", "批量（很多个设计）": "Batch (many designs)",
  "已选择文件夹": "Folder selected", "{n} 个 Verilog 文件 · 点击可重新选择": "{n:Verilog file} · click to choose again",
  "上传 RTL 文件夹": "Upload an RTL folder",
  "包含 .v / .sv 的文件夹；只会上传源码和 $readmem 数据文件": "A folder with .v / .sv files; only sources and $readmem data files are uploaded",
  "清除": "Clear", "顶层模块名（可不填，自动识别）": "Top module name (optional, auto-detected)",
  "没有现成的 RTL？先用示例试试：": "No RTL at hand? Try an example:",
  "选择工艺平台": "Choose a process platform", "工艺平台": "Process platform",
  "选择数据集类型": "Choose a dataset type", "流程参数": "Flow parameters", "提交中…": "Submitting…",
  "开始处理 {n} 个设计": "Process {n:design}", "（自定义参数）": " (custom parameters)",
  "先在第 1 步选择设计": "Choose designs in step 1 first",
  "设计会按「设置」里的并发上限排队运行；服务重启后会自动接着跑。":
    "Designs queue under the concurrency limit in Settings; after a service restart they resume automatically.",
  "小设计约 3–5 分钟；提交后会打开进度页，可以随时离开再回来看。":
    "A small design takes about 3–5 minutes. The progress page opens after you submit; you can leave and come back any time.",
  "不确定怎么做？问 agent": "Not sure what to do? Ask the agent",
  "让它帮你在 GitHub 上找设计、提交任务、解释失败原因": "It can find designs on GitHub, submit jobs and explain failures",
  "最近的任务": "Recent jobs", "{n}/{total} 通过": "{n}/{total} passed",
  "这个文件夹里没有 Verilog 文件": "This folder has no Verilog files",
  "准备一个文件夹，里面": "Prepare a folder in which ", "每个子文件夹是一个设计": "each subfolder is one design",
  "。可以上传，也可以直接填服务器上的路径（文件已经在服务器上时更快）。":
    ". Upload it, or enter a path on the server (faster when the files are already there).",
  "上传装着多个设计的文件夹": "Upload a folder of designs", "只会上传源码和 $readmem 数据文件": "Only sources and $readmem data files are uploaded",
  "或填服务器上的目录，例如 /data/rtl_corpus": "Or a directory on the server, e.g. /data/rtl_corpus",
  "扫描": "Scan", "识别到 {n} 个设计：": "Found {n:design}: ", "{n} 个将运行": "{n} will run",
  "，{n} 个将跳过": ", {n} will be skipped", "· 顶层 {top}": "· top {top}",
  "收起跳过的设计和原因": "Hide skipped designs and reasons", "查看跳过的设计和原因": "Show skipped designs and reasons",

  // ---- jobs list
  "新建任务": "New job", "还没有任务。": "No jobs yet. ", "去新建一个": "Create one",
  "{n} 双清 · ": "{n} DRC/LVS clean · ", "{n} 数据集通过": "{n} passed dataset checks", " · {n} 失败": " · {n} failed",
  " · {n} 运行": " · {n} running", " · {n} 排队": " · {n} queued", " · {n} 已取消": " · {n} cancelled", " · 共 {n}": " · {n} total",

  // ---- job detail
  "时钟": "Clock", "时钟分档": "Clock tiers", "core 下限": "Core floor", "IO 延迟%": "I/O delay %",
  "建立不确定度%": "Setup uncertainty %", "保持不确定度": "Hold uncertainty", "密度加量": "Density add-on",
  "准备": "Preparing", "定尺寸": "Sizing", "自动修复": "Auto-repair", "生成数据集": "Building dataset",
  "数据集校验": "Dataset verification", "版图预览": "Layout preview", "写入知识库": "Writing to knowledge store",
  "全部任务": "All jobs", "自定义参数：": "Custom parameters: ", "r2g 经验参数": "r2g defaults",
  "正在停止所有未完成设计": "Stopping all unfinished designs", "正在处理": "Processing", "排队等待计算资源": "Waiting for compute",
  " · 已结束 {n}/{total}": " · {n}/{total} finished",
  "{r} 运行 · {p} 排队 · {c} 已取消 · 已用 {m} 分钟": "{r} running · {p} queued · {c} cancelled · {m} min elapsed",
  "任务已停止：{n} 个设计已取消": "Job stopped: {n:design} cancelled",
  "完成：{n}/{total} 个设计通过": "Done: {n}/{total} designs passed",
  "没有设计通过（{n}/{total} 失败）": "No design passed ({n}/{total} failed)",
  "已完成的结果和报告保留。": "Finished results and reports are kept.",
  "通过的设计都已签核（DRC、LVS 干净）并通过数据集校验，已打包。":
    "Every passed design is signed off (DRC and LVS clean), passed dataset verification and is packaged.",
  "点开失败的设计查看原因，或让 agent 帮你分析。": "Open a failed design to see why, or ask the agent to analyze it.",
  " {n} 个失败的设计不在数据集里。": " Failed designs ({n}) are not in the dataset.",
  " 用时 {m} 分钟。": " Took {m} min.",
  "取消所有排队设计，并停止运行中的设计；已完成的结果保留": "Cancel all queued designs and stop running ones; finished results are kept",
  "停止请求失败，请重试": "Stop request failed; please retry", "提交停止请求…": "Requesting stop…",
  "正在停止…": "Stopping…", "停止整个任务": "Stop the whole job", "下载数据集": "Download dataset",
  "停止失败：": "Stop failed: ", "DRC + LVS 双清：": "DRC + LVS clean: ",
  " · 数据集通过：{p} · 失败：{f}": " · dataset passed: {p} · failed: {f}",
  "双清与数据集校验分别统计。": "DRC/LVS-clean and dataset verification are counted separately.",
  "详细过程（最近 {n} 条）": "Detailed log (last {n} events)", "任务|event": "Job",
  "把目前已通过的设计先打包下载": "Package and download the designs that have passed so far",
  "下载已完成的部分": "Download finished part", "数据集通过": "Dataset passed",
  "失败原因（点一下只看这一类）": "Failure reasons (click to show only that kind)",
  "按设计名搜索": "Search by design name", "{n} 个": "{n:design}", "没有符合条件的设计": "No matching designs",
  "上一页": "Previous", "下一页": "Next",
  "任务 {job} 有 {n} 个设计失败，帮我按原因归类，告诉我哪些是 RTL 问题、哪些可以修":
    "Job {job} has {n} failed designs. Group them by cause and tell me which are RTL problems and which can be fixed",
  "让 agent 归类分析失败的设计": "Ask the agent to group the failed designs",
  "排队中，等待空闲的算力": "Queued, waiting for free compute",
  "完成：签核通过，数据集校验 {n}/{total} 项全部通过": "Done: signoff passed, all {n}/{total} dataset checks passed",
  "正在{stage}…": "{stage}…",
  "{cells} 个单元 · die {die} µm{b} · 时钟 {p} ns{ps}": "{cells} cells · die {die} µm{b} · clock {p} ns{ps}",
  "（自定义）": " (custom)", "（按引脚数修正）": " (pin-count corrected)",
  "干净": "clean", "{n} 处违例": "{n} violations", "一致": "match",
  "签核未通过后自动诊断并修复（{n} 步，见详情）": "Diagnosed and repaired automatically after signoff failed ({n:step}, see details)",
  "任务 {job} 里的设计 {name} 失败了，帮我分析原因并告诉我怎么改":
    "Design {name} in job {job} failed. Analyze why and tell me how to fix it",
  "让 agent 分析原因": "Ask the agent why",
  "概览": "Overview", "图像": "Images", "版图": "Layout", "日志": "Log",
  "尺寸与约束": "Size and constraints", "综合后单元": "Cells after synthesis", "面积": "Area", "IO 引脚": "I/O pins",
  "die 边长": "Die side", "由面积决定": "Set by area", "由引脚数决定": "Set by pin count",
  "由电源网格下限决定": "Set by the PDN floor", "建立时间裕量": "Setup slack", "PPA（来自版图）": "PPA (from layout)",
  "单元面积": "Cell area", "功耗": "Power", "签核（由工具判定）": "Signoff (decided by the tools)",
  "{n} 处不匹配": "{n} mismatches", "自动修复记录": "Auto-repair log", "失败于：": "Failed at: ", "（暂无日志）": "(no log yet)",
  "单元密度": "Cell density", "宏单元区域": "Macro region", "RUDY（布线需求估计）": "RUDY (routing demand estimate)",
  "引脚 RUDY": "Pin RUDY", "引脚密度": "Pin density", "拥塞（平滑）": "Congestion (smoothed)", "拥塞（原始）": "Congestion (raw)",
  "图像数据集在设计完成后生成": "The image dataset is built after the design finishes",
  "最大 {v} {unit}": "max {v} {unit}", "网格": "Grid", "特征来源": "Feature source", "CTS 后": "Post-CTS",
  "特征（输入）": "Features (input)", "只取自放置后版图，无布线信息": "From the placed layout only, no routing information",
  "标签（预测目标）": "Labels (prediction targets)", "取自布线后结果": "From the routed result",
  "颜色仅用于预览（深紫→黄，按 99 分位缩放）；数值在下载包的 features.npz / labels.npz 中，数组为 [x][y]。":
    "Colors are for preview only (dark purple → yellow, scaled to the 99th percentile); values are in features.npz / labels.npz in the download, indexed [x][y].",

  // ---- settings
  "已保存": "Saved", "（{s} 秒）": " ({s} s)", "对话用的模型": "Model for chat",
  "只有「对话」需要模型；提交任务、签核和生成数据集不需要。密钥只保存在本机。":
    "Only Chat needs a model; submitting jobs, signoff and dataset generation do not. The key is stored on this machine only.",
  "服务商": "Provider", "模型名": "Model", "不填则用 {m}": "Defaults to {m}", "必填": "Required",
  "例如 my-model": "e.g. my-model", "API 密钥": "API key", "✓ 已配置，不改就留空": "✓ Configured; leave empty to keep",
  "未配置": "Not configured", "••••••••（已保存）": "•••••••• (saved)", "粘贴你的 API 密钥": "Paste your API key",
  "深度思考": "Deep thinking",
  "开启后分析失败原因更准，每次回答多等几秒。思考过程会折叠显示在对话里。":
    "Diagnoses failures more accurately at the cost of a few seconds per answer. The reasoning is shown collapsed in the chat.",
  "高级选项": "Advanced", "接口地址": "Base URL", "一般不用改": "Usually unchanged",
  "从环境变量读取密钥": "Read the key from an environment variable",
  "填了就优先用它，而不是上面保存的密钥": "If set, takes precedence over the saved key above",
  "保存": "Save", "保存并测试": "Save and test", "算力": "Compute",
  "网页、命令行和 MCP 共用这个上限，超出的设计会排队。": "The web UI, CLI and MCP share this limit; designs beyond it wait in the queue.",
  "同时运行的设计数": "Concurrent designs", "每个设计的 CPU 核数": "CPU cores per design",
  "本机 ": "This machine has ", " 个线程，建议两者相乘不超过这个数，例如同时 ": " threads; keep the product of the two at or below that, e.g. ",
  " 个 × 每个 8 核。": " designs × 8 cores each. ",
  "建议两者相乘约等于机器的线程数。": "Keep the product of the two close to the machine's thread count. ",
  "设得太大机器看起来很忙，实际反而更慢；如果机器还有别人在用，留出余量。":
    "Set too high, the machine looks busy but runs slower; leave headroom if others share the machine.",
  "磁盘": "Disk",
  "当前剩余 {n} GB。剩余空间低于下限时暂停开始新设计，清理出空间后自动继续。":
    "{n} GB free. Below the floor, new designs pause and resume automatically once space is freed.",
  "剩余空间下限（GB）": "Free-space floor (GB)", "保留通过设计的中间版图文件": "Keep intermediate layout files of passed designs",
  "关闭时（推荐），设计通过后删除各阶段的 ODB/DEF/GDS 副本，只留数据集、报告和日志，大批量时能省下大部分空间。":
    "When off (recommended), per-stage ODB/DEF/GDS copies are deleted once a design passes, keeping only the dataset, reports and logs; this saves most of the space in large batches.",
  "保留中间文件": "Keep intermediate files", "修复经验库": "Repair knowledge base",
  "每跑完一个设计都会记下结果，下次遇到类似问题修得更准。只保存在本机，不会上传。":
    "Every finished design is recorded so similar problems are repaired better next time. Stored on this machine only, never uploaded.",
  "已记录 ": "", " 次运行": " runs recorded",
  // config.py provider labels
  "通义千问": "Qwen (Tongyi)", "智谱 GLM": "Zhipu GLM", "豆包（填写推理接入点 ID）": "Doubao (enter the inference endpoint ID)",
  "本地 Ollama": "Local Ollama", "自定义（OpenAI 兼容）": "Custom (OpenAI-compatible)",
  "连接正常，支持工具调用": "Connected; tool calling works",
  "能连通，但模型没有调用工具——该模型可能不支持函数调用": "Connected, but the model did not call a tool — it may not support function calling",

  // ---- backend: pipeline stage labels and fixed event messages
  "准备工程": "Preparing project", "定尺寸与时钟": "Sizing die and clock",
  "按最终时钟综合并布局布线": "Synthesis and place and route at the final clock", "签核 DRC/LVS": "Signoff (DRC/LVS)",
  "布局布线完成": "Place and route complete",
  "自动修复：诊断 → 修改 → 重跑 → 重新签核": "Auto-repair: diagnose → modify → rerun → re-signoff",
  "修复成功，DRC/LVS 均已清零": "Repair succeeded: DRC and LVS clean",
  "完成：DRC/LVS 签核通过（仅签核，不生成数据集）": "Done: DRC/LVS signoff passed (signoff only, no dataset)",
  "完成：签核通过，图像数据集已校验": "Done: signoff passed, image dataset verified",
  "完成：签核通过，数据集已校验": "Done: signoff passed, dataset verified",
  "生成图像数据集（特征取自放置后版图，标签取自布线后）": "Building image dataset (features from the placed layout, labels from the routed result)",
  "布线失败：按已验证策略调整面积，重跑并重新签核": "Routing failed: resizing by a validated recipe, rerunning and re-signing off",
  "布线恢复完成，等待 DRC/LVS 签核": "Routing recovered; awaiting DRC/LVS signoff",
  "停止尚未确认，保留执行记录供核对": "Stop not yet confirmed; execution records kept for review",
  "记录不完整，无法恢复（可重新提交）": "Incomplete record, cannot resume (resubmit to retry)",
  "服务重启后从头重新运行": "Restarting from scratch after a service restart", "服务重启后继续排队": "Back in the queue after a service restart",
  "无法确认子进程已停止，未标记为已取消": "Could not confirm the child process stopped; not marked cancelled",
  "检测到源码解析失败，尝试 Slang 前端": "Source parse failure detected; trying the Slang frontend",
  "无运行记录": "no run record", "见日志": "see log",
  "已请求停止：排队设计已取消，运行中的设计正在退出": "Stop requested: queued designs cancelled, running designs are exiting",
  "工作目录已清理（数据集包保留）": "Work directory cleaned (dataset package kept)",
  "电源网格下限": "PDN floor", "自定义固定值": "custom fixed value", "按引脚数修正": "pin-count corrected",
  "自动识别（唯一未被例化的模块）": "auto-detected: the only uninstantiated module", "用户指定": "user-specified",
  "design.json 指定": "from design.json", "指定": "specified",
  "被其他文件 `include 的头文件，不再单独编译": "Header `included by other files; not compiled on its own",
  "没有模块声明的代码片段（只能粘贴进模块里用），不单独编译": "Code fragment without a module declaration (only usable inside a module); not compiled on its own",
  "上传的文件里有多个同名数据文件": "several uploaded data files share this name",
  "上传的文件里没有这个数据文件": "this data file is not among the uploaded files",
  "按相对路径找到": "found by relative path", "按唯一文件名找到": "found by unique file name",
  "去掉 FPGA 厂商属性 altera_attribute": "removed the FPGA vendor attribute altera_attribute",
  "用 Slang 前端转换 SystemVerilog，再交给原 ORFS 综合": "converted SystemVerilog with the Slang frontend, then synthesized with stock ORFS",
  "内部信号与端口名仅大小写不同（LVS 网表不分大小写），给内部信号改名":
    "an internal signal differs from a port name only in case (LVS netlists are case-insensitive); renamed the internal signal",
  "增量全局布线树重建失败；跳过该可选优化，重新布线并正常签核":
    "Incremental global-route tree rebuild failed; skipped this optional optimization, rerouted and signed off normally",
  "SPEF 负下标名称编码不兼容；仅规范转义，保留全部连接和RC值":
    "Incompatible SPEF encoding of negative-index names; normalized escaping only, all connections and RC values kept",
  // server.py / package.py / deletion.py errors
  "对话不存在或已删除": "Chat not found or deleted", "对话正在删除或已删除，请新建对话": "This chat is being deleted or was deleted; start a new chat",
  "请先确认删除及生成数据清理": "Confirm the deletion and cleanup of generated data first",
  "任务或对话不存在": "Job or chat not found", "尚未请求删除": "Deletion not requested",
  "任务正在删除或已删除": "This job is being deleted or was deleted",
};

type Tr = (s: string) => string;
const pl = (n: string, noun: string) => `${n} ${noun}${n === "1" ? "" : "s"}`;
const lower = (s: string) => (/^[A-Z][a-z]/.test(s) ? s[0].toLowerCase() + s.slice(1) : s);   // keeps "PDN floor"
const semi = (s: string) => s.replace(/；/g, "; ").replace(/……$/, "…");

export const PATTERNS: [RegExp, (m: RegExpMatchArray, tr: Tr) => string][] = [
  // job titles
  [/^(\d+) 个设计 · (\S+) · (\S+)$/, (m) => `${pl(m[1], "design")} · ${m[2]} · ${m[3]}`],
  [/^批量 · (.+) · (\d+) 个设计$/, (m) => `Batch · ${m[1]} · ${pl(m[2], "design")}`],
  // job lifecycle (jobs.py / pipeline.py)
  [/^任务已创建：(\d+) 个设计$/, (m) => `Job created: ${pl(m[1], "design")}`],
  [/^任务结束：(\d+)\/(\d+) 个设计通过并产出数据集$/, (m) => `Job finished: ${m[1]}/${m[2]} designs passed and produced a dataset`],
  [/^运行中被中断了 (\d+) 次，不再重试（可单独重新提交）$/, (m) => `Interrupted ${m[1]} times while running; not retrying (resubmit it on its own)`],
  [/^服务重启后恢复了 (\d+) 个未完成的设计$/, (m) => `Resumed ${pl(m[1], "unfinished design")} after a service restart`],
  [/^排队中：本机已有 (\d+) 个设计在跑（上限 (\d+)，网页、命令行、MCP 共用）$/,
    (m) => `Queued: ${pl(m[1], "design")} already running here (limit ${m[2]}, shared by web, CLI and MCP)`],
  [/^磁盘只剩 (\S+) GB（下限 (\S+) GB），暂停开始新设计；清理出空间后自动继续$/,
    (m) => `Only ${m[1]} GB of disk left (floor ${m[2]} GB); new designs paused until space is freed`],
  [/^已删除中间版图文件 (\S+) MB（数据集与报告保留）$/, (m) => `Deleted ${m[1]} MB of intermediate layout files (dataset and reports kept)`],
  [/^已清理 ORFS 目录中的重复中间文件 (\S+) MB$/, (m) => `Cleaned ${m[1]} MB of duplicate intermediate files from the ORFS directory`],
  [/^阶段预览暂不可用：([\s\S]*)$/, (m, tr) => `Stage preview unavailable: ${tr(m[1])}`],
  [/^内部错误：([\s\S]*)$/, (m, tr) => `Internal error: ${tr(m[1])}`],
  // prepare / synth / size
  [/^使用自定义参数：(.*)$/, (m, tr) => `Using custom parameters: ${m[1].split("、").map(tr).join(", ")}`],
  [/^顶层模块 ([^\s（]+)（(.*)），(\d+) 个源文件$/, (m, tr) => `Top module ${m[1]} (${tr(m[2])}), ${pl(m[3], "source file")}`],
  [/^按文件夹名在候选 (.*) 中选定$/, (m) => `chosen by folder name among ${m[1]}`],
  [/^前端修复：(.*)，重新综合$/, (m, tr) => `Frontend fix: ${tr(m[1])}; re-synthesizing`],
  [/^前端修复：(.*)（([^（）]*)）$/, (m, tr) => `Frontend fix: ${tr(m[1])} (${m[2]})`],
  [/^前端兼容修复：(.*)$/, (m, tr) => `Frontend compatibility fix: ${tr(m[1])}`],
  [/^\$readmem 路径 ([^\s（]+)（(.*?)）→ ([^\s（]+)（(.*?)）$/, (m, tr) => `$readmem path ${m[1]} (${m[2]}) → ${m[3]} (${tr(m[4])})`],
  [/^清单把 (\S+) 文件列成了源文件，它不是可单独编译的 Verilog，不编译$/,
    (m) => `The manifest lists a ${m[1]} file as a source; it is not standalone Verilog, so it is not compiled`],
  [/^缺少数据文件：(.*) 里的 \$readmem 读取 (.*)，(.*)；请补传这个文件$/,
    (m, tr) => `Missing data file: $readmem in ${m[1]} reads ${m[2]}; ${tr(m[3])}. Please upload this file`],
  [/^综合失败：(.*?)：缺少数据文件 (.*)$/, (m, tr) => `Synthesis failed: ${tr(m[1])}: missing data file ${m[2].split("、").join(", ")}`],
  [/^综合失败：([\s\S]*)$/, (m, tr) => `Synthesis failed: ${tr(m[1])}`],
  [/^Slang 转换失败（rc=(.*?)），见 agent_logs\/frontend\.log$/, (m) => `Slang conversion failed (rc=${m[1]}); see agent_logs/frontend.log`],
  [/^([^\s：]+:\d+)：([\s\S]*)$/, (m) => `${m[1]}: ${m[2]}`],   // yosys "<file>:<line>：<error>"
  [/^(\d+) 个单元，die (\S+) µm（(.*?)），时钟 (\S+) ns（(.*?)）$/,
    (m, tr) => `${pl(m[1], "cell")}, die ${m[2]} µm (${lower(tr(m[3]))}), clock ${m[4]} ns (${lower(tr(m[5]))})`],
  [/^固定 die (\S+) µm 小于规则算出的 (\S+) µm，可能放不下或拥塞$/,
    (m) => `Fixed die ${m[1]} µm is below the rule-derived ${m[2]} µm; the design may not fit or may congest`],
  // flow / signoff / repair
  [/^引脚放不下（PPL-0024：(\d+) 个引脚，只有 (\d+) 个位置），die (\S+) → (\S+) µm$/,
    (m) => `Pins do not fit (PPL-0024: ${m[1]} pins, only ${m[2]} slots), die ${m[3]} → ${m[4]} µm`],
  [/^自动修复：(.*)，从 floorplan 重跑$/, (m, tr) => `Auto-repair: ${tr(m[1])}; rerunning from floorplan`],
  [/^布局布线失败（rc=(.*?)）：([\s\S]*)$/, (m, tr) => `Place and route failed (rc=${m[1]}): ${tr(m[2])}`],
  [/^DRC ([^\s（]+)（(\S+) 处），LVS (\S+)$/, (m) => `DRC ${m[1]} (${m[2]} violations), LVS ${m[3]}`],
  [/^修复后仍未双清：DRC (\S+)，LVS (\S+)$/, (m) => `Still not DRC/LVS clean after repair: DRC ${m[1]}, LVS ${m[2]}`],
  // dataset
  [/^转图失败（rc=(.*?)，status=(.*?)）$/, (m) => `Graph conversion failed (rc=${m[1]}, status=${m[2]})`],
  [/^图数据集已生成（(.*)）$/, (m) => `Graph dataset built (${m[1]})`],
  [/^数据集校验未通过：(\d+)\/(\d+)，未通过项：(.*)$/, (m) => `Dataset verification failed: ${m[1]}/${m[2]}; failed checks: ${semi(m[3])}`],
  [/^数据集校验通过：(\d+)\/(\d+) 项$/, (m) => `Dataset verification passed: ${m[1]}/${m[2]} checks`],
  [/^图像数据集未通过（status=(.*?)）(.*?)(?:，未通过项：(.*))?$/,
    (m, tr) => `Image dataset failed (status=${m[1]})${m[2] ? " " + tr(m[2]) : ""}${m[3] ? "; failed checks: " + semi(m[3]) : ""}`],
  [/^图像数据集校验通过：(\d+)\/(\d+) 项（(\S+) 网格，(\d+) 个特征通道 \+ (\d+) 个标签通道）$/,
    (m) => `Image dataset verification passed: ${m[1]}/${m[2]} checks (${m[3]} grid, ${m[4]} feature + ${m[5]} label channels)`],
  [/^未能写入知识库（rc=(.*?)）：([\s\S]*)$/, (m) => `Could not write to the knowledge store (rc=${m[1]}): ${m[2]}`],
  [/^已写入知识库，但重新学习修复经验失败：([\s\S]*)$/, (m) => `Written to the knowledge store, but relearning repair recipes failed: ${m[1]}`],
  // batch scan / params / deletion errors
  [/^无法确定顶层模块：([\s\S]*)$/, (m, tr) => `Cannot determine the top module: ${tr(m[1])}`],
  [/^不是一个目录：(.*)$/, (m) => `Not a directory: ${m[1]}`],
  [/^一次最多 (\d+) 个设计，这个目录有 (\d+) 个$/, (m) => `At most ${m[1]} designs per job; this directory has ${m[2]}`],
  [/^design\.json 列出的源文件不存在：(.*)$/, (m) => `Source files listed in design.json do not exist: ${m[1]}`],
  [/^(.+) 冻结基线：按平台采用已验证的尺寸、时钟和约束参数$/,
    (m) => `${m[1]} frozen baseline: the platform's validated sizing, clock and constraint parameters`],
  [/^未知参数：(.*)$/, (m) => `Unknown parameter: ${m[1]}`],
  [/^(.+) 不是数字：(.*)$/, (m, tr) => `${tr(m[1])} is not a number: ${m[2]}`],
  [/^(.+) 超出范围 (\S+)–(\S+)：(\S+)$/, (m, tr) => `${tr(m[1])} is out of range ${m[2]}–${m[3]}: ${m[4]}`],
  [/^任务 (\S+) 仍引用这些产物，请先删除该关联任务$/, (m) => `Job ${m[1]} still references these outputs; delete that job first`],
  [/^关联任务删除失败：(.*?)；([\s\S]*)$/, (m, tr) => `Deleting linked job failed: ${m[1]}; ${tr(m[2])}`],
  [/^清理后仍存在任务产物，未标记删除完成：(.*)$/, (m) => `Job outputs remain after cleanup; deletion not marked complete: ${m[1]}`],
  // stage-view captions with a run name in them
  [/^(.*) · 沿用 ([^\s（]+)（血缘已核验）$/, (m, tr) => `${tr(m[1])} · reused from ${m[2]} (lineage verified)`],
];
