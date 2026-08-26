# GPT Responses 与 Grok 单候选 Canary

## 目的

本轮为不计分诊断实验，用于确认新接入的 GPT Responses runner 和 Grok 路由是否能完成完整链路，而不只是通过 API 预检。完整链路包括搜索、clone、仓库检查、候选预检、提交，以及独立 evaluator 的重新 clone 和 Sky130HD 综合。

## 结果

| 方法 | 提交 | 模型侧综合 | 独立复验 | 结论 |
|---|---:|---:|---:|---|
| GPT-5.5 Responses | 1/1 | 通过 | 通过 | 完整 canary 通过 |
| Grok 4.6 | 0/1 | 未进入 | 不适用 | 完整 canary 未通过 |
| R2G Cold 25 候选旁路 canary | 25/25 | 通过 | 25/25 通过 | 完整批次链路通过 |

GPT 在 6 个模型轮次内找到并提交固定 commit 的 `secworks/aes`。独立 evaluator 重新 clone 后确认 BSD-2-Clause 许可证、编译闭包和来源摘要完整，并在 Sky130HD 映射出 21,708 个 cells；独立综合耗时约 106 秒，`publishable_qualified=true`。

R2G Cold 的独立批次复验覆盖 25 个候选和 16 个唯一仓库，25/25 均通过。规模分布为 100-999 cells 7 个、1,000-9,999 cells 12 个、10,000-99,999 cells 6 个；最大单仓库占比为 12%。该结果仅用于启动前诊断，不计入论文正式成绩。

Grok 的 API、搜索、clone 和文件读取均正常，但在统一 prompt 明确要求原样复制 clone 返回的 `repo_url` 和 commit 后，仍持续产生 `pinned_source_identity_mismatch(repo_url)`。最终耗时约 295 秒、消耗 238,069 tokens，30 轮内提交 0 个候选并且没有触发综合。因此这不是 API 或 ORFS 故障，而是 Grok 在当前冻结结构化工具协议下未能维护候选来源身份。

## Canary 发现并修正的问题

1. `http.client.IncompleteRead` 原先没有被识别为可重试的中转传输错误。现已统一纳入有界重试，提交为 `8fcbdaf`。
2. 在线预检原先逐字比较仓库 URL，而独立 evaluator 会规范化大小写和 `.git` 后缀。两者现已对齐，同时保留严格来源绑定，提交为 `4da4283`。
3. 预检日志原先只记录工具调用成功，不记录公开 gate 的失败类别。现已记录非敏感的预检摘要，提交为 `8895427`。
4. 统一 prompt 现已明确 `repo_id` 是不透明句柄，候选必须复制同一次 clone 返回的 URL 和 commit，提交为 `f1af578`。

以上修改适用于所有 Vanilla LLM，不改变候选资格、独立 evaluator、资源预算或评分规则，也没有为 Grok 注入候选答案。

## 启动判断

GPT Responses、R2G Cold、工具链和独立 evaluator 已具备正式运行条件。Grok 路由只能确认“接口可用”，不能确认“方法能完成一份有效候选”。正式 6+1 实验不应默默忽略这一点：可以保留 Grok 并接受其可能得到极低完成率，也可以在冻结前换成能通过单候选 canary 的模型；不应继续放宽 provenance gate 来让它通过。

## 证据路径

- GPT campaign：`/home/yangao/r2g_exp1_gpt_grok_single_canary_2026_08_26`
- GPT 独立复验：`/home/yangao/r2g_exp1_gpt_grok_single_canary_2026_08_26/diagnostic_evaluation/openai-vanilla/diagnostic_report.json`
- Grok 最终 campaign：`/home/yangao/r2g_exp1_grok_single_canary_2026_08_26_v5`
- R2G 25 候选复验：`/home/yangao/r2g_exp1_r2g25_nonscoring_2026_08_26_run03_correct_toolchain/reports/r2g25_diagnostic_evaluation.json`
