# Gemini 单候选 Canary

## 目的

本轮是不计分诊断实验，用于验证以 Gemini 3.7 Flash 替换 Grok 后，新的第六条 Vanilla LLM 路由能否完成实验一的整条链路，而不只是通过 API 探测。

## 冻结配置

- Agent commit：`a6b8d37e05955058e250eb831cc02503df2d7eb3`
- 方法：`gemini-vanilla`
- 路由：OpenRouter `google/gemini-3.7-flash`
- 凭据变量：`GEMINI_OPENROUTER_KEY`
- 平台：Sky130HD
- 目标：1 个候选
- 运行性质：non-scoring canary

六条正式模型路由均通过真实 API 和工具调用预检，Gemini 路由状态为 `ready`。

## 结果

| 检查项 | 结果 |
|---|---|
| 搜索与 clone | 通过 |
| 候选预检 | 通过 |
| 模型提交 | 1/1 |
| 独立重新 clone | 通过 |
| 许可证与编译闭包 | 通过 |
| 独立 Sky130HD 综合 | 通过 |
| 最终结论 | 完整 canary 通过 |

Gemini 在 8 个模型轮次内提交 `secworks/aes`，固定来源为 commit `80dc4718e1dcbbdb4b0dd1bdb393d8f7b98981dc`、top module `aes`。模型侧预检与独立 evaluator 均得到 21,708 个 mapped cells。独立复验确认 BSD-2-Clause 许可证、七个 RTL 文件的完整闭包、功能输入输出和来源摘要均有效，`publishable_qualified=true`。

本次模型运行耗时 154.025 秒，使用 22,091 input tokens、822 output tokens 和 119 reasoning tokens，总计 23,032 tokens；没有格式修复或人工干预。独立复验耗时 109.663 秒。

## 判断

Gemini 3.7 Flash 已证明能够遵守当前冻结的搜索、来源绑定、候选校验和提交协议，完整性明显优于此前未能提交有效候选的 Grok 4.6 canary。它可以作为第六个 Vanilla LLM 进入正式实验，但本次 1/1 结果只证明链路可用，不代表正式 100 候选实验的质量或完成率。

## 证据路径

- 六模型预检：`/home/yangao/r2g_exp1_api_preflight_2026_08_26_gemini_six_models.json`
- Campaign：`/home/yangao/r2g_exp1_gemini_single_canary_2026_08_26_run01`
- 模型结果：`method_runs/gemini-vanilla.batch1.smoke/method_result.json`
- 模型提交：`method_runs/gemini-vanilla.batch1.smoke/submission.json`
- 独立复验：`diagnostic_evaluation/gemini-vanilla/diagnostic_report.json`
