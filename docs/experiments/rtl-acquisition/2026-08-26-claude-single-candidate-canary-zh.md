# Claude 单候选 Canary

## 目的

本轮是不计分诊断实验，用于判断 Claude Opus 4.8 能否作为第七个 Vanilla LLM 加入实验一。验证范围覆盖真实 API 工具调用、搜索、clone、候选检查、Sky130HD 预检综合、提交，以及独立 evaluator 的重新 clone 与综合。

## 冻结配置

- Agent commit：`39e4c8810bcdf179d436bf2e8648d48142e98f08`
- 方法：`claude-vanilla`
- 路由：OpenRouter `anthropic/claude-opus-4.8`
- 凭据变量：`CLAUDE_OPENROUTER_KEY`
- 平台：Sky130HD
- 目标：1 个候选
- 运行性质：non-scoring canary

七条正式 LLM 路由均通过真实 API 和结构化工具调用预检。

## 结果

| 检查项 | 结果 |
|---|---|
| API 与强制工具调用 | 通过 |
| 搜索与 clone | 通过 |
| 模型提交 | 1/1 |
| 独立重新 clone | 通过 |
| 许可证与编译闭包 | 通过 |
| 独立 Sky130HD 综合 | 通过 |
| 最终结论 | 完整 canary 通过，可采用 7+1 |

Claude 在第一个 AES 候选综合失败后继续搜索，并在第 22 轮提交 `velmurugan-vlsi/uart-verilog` 的 `uart_top`。来源固定为 commit `c3e2f1ad71aa8a61befb75f824e3586a51de893a`。模型侧预检和独立 evaluator 均得到 286 个 mapped cells。独立复验确认 MIT 许可证、四个 RTL 文件的完整闭包、功能输入输出和来源绑定均有效，`publishable_qualified=true`。

运行期间，公共沙盒拒绝了三次路径或命令不合法的只读调用，Claude 随后自行修正；另有一次不存在文件读取被拒绝。没有人工介入，也没有为 Claude 放宽工具或候选资格门槛。

## 成本观察

- 模型运行：458.726 秒
- 输入 tokens：190,985
- 输出 tokens：5,179
- 总 tokens：196,164
- 搜索请求：7
- clone：3
- 综合尝试：2
- 独立复验：9.840 秒

Claude 的单候选 token 消耗明显高于 Gemini canary 的 23,032 tokens。该差异不能通过增加 Claude 预算来消除；正式实验应继续对所有 Vanilla LLM 使用相同的 2,000,000-token 批次上限、相同墙钟时间和相同缺位惩罚。若 Claude 在正式批次中提前耗尽额度，未提交名额应按冻结规则计为失败，这正是效率比较的一部分。

## 采用判断

建议正式实验采用 **7 个 Vanilla LLM + 1 个 R2G-Expander Cold**。Claude 补充了有代表性的强闭源模型，而且完整 canary 已通过；目前没有科学理由为了减少组数而删除一个已通过 canary 的既有模型。正式论文应同时报告候选完成率、独立合格率、token、时间和工具调用成本，避免只比较最终成功数。

## 证据路径

- 七模型预检：`/home/yangao/r2g_exp1_api_preflight_2026_08_26_claude_seven_models.json`
- Campaign：`/home/yangao/r2g_exp1_claude_single_canary_2026_08_26_run01`
- 模型结果：`method_runs/claude-vanilla.batch1.smoke/method_result.json`
- 模型提交：`method_runs/claude-vanilla.batch1.smoke/submission.json`
- 独立复验：`diagnostic_evaluation/claude-vanilla/diagnostic_report.json`
