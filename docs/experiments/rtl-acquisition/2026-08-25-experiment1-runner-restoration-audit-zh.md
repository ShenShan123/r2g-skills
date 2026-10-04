# 实验一 Runner 恢复与审核记录

日期：2026-08-25  
结论：Runner 已恢复，正式方法已收紧为 6 个 Vanilla LLM 与 R2G-Expander Cold，实验一规则已于 `2026-08-25T22:17:07-07:00` 冻结。端到端预算 canary 和当前路由对应的六模型完整预检仍须在正式 Campaign 初始化前完成。

## 已恢复并校正

- 正式方法固定为 6 个 Vanilla LLM 与 R2G-Expander Cold，共 7 组；Warm 不进入实验一。
- 每个方法由一个顶层 Runner 自动执行 `4批 × 25个`，主成绩固定按 100 个名额计算。
- `acquire` 与 `evaluate` 分离；同一方法四批全部 digest-lock 前，评分器拒绝输出任何正式结果，避免根据前批得分替换后批候选。
- Vanilla 四批使用隔离会话。R2G Cold 只在方法开始时初始化一次空调度状态；R2G acquisition frontier 在四个检查点间继续扩展，但不能读取 evaluator 结果。
- Campaign manifest 绑定正式设计文档、task spec、submission schema、Runner/评分器代码、六模型 API 预检报告、Agent commit、知识库和工具链。
- 公共预检与最终 evaluator 复用同一套 RTL 闭包、许可证、Sky130HD synth-only、规模和网表 Gate；最终 evaluator 仍从干净 clone 独立复验。
- Technical Qualification 与 Publishable Qualification 分开统计。许可证失败不会伪装成 RTL 技术失败，最终论文成功率以 Publishable 为准。
- 跨批 `(repo, commit, top)` 重复会被拒绝；最终汇总再用独立综合得到的规范化 mapped-netlist digest 排除明显的同功能重复提交。
- R2G 搜索预算改按真实 query attempts 计数，而不是按 query 定义数计数；翻页和重试都会消耗预算。
- 单次 synth timeout 取 `min(1小时, 当前批剩余时间)`，避免最后一个候选让方法越过六小时预算。
- Anthropic Messages 的工具定义、消息历史、tool-use 和 tool-result 已接入共同 Vanilla Runner。

## 验证结果

- 协议、task spec 和 JSON schema lint：通过。
- Experiment 1 Runner 单元测试：`68 passed`。
- 覆盖内容包括：七组方法、四批先锁后评、固定分母、跨批去重、许可证与编译闭包、规模门、Cold 空状态、模型预检 digest、网关适配、搜索请求计数和顶层 Runner 不提前调用 evaluator。
- 六个模型均已完成真实 structured-tool API 预检；尚未运行正式网络搜索和长时间 ORFS acquisition campaign。

## 正式冻结前还需完成

1. 在最终冻结 route 配置下重新运行 6 模型 structured-tool API preflight，并把报告放入正式 Campaign 路径后绑定 digest。
2. 从干净 Git commit 初始化 Campaign。`--allow-dirty-canary` 只能用于诊断，不能产出论文成绩。
3. 先运行每种方法 1 个小型不计分 canary，验证 API、许可证、clone、synth、停止原因和 submission schema。
4. 专门验证 R2G 在第 120 次搜索请求处能否及时停止。当前 Runner 会按真实 attempts 检测并拒绝超预算结果，但仍需用真实 Expander 运行确认底层不会先发出第 121 次请求。

## 正式入口

完成上述冻结后，先初始化一次 Campaign，再分别执行每个方法：

```bash
python3 experiments/run_experiment1_method_campaign.py acquire \
  --campaign-root CAMPAIGN_ROOT \
  --method-id METHOD_ID \
  --env-file ~/.config/r2g/experiment1_api.env \
  --cores 4

python3 experiments/run_experiment1_method_campaign.py evaluate \
  --campaign-root CAMPAIGN_ROOT \
  --method-id METHOD_ID \
  --cores 4
```

R2G Cold 的 `acquire` 命令不需要 `--env-file`。必须先完成一个方法的四批 acquisition，再允许该方法进入 evaluation。
