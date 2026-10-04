# 实验四 v3 执行手册

## 状态边界

新 campaign 初始状态为 `prepared_not_started`。完成公开 canary 后进入
`ready_for_converter_development`。此时只物化 1 个 canary 和 6 个 development case；24 个
hidden case 仍未物化。旧 v1/v1.1/v2 只作为测量研发证据，不与 v3 成绩合并。

固定变量：

```bash
PY=/home/yangao/.conda/envs/gnn_env/bin/python
ROOT=/home/yangao/r2g_exp4_confirmatory_v3_20260909
REPO=/home/yangao/r2g-skills
RUN=$ROOT/runtime/experiments/run_experiment4_graph_conversion.py
DEV=$ROOT/runtime/experiments/run_experiment4_llm_converter.py
PIPE=$ROOT/runtime/experiments/run_experiment4_formal_pipeline.py
```

## 1. 公开输入和 canary

仅物化 canary 与 development，随后运行并评分 R2G canary。禁止使用 `all` 或
`hidden_test`。再用实验四专用 API key 对 GPT、Claude、Qwen 各做一次 delimiter transport
canary；它只检查路由和响应封装，不生成正式 converter。

## 2. 开发前硬门

运行 `audit_experiment4_prelaunch.py --phase converter_development
--require-confirmatory-cohort --semantic-validation-root ...`。状态必须为
`ready_for_converter_development`。它检查：

- 31 项 cohort 和未见库存的哈希、仓库隔离及 465 对结构相似度；
- hidden 尚未物化；
- R2G canary、三条模型路由、API 环境变量和工具链；
- 已验证 R2G 实现与 24/24 独立语义证据的源码哈希一致；
- runtime/protocol 逐文件校验和完整。

## 3. 三模型开发与冻结

三个模型可并行，各模型内部串行。每模型最多 6 轮、6 次调用、400,000 Token；每轮在同一
6 个 development case 上从干净输出执行。达到 `6/6` strict pass 即提前停止，否则按协议
字典序选最佳轮。最终必须生成三个 `frozen_converter.py` 及其 manifest。

中断恢复只能复用已经落盘且 usage/response/attestation 完整的调用。格式错误同样消耗一次调用，
不得通过重启免费重抽。

正式调用固定使用 `--retries 0`。HTTP 请求结果未知时停止该模型并人工核对 provider 记录，不能在
同一轮内部自动重试。三个模型使用独立 token ledger、输出目录和实验四专用 API key。

## 4. 隐藏集硬门与执行

`run_experiment4_formal_pipeline.py` 在三个 converter 冻结后自动运行第二次审计，要求状态为
`ready_for_hidden_evaluation` 且 cohort 哈希一致，然后才允许展开 hidden。之后四种冻结方法在
相同 24 个 case 上执行；hidden 阶段 LLM 调用数固定为 0。

初始单 worker。只有空闲磁盘不少于 30 GiB 且实时 I/O wait 低于 15% 时才使用 2 worker。
每 case 上限 3600 秒。环境故障可用同一冻结程序重跑；确定性错误与超时计失败。

流水线会在四种方法的原生 evaluator 完成后自动运行独立 Yosys/OpenDB/DEF/SPEF/STA 语义审计，
并生成 `reports/semantic_audit/{summary,aggregate,report.md}` 与
`reports/experiment4_final_results.json`。

## 5. 语义评分与报告

原生 evaluator 后对四种方法运行独立 semantic audit。最终报告至少包含：

- `semantic usable / 24` 主指标；
- 原生 `strict usable / 24`；
- 七个核心语义组和分层结果；
- identity/topology precision、recall、F1；
- 数值及 label 的实际 oracle 覆盖量、缺失量与错误量；
- 转换时间、峰值 RSS、输出体积和 LLM 开发资源。

没有 oracle 的字段保持 `NOT_VERIFIED`，不得填成 PASS。任何 oracle 错误单独标为基础设施问题，
不能算方法失败，也不能算方法通过。

## 6. 与实验三的关系

实验四主实验不读取实验三产物。实验三可在附录中提供修复前后图一致性案例，但不进入实验四主分。
